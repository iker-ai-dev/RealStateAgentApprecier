import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.web_api import create_app
from app.workspace_store import WorkspaceStore
from app.workspace_tokens import browser_signing_key, verify_browser_session


def snapshots(text):
    return [json.loads(frame.split("data: ", 1)[1])
            for frame in text.split("\n\n") if frame.startswith("event: progress\n")]


class ResearchProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = WorkspaceStore(Path(self.temp.name) / "workspace.db")
        self.research = Mock(return_value=[])
        self.analyze = Mock(return_value={"answer": "Verified report", "sources": []})
        self.app = create_app(store=self.store, research_fn=self.research, analyze_fn=self.analyze)
        self.client = self.enterContext(TestClient(self.app))
        self.client.get("/api/status")
        self.owner = verify_browser_session(self.client.cookies.get("retailscope_session"),
                                            browser_signing_key())
        self.lead = {"id": "listing", "url": "https://example.com/listing", "title": "Retail"}

    def test_owner_isolation_terminal_shape_and_no_paid_work(self):
        for status in ("completed", "partial", "failed"):
            job = self.store.create_job(self.owner, "Retail search")
            self.store.update_job(self.owner, job["id"], status, leads=[self.lead], stage=status)
            url = "/api/research/" + job["id"]
            other = TestClient(self.app)
            self.assertEqual(other.get(url + "/events").status_code, 404)
            response = self.client.get(url + "/events")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            self.assertEqual(response.headers["x-accel-buffering"], "no")
            self.assertEqual(snapshots(response.text), [self.client.get(url).json()])
            self.assertEqual(snapshots(response.text)[0]["stage"], status)
        self.assertEqual(self.client.get("/api/research/invalid/events").status_code, 404)
        self.research.assert_not_called()
        self.analyze.assert_not_called()

    def test_intermediate_saved_leads_and_callback_then_final(self):
        for fails in (False, True):
            entered = threading.Event()
            finish = threading.Event()

            def analyze(message, leads, history=None, *, on_progress=None):
                on_progress("analyzing")
                entered.set()
                if not finish.wait(5):
                    raise RuntimeError("Test analysis timed out")
                on_progress("validating")
                if fails:
                    raise RuntimeError("private provider detail")
                return {"answer": "Verified report", "sources": []}

            app = create_app(store=self.store, research_fn=lambda query: [self.lead], analyze_fn=analyze)
            with TestClient(app) as client:
                try:
                    job = client.post("/api/research", json={"query": "Retail search"}).json()
                    self.assertTrue(entered.wait(5))
                    url = "/api/research/" + job["id"]
                    with patch("app.web_api.RESEARCH_STREAM_SECONDS", 0.05), patch(
                            "app.web_api.RESEARCH_POLL_SECONDS", 0.01):
                        response = client.get(url + "/events")
                    intermediate = snapshots(response.text)[0]
                    self.assertEqual(intermediate["status"], "running")
                    self.assertEqual(intermediate["stage"], "analyzing")
                    self.assertTrue(intermediate["started_at"])
                    self.assertEqual(intermediate["leads"][0]["id"], "listing")
                    self.assertNotIn("analysis", intermediate)
                    self.assertIn("event: heartbeat", response.text)
                    self.assertEqual(client.get("/api/leads").json()["leads"][0]["id"], "listing")
                finally:
                    finish.set()
                with patch("app.web_api.RESEARCH_POLL_SECONDS", 0.01):
                    final = snapshots(client.get(url + "/events").text)[-1]
                self.assertEqual(final["status"], "partial" if fails else "completed")
                self.assertEqual(final["stage"], final["status"])
                self.assertNotIn("private provider detail", json.dumps(final))

    def test_reopened_store_replays_durable_snapshot(self):
        job = self.store.create_job(self.owner, "Retail search")
        self.store.update_job(self.owner, job["id"], "partial", leads=[self.lead])
        reopened = WorkspaceStore(self.store.path)
        app = create_app(store=reopened, research_fn=self.research, analyze_fn=self.analyze)
        with TestClient(app) as client:
            client.cookies.update(self.client.cookies)
            result = client.get("/api/research/" + job["id"] + "/events")
        self.assertEqual(snapshots(result.text)[0]["status"], "partial")
        self.research.assert_not_called()

    def test_heartbeat_during_slow_durable_read(self):
        job = self.store.create_job(self.owner, "Retail search")
        original = self.store.get_job
        reads = 0

        def slow_read(owner, job_id):
            nonlocal reads
            reads += 1
            if reads > 1:
                time.sleep(0.06)
                self.store.update_job(owner, job_id, "completed", stage="completed")
            return original(owner, job_id)

        with patch.object(self.store, "get_job", side_effect=slow_read), patch(
                "app.web_api.RESEARCH_POLL_SECONDS", 0.01), patch(
                "app.web_api.RESEARCH_HEARTBEAT_SECONDS", 0.01):
            response = self.client.get("/api/research/" + job["id"] + "/events")
        self.assertIn("event: heartbeat\ndata: {}", response.text)
        self.assertEqual([item["status"] for item in snapshots(response.text)], ["queued", "completed"])

    def test_disconnect_and_cancellation_release_admission_without_changing_job(self):
        job = self.store.create_job(self.owner, "Retail search")
        endpoint = next(route.endpoint for route in self.app.routes
                        if getattr(route, "path", "") == "/api/research/{job_id}/events")

        async def run():
            request = SimpleNamespace(state=SimpleNamespace(owner=self.owner),
                                      is_disconnected=AsyncMock(return_value=False))

            async def consume(response, cancel=False):
                sent = asyncio.Event()

                async def send(message):
                    if message["type"] == "http.response.body":
                        sent.set()

                async def receive():
                    await sent.wait()
                    if cancel:
                        await asyncio.Future()
                    return {"type": "http.disconnect"}

                task = asyncio.create_task(response(
                    {"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send))
                if cancel:
                    await asyncio.wait_for(sent.wait(), 2)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    await asyncio.wait_for(task, 2)

            with patch("app.web_api.RESEARCH_OWNER_STREAM_LIMIT", 1):
                for cancel in (False, True, False):
                    response = await endpoint(job["id"], request)
                    with self.assertRaises(HTTPException) as error:
                        await endpoint(job["id"], request)
                    self.assertEqual(error.exception.status_code, 429)
                    await consume(response, cancel)

        asyncio.run(run())
        self.assertEqual(self.store.get_job(self.owner, job["id"])["status"], "queued")
        self.research.assert_not_called()
        self.analyze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
