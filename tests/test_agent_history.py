import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.web_api import create_app
from app.workspace_store import WorkspaceStore, SnowflakeWorkspaceStore
from app.workspace_tokens import browser_signing_key, verify_browser_session


STATES = ("queued", "running", "completed", "partial", "failed")


class AgentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = WorkspaceStore(Path(self.temp.name) / "workspace.db")

    def job(self, owner="owner", agent="agent", status="completed", day=1, leads=None):
        with patch("app.workspace_store._now", return_value=f"2026-10-{day:02d}T00:00:00+00:00"):
            job = self.store.create_job(owner, "Find shopping centers", agent_id=agent)
        self.store.update_job(owner, job["id"], status, leads=leads,
                              error="Run failed" if status == "failed" else None)
        return job

    def test_all_states_ordering_and_owner_agent_isolation(self):
        jobs = [self.job(status=state, day=index + 1) for index, state in enumerate(STATES)]
        self.job(owner="foreign", day=20)
        self.job(agent="other", day=21)
        self.job(agent=None, day=22)
        rows = self.store.list_agent_jobs("owner", "agent")
        self.assertEqual([row["id"] for row in rows], [job["id"] for job in reversed(jobs)])
        self.assertEqual([row["status"] for row in rows], list(reversed(STATES)))
        self.assertEqual(rows[0]["error"], "Run failed")
        self.assertEqual(self.store.list_agent_jobs("outsider", "agent"), [])
        self.assertEqual({row["status"] for row in self.store.list_jobs("owner")},
                         {"completed", "partial"})

    def test_legacy_provenance_must_be_unambiguous_and_explicit_id_wins(self):
        lead = {"id": "lead", "agent_id": "agent"}
        legacy = self.job(agent=None, leads=[lead, {"id": "untagged"}])
        self.job(agent=None, leads=[lead, {"id": "other", "agent_id": "other"}])
        self.job(agent="other", leads=[lead])
        # A foreign record with the same ID cannot supply missing provenance.
        untagged = self.job(agent=None)
        self.store._write("job", "foreign", [(untagged["id"], json.dumps({
            **untagged, "status": "completed", "leads": [lead]}))])
        self.assertEqual([row["id"] for row in self.store.list_agent_jobs("owner", "agent")],
                         [legacy["id"]])

    def test_history_is_bounded_and_ties_have_stable_order(self):
        jobs = [self.job(status="failed") for _ in range(103)]
        rows = self.store.list_agent_jobs("owner", "agent")
        self.assertEqual(len(rows), 100)
        self.assertEqual([row["id"] for row in rows],
                         sorted((job["id"] for job in jobs), reverse=True)[:100])

    def test_sqlite_binds_both_ownership_scopes_and_limit(self):
        connection = Mock()
        connection.execute.return_value.fetchall.return_value = []
        with patch.object(self.store, "_connect", return_value=connection):
            self.assertEqual(self.store.list_agent_jobs("owner", "agent"), [])
        sql, params = connection.execute.call_args.args
        self.assertEqual(params, ("owner", "agent", "owner", "agent", 100))
        self.assertIn("WHERE owner=?", sql)
        self.assertIn("WHERE legacy.owner=?", sql)
        self.assertIn("ORDER BY json_extract(payload, '$.created_at') DESC, id DESC LIMIT ?", sql)
        connection.close.assert_called_once()

    def test_api_ownership_summary_and_no_research_side_effects(self):
        research, analyze = Mock(), Mock()
        app = create_app(store=self.store, research_fn=research, analyze_fn=analyze)
        with TestClient(app) as client:
            client.get("/api/status")
            owner = verify_browser_session(client.cookies.get("retailscope_session"), browser_signing_key())
            self.store.save_agent(owner, {"id": "agent"})
            self.store.save_agent("foreign", {"id": "foreign-agent"})
            self.assertEqual(client.get("/api/agents/agent/runs").json(), {"runs": []})
            jobs = [self.job(owner=owner, status=state, day=i + 1) for i, state in enumerate(STATES)]
            self.store.update_job(owner, jobs[2]["id"], "completed", analysis={"answer": "Report"})
            legacy = self.job(owner=owner, agent=None, day=6, leads=[{"id": "lead", "agent_id": "agent"}])
            response = client.get("/api/agents/agent/runs")
            self.assertEqual(response.status_code, 200)
            rows = response.json()["runs"]
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows[0], {"id": legacy["id"], "created_at": legacy["created_at"],
                "status": "completed", "query": legacy["query"], "lead_count": 1,
                "has_report": False, "agent_id": "agent"})
            self.assertEqual(rows[1]["error"], "Run failed")
            self.assertTrue(next(row for row in rows if row["id"] == jobs[2]["id"])["has_report"])
            for agent in ("foreign-agent", "missing", "%20", "x" * 257, "\u00e9" * 129):
                self.assertEqual(client.get(f"/api/agents/{agent}/runs").status_code, 404)
            client.cookies.clear()
            client.get("/api/status")
            self.assertEqual(client.get("/api/agents/agent/runs").status_code, 404)
        research.assert_not_called()
        analyze.assert_not_called()


class SnowflakeAgentHistoryTests(unittest.TestCase):
    def test_bound_parameters_states_legacy_scope_and_decoding(self):
        session = Mock()
        payload = {"id": "job", "created_at": "2026-10-13T00:00:00+00:00",
                   "status": "failed", "query": "Search", "agent_id": "agent"}
        session.sql.return_value.collect.return_value = [(json.dumps(payload), payload["created_at"])]
        store = SnowflakeWorkspaceStore(lambda: session)
        rows = store.list_agent_jobs("owner", "agent")
        self.assertEqual(rows[0]["status"], "failed")
        sql = session.sql.call_args.args[0]
        self.assertEqual(session.sql.call_args.kwargs["params"],
                         ["job", "owner", *STATES, "agent", "job", "owner", "agent", 100])
        self.assertIn("record_type=? AND owner=?", sql)
        self.assertIn("legacy.record_type=? AND legacy.owner=?", sql)
        self.assertIn("COUNT(DISTINCT lead.value:agent_id::STRING)=1", sql)
        self.assertIn("NULLIF(payload:agent_id::STRING, '') IS NULL", sql)
        self.assertIn("ORDER BY payload:created_at::STRING DESC, id DESC LIMIT ?", sql)
        session.sql.return_value.collect.assert_called_once_with(
            statement_params={"STATEMENT_TIMEOUT_IN_SECONDS": "30"}, log_on_exception=False)


if __name__ == "__main__":
    unittest.main()
