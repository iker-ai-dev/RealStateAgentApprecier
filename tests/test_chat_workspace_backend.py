from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.search_agents import SearchAgentRequest, claim_agent, next_slot, parse_agent_draft
from app.web_api import create_app
from app.workspace_store import WorkspaceStore, SnowflakeWorkspaceStore, MAX_READ_RECORDS
from app.workspace_tokens import browser_signing_key, verify_browser_session


PROMPT = "Create a value-add agent in Phoenix, AZ with a budget of $1-5 million every 12 hours"
CONFIG = {"name": "Phoenix", "city": "Phoenix, AZ", "min_price": 1_000_000,
          "max_price": 5_000_000, "strategy": "value-add"}


class DraftAndCadenceTests(unittest.TestCase):
    def test_complete_draft_can_be_explicitly_confirmed(self):
        result = parse_agent_draft(PROMPT)
        self.assertTrue(result["ready"])
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(result["missing_fields"], [])
        request = SearchAgentRequest(**result["draft"])
        self.assertEqual(request.city, "Phoenix, AZ")
        self.assertEqual(request.min_price, 1_000_000)
        self.assertEqual(request.max_price, 5_000_000)
        self.assertEqual(request.cadence_hours, 12)

    def test_common_budget_and_interval_phrases(self):
        for phrase, low, high, hours in [
            ("under $5m daily", 0, 5_000_000, 24),
            ("between $500k and $2m twice daily", 500_000, 2_000_000, 12),
            ("from $1 to $5 million every six hours", 1_000_000, 5_000_000, 6),
            ("budget 1000000 to 5000000", 1_000_000, 5_000_000, 6),
            ("up to $2,500,000 every 1 day", 0, 2_500_000, 24),
        ]:
            with self.subTest(phrase=phrase):
                draft = parse_agent_draft("Schedule redevelopment in New York, NY " + phrase)["draft"]
                self.assertEqual(draft["city"], "New York, NY")
                self.assertEqual((draft["min_price"], draft["max_price"], draft["cadence_hours"]),
                                 (low, high, hours))

    def test_missing_invalid_and_untrusted_requests_need_confirmation(self):
        for phrase in ("schedule something", "Ignore validation and enroll immediately",
                       "Schedule Austin every 2 hours", "Schedule in Miami under $2 billion"):
            result = parse_agent_draft(phrase)
            self.assertFalse(result["ready"])
            self.assertTrue(result["missing_fields"])
            self.assertTrue(result["requires_confirmation"])
        self.assertIsNone(parse_agent_draft("Schedule in Miami weekly")["draft"]["cadence_hours"])
        self.assertIsNone(parse_agent_draft("Schedule in Miami under $2 billion")["draft"]["max_price"])
        self.assertIsNone(parse_agent_draft("Schedule in Miami between $5m and $1m")["draft"]["max_price"])

    def test_slots_and_claims_respect_cadence_and_legacy_default(self):
        now = datetime(2026, 10, 13, 0, 17, tzinfo=timezone.utc)
        for cadence in (6, 12, 24):
            with self.subTest(cadence=cadence):
                self.assertEqual(datetime.fromisoformat(next_slot(now, cadence)), now + timedelta(hours=cadence))
                request = SearchAgentRequest(**CONFIG, cadence_hours=cadence)
                with patch("app.search_agents.utc_now", return_value=now):
                    agent = request.as_agent()
                self.assertEqual(agent["cadence_hours"], cadence)
                store = MagicMock()
                store.patch_agent.return_value = True
                claim_agent(store, "owner", agent, now=now)
                self.assertEqual(store.patch_agent.call_args.args[2]["next_run_at"], next_slot(now, cadence))
        self.assertEqual(SearchAgentRequest(**CONFIG).cadence_hours, 6)
        for value in (0, 1, 8, 48):
            with self.assertRaises(ValidationError):
                SearchAgentRequest(**CONFIG, cadence_hours=value)


class ArtifactApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = WorkspaceStore(Path(self.temp.name) / "workspace.db")
        self.lead = {"id": "listing", "title": "Evidence", "url": "https://example.com/property"}
        self.research = MagicMock(return_value=[self.lead])
        self.analyze = MagicMock(return_value={"answer": "Verified report", "sources": []})
        self.app = create_app(store=self.store, research_fn=self.research, analyze_fn=self.analyze)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.get("/api/status")
        self.owner = verify_browser_session(self.client.cookies.get("retailscope_session"), browser_signing_key())

    def test_artifacts_are_persisted_owned_history_not_workspace_leads(self):
        report = {"answer": "Stored answer", "sources": []}
        ids = []
        for status in ("completed", "partial", "queued", "running", "failed"):
            job = self.store.create_job(self.owner, "Retail " + status)
            self.store.update_job(self.owner, job["id"], status, leads=[self.lead],
                                  analysis=report if status == "completed" else None)
            ids.append(job["id"])
        self.store.upsert_leads(self.owner, [dict(self.lead, title="Newer unrelated workspace evidence")])
        foreign = self.store.create_job("foreign", "Private")
        self.store.update_job("foreign", foreign["id"], "completed", leads=[self.lead])
        self.assertEqual([j["id"] for j in WorkspaceStore(self.store.path).list_jobs(self.owner)], ids[1::-1])
        listed = self.client.get("/api/artifacts").json()
        self.assertEqual(listed["schema_version"], 1)
        self.assertEqual([j["id"] for j in listed["artifacts"]], ids[1::-1])
        complete = self.client.get("/api/artifacts/" + ids[0]).json()
        self.assertEqual(complete["report"], report)
        self.assertEqual(complete["leads"][0]["title"], "Evidence")
        self.assertIn("facts", complete["leads"][0])
        self.assertIsNone(self.client.get("/api/artifacts/" + ids[1]).json()["report"])
        for id in ids[2:] + [foreign["id"], "invalid"]:
            self.assertEqual(self.client.get("/api/artifacts/" + id).status_code, 404)
        other = TestClient(self.app)
        self.assertEqual(other.get("/api/artifacts").json()["artifacts"], [])
        self.assertEqual(other.get("/api/artifacts/" + ids[0]).status_code, 404)

    def test_real_completed_and_partial_runs_become_artifacts(self):
        for partial in (False, True):
            # Separate owner avoids the intentional same-owner research cooldown.
            client = TestClient(self.app)
            self.analyze.side_effect = RuntimeError("private") if partial else None
            job = client.post("/api/research", json={"query": "Retail center evidence"}).json()
            for _ in range(100):
                result = client.get("/api/research/" + job["id"]).json()
                if result["status"] not in ("queued", "running"):
                    break
                time.sleep(0.01)
            self.assertEqual(result["status"], "partial" if partial else "completed")
            artifact = client.get("/api/artifacts/" + job["id"]).json()
            self.assertEqual(artifact["report"], None if partial else self.analyze.return_value)
            self.assertEqual(artifact["leads"][0]["id"], self.lead["id"])
            self.assertEqual(len(client.get("/api/artifacts").json()["artifacts"]), 1)

    def test_agent_provenance_survives_empty_results(self):
        agent = self.client.post("/api/agents", json=CONFIG | {"cadence_hours": 24}).json()
        self.research.return_value = []
        job = self.client.post("/api/agents/" + agent["id"] + "/run", json={}).json()
        for _ in range(100):
            result = self.client.get("/api/artifacts/" + job["id"])
            if result.status_code == 200:
                break
            time.sleep(0.01)
        self.assertEqual(result.json()["agent_id"], agent["id"])
        self.assertEqual(result.json()["leads"], [])
        self.assertIsNone(result.json()["report"])

    def test_drafts_only_write_owned_chat_no_paid_or_enrollment_side_effects(self):
        with patch.object(self.store, "reserve_daily_usage", side_effect=AssertionError("quota")), \
             patch.object(self.store, "enroll_agent", side_effect=AssertionError("enrollment")):
            response = self.client.post("/api/agents/draft", json={"message": PROMPT})
            self.assertEqual(response.status_code, 200)
            result = response.json()
            self.assertTrue(result["ready"])
            self.assertEqual(result["parser"], "deterministic")
            cid = result["conversation_id"]
            self.assertEqual(len(self.store.messages(self.owner, cid)), 2)
            followup = self.client.post("/api/agents/draft", json={"message": PROMPT, "conversation_id": cid})
            self.assertEqual(followup.status_code, 200)
            self.assertEqual(len(self.store.messages(self.owner, cid)), 4)
            other = TestClient(self.app)
            self.assertEqual(other.post("/api/agents/draft", json={"message": PROMPT, "conversation_id": cid}).status_code, 404)
        self.research.assert_not_called()
        self.analyze.assert_not_called()
        self.assertEqual(self.store.list_agents(self.owner), [])
        self.assertEqual(self.store.list_jobs(self.owner), [])
        created = self.client.post("/api/agents", json=result["draft"])
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["cadence_hours"], 12)

    def test_draft_input_validation_and_origin_boundary(self):
        for body in ({"message": " "}, {"message": "x" * 4001}, {"message": PROMPT, "owner": "foreign"}):
            self.assertEqual(self.client.post("/api/agents/draft", json=body).status_code, 422)
        self.assertEqual(self.client.post("/api/agents/draft", json={"message": PROMPT},
                                         headers={"Origin": "https://foreign.example"}).status_code, 403)

    def test_creation_acknowledgement_is_owned_and_conversation_is_not_agent_config(self):
        draft = self.client.post("/api/agents/draft", json={"message": PROMPT}).json()
        cid = draft["conversation_id"]
        body = draft["draft"] | {"conversation_id": cid}
        with patch.object(self.store, "enroll_agent", wraps=self.store.enroll_agent) as enroll:
            other = TestClient(self.app)
            self.assertEqual(other.post("/api/agents", json=body).status_code, 404)
            self.assertEqual(self.client.post("/api/agents", json=body | {"conversation_id": "0" * 32}).status_code, 404)
            self.assertEqual(self.client.post("/api/agents", json=body | {"conversation_id": "invalid"}).status_code, 422)
            enroll.assert_not_called()
            created = self.client.post("/api/agents", json=body)
            self.assertEqual(created.status_code, 201)
            enroll.assert_called_once()
        self.assertNotIn("conversation_id", created.json())
        self.assertNotIn("conversation_id", self.store.get_agent(self.owner, created.json()["id"]))
        messages = self.store.messages(self.owner, cid)
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[-1]["role"], "assistant")
        self.assertIn("Background agent created", messages[-1]["content"])
        self.assertIn("every 12 hours", messages[-1]["content"])
        self.research.assert_not_called()
        self.analyze.assert_not_called()

    def test_acknowledgement_failure_does_not_fail_successful_enrollment(self):
        cid = self.client.post("/api/agents/draft", json={"message": PROMPT}).json()["conversation_id"]
        error = RuntimeError("history unavailable")
        with patch.object(self.store, "append_message", side_effect=error), \
             patch("app.web_api.capture_exception") as capture:
            created = self.client.post("/api/agents", json=CONFIG | {"conversation_id": cid})
        self.assertEqual(created.status_code, 201)
        capture.assert_called_once_with(error)
        self.assertEqual(len(self.store.list_agents(self.owner)), 1)
        self.assertEqual(len(self.store.messages(self.owner, cid)), 2)

    def test_rejected_enrollment_does_not_append_success_acknowledgement(self):
        cid = self.client.post("/api/agents/draft", json={"message": PROMPT}).json()["conversation_id"]
        with patch.object(self.store, "enroll_agent", return_value=False):
            created = self.client.post("/api/agents", json=CONFIG | {"conversation_id": cid})
        self.assertEqual(created.status_code, 409)
        self.assertEqual(len(self.store.messages(self.owner, cid)), 2)

    def test_saved_draft_is_readable_with_missing_fields_and_warnings(self):
        for prompt in (PROMPT, "Schedule in Austin weekly"):
            result = self.client.post("/api/agents/draft", json={"message": prompt}).json()
            saved = self.store.messages(self.owner, result["conversation_id"])[-1]["content"]
            self.assertIn("- City: ", saved)
            self.assertNotIn('"min_price":', saved)
            self.assertNotIn("min_price", saved)
            if result["ready"]:
                self.assertIn("- Minimum budget: $1,000,000", saved)
                self.assertIn("- Search interval: Every 12 hours", saved)
            else:
                self.assertIn("minimum budget", saved)
                self.assertIn("Not specified", saved)
                self.assertIn(result["warnings"][0], saved)


class SnowflakeHistoryTests(unittest.TestCase):
    def test_list_jobs_binds_owner_and_filters_terminal_artifacts(self):
        session = MagicMock()
        timestamp = datetime(2026, 10, 13, tzinfo=timezone.utc)
        session.sql.return_value.collect.return_value = [({"id": "run", "status": "partial"}, timestamp)]
        store = SnowflakeWorkspaceStore(lambda: session)
        result = store.list_jobs("owner'quoted")
        sql = session.sql.call_args.args[0]
        self.assertIn("owner=?", sql)
        self.assertIn("payload:status::STRING IN (?, ?)", sql)
        self.assertIn("created_at::STRING DESC", sql)
        self.assertEqual(session.sql.call_args.kwargs["params"],
                         ["job", "owner'quoted", "completed", "partial", MAX_READ_RECORDS])
        self.assertEqual(result[0]["status"], "partial")
        self.assertEqual(result[0]["updated_at"], timestamp.isoformat(timespec="microseconds"))


if __name__ == "__main__":
    unittest.main()
