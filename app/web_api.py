"""Live workspace API. Browser sessions never share property or chat records."""
from __future__ import annotations

from collections import defaultdict, deque
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.routing import Route
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.data_access import get_snowflake_config
from app.observability import capture_exception, init_sentry
from app.underwriting import Scenario, calculate_scenario
from app.workspace_tokens import (browser_signing_key, issue_browser_session,
                                  issue_token, verify_browser_session, verify_token)


class ResearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)

    @field_validator("query")
    @classmethod
    def trim(cls, value):
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Enter a research query.")
        return value


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    lead_id: str | None = Field(default=None, max_length=128)
    conversation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    scenario: Scenario | None = None

    @field_validator("message")
    @classmethod
    def trim(cls, value):
        if not value.strip():
            raise ValueError("Enter a message.")
        return value.strip()


class UsageGate:
    """Bound paid calls and concurrency, including when cookies are discarded."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events = defaultdict(deque)
        self.active = defaultdict(set)

    def reserve(self, owner, ip, kind="request"):
        now = time.monotonic()
        with self.lock:
            if (kind in self.active.get(owner, set())
                    or sum(len(kinds) for kinds in self.active.values()) >= 4):
                raise HTTPException(429, "Research is busy. Please try again shortly.")
            limits = [("global", 86400, int(os.getenv("RETAILSCOPE_DAILY_CALL_LIMIT", "120"))),
                      ("ip:" + ip, 3600, 20), ("owner:" + owner, 3600, 12)]
            for key, window, limit in limits:
                queue = self.events[key]
                while queue and queue[0] <= now - window:
                    queue.popleft()
                if len(queue) >= limit:
                    raise HTTPException(429, "Research allowance reached. Please try again later.")
            for key, _, _ in limits:
                self.events[key].append(now)
            self.active[owner].add(kind)
            if len(self.events) > 1000:
                self.events = defaultdict(deque, {key: queue for key, queue in self.events.items()
                                                   if queue and queue[-1] > now - 86400})

    def release(self, owner, kind="request"):
        with self.lock:
            self.active.get(owner, set()).discard(kind)
            if not self.active.get(owner):
                self.active.pop(owner, None)

    def is_active(self, owner, kind="request"):
        with self.lock:
            return kind in self.active.get(owner, set())


def create_app(*, store=None, research_fn=None, analyze_fn=None):
    if os.getenv("RENDER") == "true" and os.getenv("RETAILSCOPE_STORAGE") != "snowflake":
        raise RuntimeError("Hosted live workspaces require durable Snowflake storage.")
    if store is None:
        from app.workspace_store import get_store
        store = get_store()
    if research_fn is None:
        from app.live_discovery import research
        research_fn = research
    if analyze_fn is None:
        from app.live_agent import analyze
        analyze_fn = analyze
    executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="retailscope-research")
    gate = UsageGate()
    session_key = browser_signing_key()

    @asynccontextmanager
    async def lifespan(app):
        init_sentry()
        if os.getenv("RENDER") == "true":
            await asyncio.to_thread(store.list_leads, "__readiness__")
        async with mcp.session_manager.run():
            yield
        executor.shutdown(wait=True, cancel_futures=False)

    app = FastAPI(title="RetailScope", lifespan=lifespan, docs_url=None, redoc_url=None)
    hosts = ["localhost", "127.0.0.1", "testserver", "retailscope-ai-demo.onrender.com"]
    if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
        hosts.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.middleware("http")
    async def session_boundary(request, call_next):
        is_mcp = request.url.path == "/mcp"
        if not request.url.path.startswith("/api/") and not is_mcp:
            return await call_next(request)
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin request rejected."}, status_code=403)
        if is_mcp:
            authorization = request.headers.get("authorization", "")
            try:
                if not authorization.startswith("Bearer "):
                    raise ValueError()
                request.state.owner = verify_token(authorization[7:])
            except (ValueError, RuntimeError):
                return JSONResponse({"detail": "A valid workspace token is required."},
                                    status_code=401, headers={"WWW-Authenticate": "Bearer"})
        if request.method == "POST":
            if "application/json" not in request.headers.get("content-type", ""):
                return JSONResponse({"detail": "JSON required."}, status_code=415)
            body = await request.body()
            if len(body) > 16384:
                return JSONResponse({"detail": "Request too large."}, status_code=413)
        owner = verify_browser_session(request.cookies.get("retailscope_session", ""), session_key)
        fresh = not is_mcp and owner is None
        if not is_mcp:
            request.state.owner = secrets.token_hex(32) if fresh else owner
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if fresh:
            response.set_cookie("retailscope_session", issue_browser_session(request.state.owner, session_key),
                                secure=os.getenv("RENDER") == "true", httponly=True,
                                samesite="strict", max_age=60 * 60 * 24 * 30)
        return response

    @app.get("/healthz")
    def health():
        return {"status": "ok", "service": "retailscope-workspace",
                "release": os.getenv("RENDER_GIT_COMMIT", os.getenv("GITHUB_SHA", "local"))}

    @app.get("/api/status")
    def status():
        cfg = get_snowflake_config()
        return {"providers": {"snowflake": bool(cfg.get("account")),
                              "nimble": bool(os.getenv("NIMBLE_API_KEY"))},
                "model": os.getenv("RETAILSCOPE_CORTEX_MODEL") or cfg.get("cortex_model") or "claude-sonnet-4-6",
                "storage": os.getenv("RETAILSCOPE_STORAGE", "sqlite"),
                "billing_provider": "Snowflake Cortex"}

    @app.get("/api/leads")
    def leads(request: Request):
        return {"leads": store.list_leads(request.state.owner)}

    @app.post("/api/mcp-token")
    def mcp_token(request: Request):
        try:
            return issue_token(request.state.owner)
        except RuntimeError:
            raise HTTPException(503, "Workspace MCP access is not configured.") from None

    @app.post("/api/scenarios")
    def scenario(payload: Scenario):
        return calculate_scenario(payload)

    def run_research(owner, job_id, query):
        deadline = time.monotonic() + 300
        try:
            store.update_job(owner, job_id, "running", expected_status="queued")
            found = research_fn(query)
            if time.monotonic() >= deadline:
                raise TimeoutError("Research exceeded its execution deadline.")
            if found:
                report = analyze_fn("Analyze these leads for this acquisition search: " + query,
                                    found, history=None)
                if time.monotonic() >= deadline:
                    raise TimeoutError("Research exceeded its execution deadline.")
                current = store.get_job(owner, job_id)
                if current is None or current["status"] != "running":
                    return
                store.upsert_leads(owner, found)
                store.update_job(owner, job_id, "completed", leads=found, analysis=report,
                                 expected_status="running")
            else:
                store.update_job(owner, job_id, "completed", leads=[], expected_status="running")
        except Exception as exc:
            capture_exception(exc)
            store.update_job(owner, job_id, "failed", expected_status="running",
                             error="Live research could not finish. Please retry or check provider access.")
        finally:
            gate.release(owner, "research")

    @app.post("/api/research", status_code=202)
    def start_research(payload: ResearchRequest, request: Request):
        owner = request.state.owner
        gate.reserve(owner, request.client.host if request.client else "unknown", "research")
        try:
            job = store.create_job(owner, payload.query)
            executor.submit(run_research, owner, job["id"], payload.query)
            return job
        except Exception:
            gate.release(owner, "research")
            raise

    @app.get("/api/research/{job_id}")
    def research_status(job_id: str, request: Request):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise HTTPException(404, "Research run not found.")
        job = store.get_job(request.state.owner, job_id)
        if job is None:
            raise HTTPException(404, "Research run not found.")
        if job["status"] in {"queued", "running"}:
            started = datetime.fromisoformat(job["created_at"])
            if ((datetime.now(timezone.utc) - started).total_seconds() > 600
                    and not gate.is_active(request.state.owner, "research")):
                store.update_job(request.state.owner, job_id, "failed",
                                 error="Research was interrupted or exceeded its deadline. Submit a new run to retry.",
                                 expected_status=job["status"])
                job = store.get_job(request.state.owner, job_id)
        return job

    @app.get("/api/conversations")
    def conversations(request: Request):
        return {"conversations": store.list_conversations(request.state.owner)}

    @app.get("/api/conversations/{conversation_id}")
    def conversation_messages(conversation_id: str, request: Request):
        if not re.fullmatch(r"[a-f0-9]{32}", conversation_id):
            raise HTTPException(404, "Conversation not found.")
        messages = store.messages(request.state.owner, conversation_id)
        if not messages:
            raise HTTPException(404, "Conversation not found.")
        return {"id": conversation_id, "messages": messages}

    @app.post("/api/chat")
    def chat(payload: ChatRequest, request: Request):
        owner = request.state.owner
        selected = store.list_leads(owner)
        if payload.lead_id:
            selected = [lead for lead in selected if lead["id"] == payload.lead_id]
            if not selected:
                raise HTTPException(404, "Lead not found in this workspace.")
        gate.reserve(owner, request.client.host if request.client else "unknown", "chat")
        conversation_id = payload.conversation_id or secrets.token_hex(16)
        try:
            history = store.messages(owner, conversation_id)[-12:]
            kwargs = {"history": history}
            if payload.scenario:
                kwargs["scenario"] = calculate_scenario(payload.scenario)
            result = analyze_fn(payload.message, selected[:10], **kwargs)
            store.append_message(owner, conversation_id, "user", payload.message)
            store.append_message(owner, conversation_id, "assistant", result["answer"])
            return {**result, "conversation_id": conversation_id}
        except Exception as exc:
            capture_exception(exc)
            raise HTTPException(502, "The analysis provider is unavailable. Your message was not answered; please retry.") from None
        finally:
            gate.release(owner, "chat")

    from app.workspace_mcp import create_workspace_mcp
    mcp = create_workspace_mcp(
        list_leads=leads, get_job=research_status,
        start_research=lambda query, req: start_research(ResearchRequest(query=query), req),
        chat=lambda body, req: chat(ChatRequest(**body), req),
        calculate=lambda assumptions: calculate_scenario(Scenario(**assumptions)))
    mcp_app = mcp.streamable_http_app()
    app.router.routes.append(Route("/mcp", mcp_app, methods=["GET", "POST", "DELETE"]))

    dist = Path(__file__).resolve().parents[1] / "web" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Endpoint not found.")
        if not (dist / "index.html").is_file():
            raise HTTPException(503, "Frontend build is not installed.")
        return FileResponse(dist / "index.html", headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                       "img-src 'self' data: https:; connect-src 'self'; "
                                       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        })

    return app
