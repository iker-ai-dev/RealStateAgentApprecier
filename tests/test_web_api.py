import tempfile
import time
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.web_api import UsageGate, create_app
from app.workspace_store import WorkspaceStore
from app.workspace_tokens import browser_signing_key, verify_browser_session


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = WorkspaceStore(Path(self.temp.name) / "workspace.db")
        self.lead = {"id": "lead-one", "title": "A retail listing", "url": "https://example.com/property"}
        self.calls = []

        def analyze(message, leads, history=None):
            self.calls.append((message, leads, history))
            return {"answer": "Request the rent roll before setting an offer price.", "sources": []}

        self.app = create_app(store=self.store, research_fn=lambda query: [self.lead], analyze_fn=analyze)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def test_research_chat_and_isolation(self):
        self.client.get("/api/status")
        created = self.client.post("/api/research", json={"query": "retail centers in Austin"})
        self.assertEqual(created.status_code, 202)
        job_id = created.json()["id"]
        for _ in range(100):
            result = self.client.get(f"/api/research/{job_id}").json()
            if result["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        self.assertEqual(result["status"], "completed")
        report_messages = self.client.get(f"/api/conversations/{job_id}").json()["messages"]
        self.assertEqual(len(report_messages), 2)
        self.assertEqual(report_messages[1]["content"], result["analysis"]["answer"])
        self.assertEqual(self.client.get("/api/leads").json()["leads"], [self.lead])
        response = self.client.post("/api/chat", json={"message": "Should I buy it?", "lead_id": "lead-one"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[0][1], [self.lead])
        conversation = response.json()["conversation_id"]
        self.client.post("/api/chat", json={"message": "What evidence?", "conversation_id": conversation})
        self.assertEqual(len(self.calls[-1][2]), 2)
        saved = self.client.get("/api/conversations").json()["conversations"]
        self.assertEqual(saved[0]["id"], conversation)
        self.assertEqual(len(self.client.get(f"/api/conversations/{conversation}").json()["messages"]), 4)
        other = TestClient(self.app)
        self.assertEqual(other.get("/api/leads").json()["leads"], [])
        self.assertEqual(other.get(f"/api/research/{job_id}").status_code, 404)
        self.assertEqual(other.get(f"/api/conversations/{conversation}").status_code, 404)
        self.assertEqual(other.post("/api/chat", json={"message": "Inspect", "lead_id": "lead-one"}).status_code, 404)

    def test_rejects_cross_origin_and_invalid_inputs(self):
        response = self.client.post("/api/chat", json={"message": "hello"}, headers={"Origin": "https://attacker.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post("/api/chat", json={"message": "   "}).status_code, 422)
        self.assertEqual(self.client.post("/api/chat", json={"message": "x" * 17000}).status_code, 413)
        self.assertEqual(self.client.post("/api/chat", content="hello").status_code, 415)
        self.assertEqual(self.client.get("/api/status", headers={"Host": "attacker.example"}).status_code, 400)

    def test_research_followup_uses_its_report_sources_not_older_workspace_leads(self):
        self.client.get('/api/status')
        owner = verify_browser_session(self.client.cookies.get('retailscope_session'), browser_signing_key())
        stale = [dict(self.lead, id=f'old-{i}', title='Old unrelated source') for i in range(10)]
        self.store.upsert_leads(owner, stale)
        job = self.store.create_job(owner, 'Current retail search')
        self.store.upsert_leads(owner, [self.lead])
        self.store.update_job(owner, job['id'], 'completed', leads=[self.lead])
        response = self.client.post('/api/chat', json={
            'message': 'Which evidence should I verify?', 'conversation_id': job['id']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[-1][1], [self.lead])

    def test_ad_hoc_chat_prefers_recent_sources(self):
        self.client.get('/api/status')
        owner = verify_browser_session(self.client.cookies.get('retailscope_session'), browser_signing_key())
        self.store.upsert_leads(owner, [dict(self.lead, id=f'old-{i}') for i in range(10)])
        self.store.upsert_leads(owner, [self.lead])
        response = self.client.post('/api/chat', json={'message': 'Current evidence?'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.lead, self.calls[-1][1])
        self.assertEqual(len(self.calls[-1][1]), 10)

    def test_no_sample_leads_on_new_workspace(self):
        response = self.client.get("/api/leads")
        self.assertEqual(response.json(), {"leads": []})
        self.assertIn("httponly", response.headers["set-cookie"].lower())

    def test_model_failure_is_explicit_and_does_not_save_fake_answer(self):
        def fail(*args, **kwargs):
            raise RuntimeError("provider-secret-not-for-browser")

        app = create_app(store=self.store, research_fn=lambda query: [], analyze_fn=fail)
        with TestClient(app) as client:
            response = client.post("/api/chat", json={"message": "Analyze this market"})
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("provider-secret", response.text)
            owner = verify_browser_session(client.cookies.get("retailscope_session"), browser_signing_key())
            self.assertEqual(self.store.list_leads(owner), [])

    def test_calculation_endpoint_uses_explicit_assumptions(self):
        response = self.client.post("/api/scenarios", json={"purchase_price": 1000000,
            "annual_noi": 100000, "exit_cap_rate": 0.1, "hold_years": 5})
        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(response.json()["irr"], 0.1)
        self.assertEqual(self.client.post("/api/scenarios", json={}).status_code, 422)

    def test_research_preserves_sources_when_analysis_fails(self):
        def fail(*args, **kwargs):
            raise RuntimeError("private-provider-response")

        app = create_app(store=self.store, research_fn=lambda query: [self.lead], analyze_fn=fail)
        with TestClient(app) as client:
            client.get('/api/status')
            job = client.post('/api/research', json={'query': 'Phoenix redevelopment'}).json()
            for _ in range(100):
                result = client.get('/api/research/' + job['id']).json()
                if result['status'] not in ('queued', 'running'):
                    break
                time.sleep(0.01)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['leads'], [self.lead])
            self.assertIn('Sources were saved', result['error'])
            self.assertNotIn('private-provider', result['error'])
            self.assertFalse(result.get('analysis'))
            self.assertEqual(client.get('/api/leads').json()['leads'], [self.lead])
            self.assertEqual(client.get('/api/conversations').json()['conversations'], [])
            other = TestClient(app)
            self.assertEqual(other.get('/api/leads').json()['leads'], [])
            self.assertEqual(other.get('/api/research/' + job['id']).status_code, 404)

    def test_paid_calls_are_bounded_even_when_cookie_changes(self):
        gate = UsageGate()
        for i in range(20):
            gate.reserve(str(i), "same-ip")
            gate.release(str(i))
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as error:
            gate.reserve("new-cookie", "same-ip")
        self.assertEqual(error.exception.status_code, 429)

    def test_background_research_does_not_block_ad_hoc_chat(self):
        gate = UsageGate()
        gate.reserve("owner", "ip", "research")
        gate.reserve("owner", "ip", "chat")
        self.assertTrue(gate.is_active("owner", "research"))
        gate.release("owner", "chat")
        self.assertTrue(gate.is_active("owner", "research"))
        self.assertFalse(gate.is_active("owner", "chat"))

    def test_long_conversation_is_bounded_for_the_model_without_losing_storage(self):
        response = self.client.get("/api/status")
        owner = self.client.cookies.get("retailscope_session").split(".")[1]
        conversation = "a" * 32
        for index in range(12):
            self.store.append_message(owner, conversation, "user" if index % 2 == 0 else "assistant", "x" * 5000)
        response = self.client.post("/api/chat", json={"message": "Continue", "conversation_id": conversation})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.calls[-1][2]), 8)
        self.assertTrue(all(len(item["content"]) <= 3000 for item in self.calls[-1][2]))
        self.assertEqual(len(self.store.messages(owner, conversation)[0]["content"]), 5000)

    @patch.dict(os.environ, {"RETAILSCOPE_MCP_SIGNING_KEY": "test-only-signing-key-" * 3})
    def test_mcp_uses_same_workspace_and_requires_signed_token(self):
        self.client.get("/api/status")
        owner = self.client.cookies.get("retailscope_session").split(".")[1]
        self.store.upsert_leads(owner, [self.lead])
        token = self.client.post("/api/mcp-token", json={}).json()["token"]
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "list_research_leads", "arguments": {}}}
        self.assertEqual(self.client.post("/mcp", json=body).status_code, 401)
        headers = {"Authorization": "Bearer " + token,
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2025-11-25"}
        response = self.client.post("/mcp", json=body, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        self.assertFalse(result.get("isError"), result)
        self.assertIn("structuredContent", result, result)
        self.assertEqual(result["structuredContent"]["leads"], [self.lead])
        body["params"] = {"name": "calculate_investment_scenario", "arguments": {
            "assumptions": {"purchase_price": 1000000, "annual_noi": 100000, "exit_cap_rate": 0.1}}}
        result = self.client.post("/mcp", json=body, headers=headers).json()["result"]
        self.assertAlmostEqual(result["structuredContent"]["irr"], 0.1)
        attacker = TestClient(self.app)
        attacker.cookies.set("retailscope_session", owner)
        self.assertEqual(attacker.get("/api/leads").json()["leads"], [])


if __name__ == "__main__":
    unittest.main()
