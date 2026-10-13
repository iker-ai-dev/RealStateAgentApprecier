"""Live workspace API. Browser sessions never share property or chat records."""
from __future__ import annotations

from collections import defaultdict, deque
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
import inspect
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.routing import Route
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.data_access import get_snowflake_config
from app.live_agent import LiveAgentError
from app.search_agents import (SearchAgentRequest, AgentToggle, claim_agent, public_agent,
                              scheduler_key, stamp, utc_now, AgentDraftRequest, parse_agent_draft, draft_summary)
from app.observability import capture_exception, init_sentry
from app.underwriting import Scenario, calculate_scenario
from app.workspace_tokens import (browser_signing_key, issue_browser_session,
                                  issue_token, verify_browser_session, verify_token, SESSION_LIFETIME)


RESEARCH_STREAM_SECONDS = 300
RESEARCH_POLL_SECONDS = 2
RESEARCH_HEARTBEAT_SECONDS = 10
RESEARCH_STREAM_LIMIT = 32
RESEARCH_OWNER_STREAM_LIMIT = 4


class _ResearchStream(StreamingResponse):
    """Release admission even if transport closes before iteration starts."""

    def __init__(self, content, release):
        super().__init__(content, media_type="text/event-stream",
                         headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        self.release = release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.release()


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

    def __init__(self, reserve_daily=None):
        self.lock = threading.Lock()
        self.events = defaultdict(deque)
        self.active = defaultdict(set)
        self.reserve_daily = reserve_daily

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
            if self.reserve_daily is not None:
                try:
                    permitted = self.reserve_daily(limits[0][2])
                except Exception as exc:
                    capture_exception(exc)
                    raise HTTPException(503, "Usage accounting is unavailable. No paid research was started.") from None
                if not permitted:
                    raise HTTPException(429, "The daily research allowance has been reached.")
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
    needs_nimble_key = research_fn is None
    if research_fn is None:
        from app.live_discovery import research
        research_fn = research
    if analyze_fn is None:
        from app.live_agent import analyze
        analyze_fn = analyze
    executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="retailscope-research")
    gate = UsageGate(store.reserve_daily_usage)
    session_key = browser_signing_key()
    agent_config_lock = threading.Lock()
    stream_lock = threading.Lock()
    streams = defaultdict(int)

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
            request.state.session_expires_at = (int(time.time()) + SESSION_LIFETIME if fresh else
                int(request.cookies["retailscope_session"].split('.')[2]))
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
        from app.lead_facts import enrich_leads
        owner = request.state.owner
        receipts = {row["id"]: row["read_at"] for row in store.list_read_receipts(owner)}
        return {"leads": [dict(lead, read_at=receipts.get(lead["id"]))
                          for lead in enrich_leads(store.list_leads(owner))]}

    @app.post("/api/leads/{lead_id}/read")
    def read_lead(lead_id: str, request: Request):
        owner = request.state.owner
        if not any(lead["id"] == lead_id for lead in store.list_leads(owner)):
            raise HTTPException(404, "Lead not found.")
        store.mark_lead_read(owner, lead_id)
        return {"ok": True}

    @app.post("/api/mcp-token")
    def mcp_token(request: Request):
        try:
            return issue_token(request.state.owner)
        except RuntimeError:
            raise HTTPException(503, "Workspace MCP access is not configured.") from None

    @app.post("/api/scenarios")
    def scenario(payload: Scenario):
        return calculate_scenario(payload)

    def run_research(owner, job_id, query, agent_id=None):
        deadline = time.monotonic() + 300
        try:
            if not store.update_job(owner, job_id, "running", expected_status="queued",
                                    stage="searching", started_at=datetime.now(timezone.utc).isoformat()):
                return
            found = research_fn(query)
            if agent_id:
                found = [dict(lead, agent_id=agent_id, job_id=job_id) for lead in found]
            if time.monotonic() >= deadline:
                raise TimeoutError("Research exceeded its execution deadline.")
            if found:
                current = store.get_job(owner, job_id)
                if current is None or current["status"] != "running":
                    return
                # Retrieved evidence is useful even if inference fails. Persist
                # it independently; never substitute an unverified AI answer.
                store.upsert_leads(owner, found)
                store.update_job(owner, job_id, "running", leads=found, expected_status="running")
                try:
                    def on_progress(stage):
                        if stage in {"analyzing", "validating", "repairing"}:
                            store.update_job(owner, job_id, "running", stage=stage,
                                             expected_status="running")

                    kwargs = {"history": None}
                    # Explicit opt-in keeps legacy injected analyzers unchanged.
                    try:
                        parameters = inspect.signature(analyze_fn).parameters
                    except (TypeError, ValueError):
                        parameters = {}
                    if "on_progress" in parameters:
                        kwargs["on_progress"] = on_progress
                    if "mode" in parameters:
                        kwargs["mode"] = "research"
                    report = analyze_fn("Analyze these leads for this acquisition search: " + query,
                                        found, **kwargs)
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Research exceeded its execution deadline.")
                except Exception as exc:
                    capture_exception(exc)
                    store.update_job(owner, job_id, "partial", leads=found, stage="partial",
                                     error="Sources were saved, but the AI analysis could not be verified. "
                                           "You can inspect the sources or ask a follow-up question.",
                                     expected_status="running")
                    return
                current = store.get_job(owner, job_id)
                if current is None or current["status"] != "running":
                    return
                store.append_message(owner, job_id, "user", query)
                store.append_message(owner, job_id, "assistant", report["answer"],
                                     metadata={key: report[key] for key in ("sources", "model", "usage") if key in report})
                store.update_job(owner, job_id, "completed", leads=found, analysis=report, stage="completed",
                                 expected_status="running")
            else:
                store.update_job(owner, job_id, "completed", leads=[], stage="completed", expected_status="running")
        except Exception as exc:
            capture_exception(exc)
            error = "Live research could not finish. Please retry or check provider access."
            if isinstance(exc, LiveAgentError):
                # LiveAgentError contains only host-authored validation reasons.
                error = str(exc)
            store.update_job(owner, job_id, "failed", expected_status="running", stage="failed",
                             error=error)
        finally:
            gate.release(owner, "research")

    @app.post("/api/research", status_code=202)
    def start_research(payload: ResearchRequest, request: Request):
        if needs_nimble_key and not os.getenv("NIMBLE_API_KEY", "").strip():
            raise HTTPException(503, "Live discovery is unavailable: Nimble access is not configured.")
        owner = request.state.owner
        gate.reserve(owner, request.client.host if request.client else "unknown", "research")
        try:
            job = store.create_job(owner, payload.query)
            executor.submit(run_research, owner, job["id"], payload.query)
            return job
        except Exception:
            gate.release(owner, "research")
            raise

    @app.get("/api/agents")
    def list_agents(request: Request):
        owner = request.state.owner
        agents = store.list_agents(owner)
        now = stamp(utc_now())
        for agent in agents:
            if agent.get("expires_at", "") <= now and agent["enabled"]:
                store.patch_agent(owner, agent["id"], {"enabled": False})
                agent["enabled"] = False
            if (agent.get("last_status") == "running" and agent.get("lease_until")
                    and agent["lease_until"] <= now):
                changes = {"last_status": "failed", "lease_until": None,
                           "last_error": "Run was interrupted. The next scheduled search will still run."}
                if store.patch_agent(owner, agent["id"], changes, {"run_id": agent["run_id"],
                                      "last_status": "running", "lease_until": agent["lease_until"]}):
                    agent.update(changes)
        return {"agents": [public_agent(agent) for agent in agents]}

    @app.post("/api/agents/draft")
    def draft_agent(payload: AgentDraftRequest, request: Request):
        owner = request.state.owner
        conversation_id = payload.conversation_id or secrets.token_hex(16)
        if payload.conversation_id and not (store.messages(owner, conversation_id)
                                           or store.get_job(owner, conversation_id)):
            raise HTTPException(404, "Conversation not found.")
        result = parse_agent_draft(payload.message)
        store.append_message(owner, conversation_id, "user", payload.message)
        store.append_message(owner, conversation_id, "assistant", draft_summary(result))
        return {**result, "conversation_id": conversation_id}

    @app.post("/api/agents", status_code=201)
    def create_agent(payload: SearchAgentRequest, request: Request):
        owner = request.state.owner
        if payload.conversation_id and not store.messages(owner, payload.conversation_id):
            raise HTTPException(404, "Conversation not found.")
        with agent_config_lock:
            if sum(agent.get("expires_at", "") > stamp(utc_now()) for agent in store.list_agents(owner)) >= 5:
                raise HTTPException(409, "This workspace supports up to five background agents.")
            agent = payload.as_agent()
            expires_at = request.state.session_expires_at
            if expires_at <= time.time():
                raise HTTPException(401, "Workspace session expired. Reload before creating an agent.")
            agent["expires_at"] = stamp(datetime.fromtimestamp(expires_at, timezone.utc))
            if not store.enroll_agent(owner, agent):
                raise HTTPException(409, "Background agent enrollment is full. Please try again later.")
        if payload.conversation_id:
            try:
                store.append_message(owner, payload.conversation_id, "assistant",
                                     f"Background agent created. It will search every {agent['cadence_hours']} hours. "
                                     f"Next scheduled search: {agent['next_run_at']}. "
                                     "No research has been run yet.")
            except Exception as exc:
                # Enrollment already committed; do not encourage a duplicate retry.
                capture_exception(exc)
        return public_agent(agent)

    @app.post("/api/agents/{agent_id}")
    def toggle_agent(agent_id: str, payload: AgentToggle, request: Request):
        owner = request.state.owner
        agent = store.get_agent(owner, agent_id)
        if agent is None:
            raise HTTPException(404, "Background agent not found.")
        if payload.enabled and agent.get("expires_at", "") <= stamp(utc_now()):
            raise HTTPException(409, "This agent has expired. Create a new background search.")
        if not store.patch_agent(owner, agent_id, {"enabled": payload.enabled}):
            raise HTTPException(404, "Background agent not found.")
        return public_agent(store.get_agent(owner, agent_id))

    def finish_agent(owner, agent_id, run_id, job_id):
        job = store.get_job(owner, job_id)
        changes = {"lease_until": None, "last_status": job["status"],
                   "last_error": job.get("error"), "last_job_id": job_id}
        store.patch_agent(owner, agent_id, changes, {"run_id": run_id})

    def execute_agent(owner, agent, run_id, job):
        try:
            run_research(owner, job["id"], agent["query"], agent["id"])
        finally:
            finish_agent(owner, agent["id"], run_id, job["id"])

    def prepare_agent(owner, agent, ip, scheduled=False):
        if needs_nimble_key and not os.getenv("NIMBLE_API_KEY", "").strip():
            raise HTTPException(503, "Live discovery is unavailable: Nimble access is not configured.")
        run_id = claim_agent(store, owner, agent, scheduled=scheduled)
        if run_id is None:
            raise HTTPException(409, "This agent is already running or no longer due.")
        reserved = False
        try:
            gate.reserve(owner, ip, "research")
            reserved = True
            job = store.create_job(owner, agent["query"], agent_id=agent["id"])
            store.patch_agent(owner, agent["id"], {"last_job_id": job["id"]}, {"run_id": run_id})
            return run_id, job
        except Exception:
            if reserved:
                gate.release(owner, "research")
            store.patch_agent(owner, agent["id"], {"lease_until": None, "last_status": "failed",
                              "next_run_at": agent["next_run_at"],
                              "admission_after": stamp(utc_now() + timedelta(seconds=30)),
                              "last_error": "Run could not start. Check the research allowance or provider access."},
                              {"run_id": run_id})
            raise

    @app.post("/api/agents/{agent_id}/run", status_code=202)
    def run_agent(agent_id: str, request: Request):
        owner = request.state.owner
        agent = store.get_agent(owner, agent_id)
        if agent is None:
            raise HTTPException(404, "Background agent not found.")
        run_id, job = prepare_agent(owner, agent, request.client.host if request.client else "unknown")
        try:
            executor.submit(execute_agent, owner, agent, run_id, job)
        except Exception:
            gate.release(owner, "research")
            store.update_job(owner, job["id"], "failed", error="Run interrupted; please retry.")
            finish_agent(owner, agent["id"], run_id, job["id"])
            raise
        return job

    @app.post("/internal/schedules/tick")
    def scheduled_tick(request: Request):
        # No browser cookies or owner input: only the bounded internal dispatcher
        # can enumerate due subscriptions across workspaces.
        key = scheduler_key(session_key)
        provided = request.headers.get("authorization", "")
        if len(key) < 32 or not secrets.compare_digest(provided, "Bearer " + key):
            raise HTTPException(401, "Scheduler authorization required.")
        due = store.due_agents(stamp(utc_now()), limit=1)
        if not due:
            return {"ran": False}
        owner, agent = due[0]["owner"], due[0]["agent"]
        try:
            run_id, job = prepare_agent(owner, agent, "scheduler", scheduled=True)
        except HTTPException as exc:
            return {"ran": False, "deferred": True, "status_code": exc.status_code}
        execute_agent(owner, agent, run_id, job)
        result = store.get_job(owner, job["id"])
        return {"ran": True, "status": result["status"]}

    def research_snapshot(owner, job_id):
        from app.lead_facts import enrich_leads
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise HTTPException(404, "Research run not found.")
        job = store.get_job(owner, job_id)
        if job is None:
            raise HTTPException(404, "Research run not found.")
        if job["status"] in {"queued", "running"}:
            started = datetime.fromisoformat(job["created_at"])
            if ((datetime.now(timezone.utc) - started).total_seconds() > 600
                    and not gate.is_active(owner, "research")):
                store.update_job(owner, job_id, "failed", stage="failed",
                                 error="Research was interrupted or exceeded its deadline. Submit a new run to retry.",
                                 expected_status=job["status"])
                job = store.get_job(owner, job_id)
        return {**job, "leads": enrich_leads(job.get("leads", []))}

    def artifact_summary(job):
        summary = {"id": job["id"], "title": job["query"][:120],
                   "created_at": job["created_at"], "status": job["status"],
                   "query": job["query"], "lead_count": len(job.get("leads", [])),
                   "has_report": job.get("analysis") is not None}
        # Older scheduled runs stored provenance on their retrieved leads only.
        agent_ids = {lead.get("agent_id") for lead in job.get("leads", []) if lead.get("agent_id")}
        agent_id = job.get("agent_id") or (next(iter(agent_ids)) if len(agent_ids) == 1 else None)
        if agent_id:
            summary["agent_id"] = agent_id
        if job.get("error"):
            summary["error"] = job["error"]
        return summary

    @app.get("/api/artifacts")
    def list_artifacts(request: Request):
        return {"schema_version": 1, "artifacts": [artifact_summary(job)
                for job in store.list_jobs(request.state.owner)]}

    @app.get("/api/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str, request: Request):
        from app.lead_facts import enrich_leads
        if not re.fullmatch(r"[a-f0-9]{32}", artifact_id):
            raise HTTPException(404, "Artifact not found.")
        job = store.get_job(request.state.owner, artifact_id)
        if job is None or job.get("status") not in {"completed", "partial"}:
            raise HTTPException(404, "Artifact not found.")
        return {"schema_version": 1, **artifact_summary(job), "report": job.get("analysis"),
                "leads": enrich_leads(job.get("leads", []))}

    @app.get("/api/research/{job_id}")
    def research_status(job_id: str, request: Request):
        return research_snapshot(request.state.owner, job_id)

    @app.get("/api/research/{job_id}/events")
    async def research_events(job_id: str, request: Request):
        owner = request.state.owner
        with stream_lock:
            if (sum(streams.values()) >= RESEARCH_STREAM_LIMIT
                    or streams.get(owner, 0) >= RESEARCH_OWNER_STREAM_LIMIT):
                raise HTTPException(429, "Too many research streams. Use status polling.")
            streams[owner] += 1

        def release():
            with stream_lock:
                streams[owner] -= 1
                if not streams[owner]:
                    del streams[owner]

        deadline = time.monotonic() + RESEARCH_STREAM_SECONDS
        try:
            # Authenticate ownership before sending streaming response headers.
            initial = await asyncio.wait_for(
                asyncio.to_thread(research_snapshot, owner, job_id), RESEARCH_STREAM_SECONDS)
        except BaseException:
            release()
            raise

        async def events():
            snapshot = initial
            previous = None
            try:
                async with asyncio.timeout(max(0, deadline - time.monotonic())):
                    while True:
                        if await request.is_disconnected():
                            return
                        data = json.dumps(snapshot, ensure_ascii=True, separators=(",", ":"))
                        if data != previous:
                            yield "event: progress\ndata: " + data + "\n\n"
                            previous = data
                        else:
                            yield "event: heartbeat\ndata: {}\n\n"
                        if snapshot["status"] in {"completed", "partial", "failed"}:
                            return
                        await asyncio.sleep(RESEARCH_POLL_SECONDS)
                        pending = asyncio.create_task(asyncio.to_thread(research_snapshot, owner, job_id))
                        try:
                            # A slow durable read must not trip the client's idle timer.
                            while not pending.done():
                                done, _ = await asyncio.wait({pending}, timeout=RESEARCH_HEARTBEAT_SECONDS)
                                if not done:
                                    yield "event: heartbeat\ndata: {}\n\n"
                            snapshot = await pending
                        finally:
                            pending.cancel()
            except TimeoutError:
                return
            except Exception as exc:
                capture_exception(exc)
                # Closing the transport lets clients fall back to status polling.
                return

        return _ResearchStream(events(), release)

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
        selected = []
        research_run = None
        if payload.lead_id:
            selected = [lead for lead in store.list_leads(owner) if lead["id"] == payload.lead_id]
            if not selected:
                raise HTTPException(404, "Lead not found in this workspace.")
        elif payload.conversation_id:
            # Research reports use their job ID as the conversation ID. Keep
            # follow-ups on that evidence instead of unrelated workspace history.
            research_run = store.get_job(owner, payload.conversation_id)
            if research_run and research_run.get('status') in ('completed', 'partial'):
                selected = research_run.get('leads', [])
        gate.reserve(owner, request.client.host if request.client else "unknown", "chat")
        conversation_id = payload.conversation_id or secrets.token_hex(16)
        try:
            messages = store.messages(owner, conversation_id)
            if not payload.lead_id and research_run is None:
                # Reuse only this conversation's cited, owner-visible sources.
                # Ordinary conversation must not inherit workspace inventory.
                source_urls = {source['url'] for item in messages if item['role'] == 'assistant'
                               for source in item.get('sources', [])}
                if source_urls:
                    selected = [lead for lead in store.list_leads(owner) if lead['url'] in source_urls]
            history = [{"role": item["role"], "content": item["content"][:3000]}
                       for item in messages[-8:]]
            kwargs = {"history": history}
            # Legacy injected analyzers need not implement conversational mode.
            try:
                parameters = inspect.signature(analyze_fn).parameters
            except (TypeError, ValueError):
                parameters = {}
            if "mode" in parameters:
                kwargs["mode"] = "chat"
            if payload.scenario:
                kwargs["scenario"] = calculate_scenario(payload.scenario)
            result = analyze_fn(payload.message, selected[:10], **kwargs)
            store.append_message(owner, conversation_id, "user", payload.message)
            metadata = {key: result[key] for key in ("sources", "model", "usage") if key in result}
            store.append_message(owner, conversation_id, "assistant", result["answer"], metadata=metadata)
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
