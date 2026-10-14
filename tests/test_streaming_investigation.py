import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.web_api import create_app
from app.workspace_store import WorkspaceStore


class StreamingInvestigationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = WorkspaceStore(Path(temp.name) / "workspace.db")

    def stream(self, client, job):
        with patch("app.web_api.RESEARCH_POLL_SECONDS", 0.01):
            response = client.get(f"/api/research/{job['id']}/events")
        self.assertEqual(response.status_code, 200)
        return [json.loads(part.split("data: ", 1)[1]) for part in response.text.split("\n\n")
                if part.startswith("event: progress\n")]

    def test_chat_stream_persists_activity_and_keeps_artifacts_clean(self):
        entered, finish = threading.Event(), threading.Event()

        def analyze(message, leads, history=None, *, on_progress=None, mode=None):
            self.assertEqual(mode, "chat")
            on_progress("analyzing")
            entered.set()
            finish.wait(3)
            on_progress("validating")
            return {"answer": "Hi! What would you like to investigate?", "sources": []}

        app = create_app(store=self.store, research_fn=lambda query: [], analyze_fn=analyze)
        with TestClient(app) as client:
            job = client.post("/api/chat/start", json={"message": "hi"}).json()
            try:
                self.assertTrue(entered.wait(3))
                running = client.get(f"/api/research/{job['id']}").json()
                self.assertEqual(running["status"], "running")
                self.assertGreaterEqual(len(running["activity"]), 2)
                self.assertNotIn("analysis", running)
                runs = client.get("/api/runs")
                self.assertEqual(runs.status_code, 200)
                self.assertEqual([run["id"] for run in runs.json()["runs"]], [job["id"]])
                conversations = client.get("/api/conversations").json()["conversations"]
                self.assertIn(job["conversation_id"], [item["id"] for item in conversations])
                pending_messages = client.get(
                    f"/api/conversations/{job['conversation_id']}").json()["messages"]
                self.assertEqual(pending_messages, [{"role": "user", "content": "hi"}])
                other = TestClient(app)
                self.addCleanup(other.close)
                self.assertEqual(other.get("/api/runs").json()["runs"], [])
                self.assertEqual(other.get(f"/api/research/{job['id']}/events").status_code, 404)
                self.assertEqual(other.post("/api/chat/start", json={
                    "message": "hi", "conversation_id": job["conversation_id"]}).status_code, 404)
            finally:
                finish.set()
            final = self.stream(client, job)[-1]
            self.assertEqual(final["status"], "completed")
            self.assertEqual(client.get("/api/runs").json()["runs"], [])
            self.assertEqual(final["analysis"]["conversation_id"], job["conversation_id"])
            self.assertEqual(client.get("/api/artifacts").json()["artifacts"], [])
            messages = client.get(f"/api/conversations/{job['conversation_id']}").json()["messages"]
            self.assertEqual(len(messages), 2)
            self.assertEqual(self.stream(client, job)[-1]["activity"], final["activity"])

    def test_greeting_then_research_followups_use_latest_research_context(self):
        queries = []
        lead = {"id": "retail", "title": "Retail", "url": "https://example.com/retail"}

        def research(query):
            queries.append(query)
            return [lead]

        app = create_app(store=self.store, research_fn=research,
                         analyze_fn=lambda *args, **kwargs: {"answer": "Report", "sources": []})
        with TestClient(app) as client:
            response = client.post("/api/chat/start", json={"message": "hi"})
            self.assertEqual(response.status_code, 202)
            greeting = response.json()
            self.assertEqual(self.stream(client, greeting)[-1]["status"], "completed")
            conversation_id = greeting["conversation_id"]

            def investigate(query):
                response = client.post("/api/research", json={
                    "query": query, "conversation_id": conversation_id})
                self.assertEqual(response.status_code, 202)
                final = self.stream(client, response.json())[-1]
                self.assertEqual(final["status"], "completed")
                self.assertEqual(final["conversation_id"], conversation_id)
                # Persist the effective query so another follow-up retains its brief.
                self.assertEqual(final["query"], queries[-1])
                return queries[-1]

            brief = "Find shopping centers in San Diego under $5M"
            self.assertEqual(investigate(brief), brief)
            followup = investigate("Find better resources")
            self.assertIn(brief, followup)
            self.assertIn("Find better resources", followup)
            self.assertNotIn("hi\n", followup)
            second_followup = investigate("Continue with additional rent-roll evidence")
            self.assertIn(brief, second_followup)
            self.assertIn("Find better resources", second_followup)

            # An explicit new market must replace, not inherit, the previous brief.
            new_brief = "Find shopping centers in Phoenix under $2M"
            self.assertEqual(investigate(new_brief), new_brief)
            latest = investigate("Find more sources")
            self.assertIn(new_brief, latest)
            self.assertNotIn("San Diego", latest)
            messages = client.get(f"/api/conversations/{conversation_id}").json()["messages"]
            submitted = [item["content"] for item in messages if item["role"] == "user"]
            self.assertEqual(submitted, ["hi", brief, "Find better resources",
                                         "Continue with additional rent-roll evidence",
                                         new_brief, "Find more sources"])

    def test_chat_uses_latest_partial_followup_sources_only_from_its_conversation(self):
        calls = []
        retrieved = []

        def research(query):
            number = len(retrieved)
            lead = {"id": f"lead-{number}", "title": f"Retail {number}",
                    "url": f"https://example.com/property/{number}"}
            retrieved.append(lead)
            return [lead]

        def analyze(message, leads, history=None, *, mode=None):
            calls.append((mode, list(leads)))
            if mode == "research" and leads[0]["id"] != "lead-0":
                raise RuntimeError("Injected analysis validation failure")
            return {"answer": "Report", "sources": []}

        app = create_app(store=self.store, research_fn=research, analyze_fn=analyze)
        with TestClient(app) as client:
            original = client.post("/api/research", json={
                "query": "Shopping centers in San Diego"}).json()
            self.assertEqual(self.stream(client, original)[-1]["status"], "completed")
            followup = client.post("/api/research", json={
                "query": "Find better resources", "conversation_id": original["id"]}).json()
            final = self.stream(client, followup)[-1]
            self.assertEqual(final["status"], "partial")
            self.assertEqual([lead["id"] for lead in final["leads"]], ["lead-1"])
            saved_messages = client.get(
                f"/api/conversations/{original['id']}").json()["messages"]
            self.assertEqual(saved_messages[-1], {"role": "user", "content": "Find better resources"})
            self.assertEqual(sum(item["role"] == "assistant" for item in saved_messages), 1)

            # Newer jobs elsewhere must not become this conversation's evidence.
            unrelated = client.post("/api/research", json={
                "query": "Shopping centers in Boston"}).json()
            self.assertEqual(self.stream(client, unrelated)[-1]["status"], "partial")
            other = TestClient(app)
            self.addCleanup(other.close)
            foreign = other.post("/api/research", json={
                "query": "Shopping centers in Chicago"}).json()
            self.assertEqual(self.stream(other, foreign)[-1]["status"], "partial")
            self.assertEqual(other.get(f"/api/research/{followup['id']}/events").status_code, 404)
            self.assertEqual(other.post("/api/chat/start", json={
                "message": "Inspect the saved sources", "conversation_id": original["id"]
            }).status_code, 404)

            response = client.post("/api/chat/start", json={
                "message": "Inspect the sources you just saved", "conversation_id": original["id"]})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(self.stream(client, response.json())[-1]["status"], "completed")
            self.assertEqual(calls[-1], ("chat", [retrieved[1]]))

    def test_followup_investigates_with_context_and_preserves_thread(self):
        queries = []
        lead = {"id": "retail", "title": "Retail", "url": "https://example.com/retail"}

        def research(query, *, on_progress=None):
            queries.append(query)
            on_progress({"stage": "inspecting", "message": "Reading a retrieved property source.",
                         "leads": [lead]})
            on_progress({"stage": "followup", "message": "Looking for missing rent-roll evidence."})
            return [lead]

        app = create_app(store=self.store, research_fn=research,
                         analyze_fn=lambda *args, **kwargs: {"answer": "Report", "sources": []})
        with TestClient(app) as client:
            original = client.post("/api/research", json={"query": "Shopping centers in Phoenix"}).json()
            self.stream(client, original)
            followup = client.post("/api/research", json={"query": "Find better resources",
                                    "conversation_id": original["id"]}).json()
            final = self.stream(client, followup)[-1]
            self.assertIn("Shopping centers in Phoenix", queries[-1])
            self.assertIn("Find better resources", queries[-1])
            self.assertEqual(final["conversation_id"], original["id"])
            self.assertEqual([entry["stage"] for entry in final["activity"]],
                             ["searching", "inspecting", "followup"])
            self.assertEqual(len(client.get(f"/api/conversations/{original['id']}").json()["messages"]), 4)
            other = TestClient(app)
            self.assertEqual(other.post("/api/research", json={"query": "Find more sources",
                             "conversation_id": original["id"]}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
