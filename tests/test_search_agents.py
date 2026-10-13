from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.search_agents import SearchAgentRequest, claim_agent, next_slot, stamp, scheduler_key
from app.workspace_store import WorkspaceStore, SnowflakeWorkspaceStore
from app.workspace_tokens import browser_signing_key, verify_browser_session
from app.web_api import create_app


CONFIG = {"name": "Phoenix value-add", "city": "Phoenix, AZ", "min_price": 1_000_000,
          "max_price": 5_000_000, "strategy": "value-add"}


class SearchAgentStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = WorkspaceStore(Path(self.temp.name) / "workspace.db")
        self.agent = SearchAgentRequest(**CONFIG).as_agent()
        self.store.save_agent("owner", self.agent)

    def test_slots_are_utc_and_aligned_every_six_hours(self):
        self.assertEqual(next_slot(datetime(2026, 10, 13, 6, 16, tzinfo=timezone.utc)),
                         "2026-10-13T06:17:00.000000+00:00")
        self.assertEqual(next_slot(datetime(2026, 10, 13, 6, 17, tzinfo=timezone.utc)),
                         "2026-10-13T12:17:00.000000+00:00")
        local = datetime(2026, 10, 13, 1, tzinfo=timezone(timedelta(hours=-5)))
        self.assertEqual(next_slot(local), "2026-10-13T06:17:00.000000+00:00")

    def test_budget_validation_and_no_arbitrary_owner_or_schedule(self):
        for change in ({"min_price": -1}, {"max_price": 1}, {"owner": "victim"},
                       {"cadence_hours": 0}, {"city": "\n"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                SearchAgentRequest(**(CONFIG | change))
        self.assertIn("$1,000,000 to $5,000,000", self.agent["query"])

    def test_durable_owned_configs_and_receipts(self):
        reopened = WorkspaceStore(self.store.path)
        self.assertEqual(reopened.get_agent("owner", self.agent["id"]), self.agent)
        self.assertEqual(reopened.list_agents("stranger"), [])
        self.assertFalse(reopened.patch_agent("stranger", self.agent["id"], {"enabled": False}))
        self.store.mark_lead_read("owner", "lead")
        self.assertEqual(reopened.list_read_receipts("owner")[0]["id"], "lead")
        self.assertEqual(reopened.list_read_receipts("stranger"), [])

    def test_claim_is_atomic_and_lease_prevents_duplicate_paid_work(self):
        stores = [self.store, WorkspaceStore(self.store.path)]
        with ThreadPoolExecutor(max_workers=8) as executor:
            claims = list(executor.map(lambda i: claim_agent(stores[i % 2], "owner", self.agent), range(16)))
        self.assertEqual(sum(value is not None for value in claims), 1)
        current = self.store.get_agent("owner", self.agent["id"])
        self.assertIsNone(claim_agent(self.store, "owner", current))
        later = datetime.fromisoformat(current["lease_until"]) + timedelta(seconds=1)
        self.assertIsNotNone(claim_agent(self.store, "owner", current, now=later))

    def test_due_selection_pause_and_manual_run(self):
        now = datetime.now(timezone.utc)
        self.store.patch_agent("owner", self.agent["id"], {"next_run_at": stamp(now - timedelta(seconds=1))})
        self.assertEqual(len(self.store.due_agents(stamp(now))), 1)
        self.store.patch_agent("owner", self.agent["id"], {"enabled": False})
        self.assertEqual(self.store.due_agents(stamp(now)), [])
        current = self.store.get_agent("owner", self.agent["id"])
        self.assertIsNone(claim_agent(self.store, "owner", current, scheduled=True, now=now))
        self.assertIsNotNone(claim_agent(self.store, "owner", current, now=now))

    def test_completion_does_not_undo_pause_or_overwrite_newer_run(self):
        run_id = claim_agent(self.store, "owner", self.agent)
        self.store.patch_agent("owner", self.agent["id"], {"enabled": False})
        self.assertTrue(self.store.patch_agent("owner", self.agent["id"], {"last_status": "completed"}, {"run_id": run_id}))
        self.assertFalse(self.store.get_agent("owner", self.agent["id"])["enabled"])
        self.assertFalse(self.store.patch_agent("owner", self.agent["id"], {"last_status": "failed"}, {"run_id": "stale"}))

    def test_snowflake_claim_is_single_conditional_update_with_bound_parameters(self):
        session = MagicMock()
        session.sql.return_value.collect.return_value = [(1,)]
        store = SnowflakeWorkspaceStore(lambda: session)
        self.assertTrue(store.patch_agent("owner", "id", {"run_id": "new"}, {"run_id": None, "enabled": True}))
        sql = session.sql.call_args.args[0]
        self.assertIn("EQUAL_NULL(payload:run_id, PARSE_JSON(?))", sql)
        self.assertIn("COUNT(*)", sql)
        self.assertNotIn("new", sql)
        self.assertEqual(session.sql.call_count, 1)
        session.sql.return_value.collect.return_value = [(0,)]
        self.assertFalse(store.patch_agent("owner", "id", {"enabled": False}))

    def test_dispatch_credential_is_domain_separated(self):
        with patch.dict(os.environ, {}, clear=True):
            key = b"test" * 16
            self.assertEqual(len(scheduler_key(key)), 64)
            self.assertNotEqual(scheduler_key(key), key.decode())
        with patch.dict(os.environ, {"RETAILSCOPE_SCHEDULER_SECRET": "short"}):
            self.assertEqual(scheduler_key(b"anything"), "")

    def test_enrollment_cap_is_global_durable_and_atomic(self):
        expiry = stamp(datetime.now(timezone.utc) + timedelta(days=30))
        with ThreadPoolExecutor(max_workers=8) as executor:
            reserved = list(executor.map(lambda i: WorkspaceStore(self.store.path).reserve_agent_slot(str(i), expiry), range(30)))
        self.assertEqual(sum(reserved), 20)
        self.assertFalse(WorkspaceStore(self.store.path).reserve_agent_slot('extra', expiry))

    def test_failed_enrollment_rolls_back_its_global_slot(self):
        with closing(self.store._connect()) as connection, connection:
            connection.execute("CREATE TRIGGER reject_enrollment BEFORE INSERT ON workspace_records "
                               "WHEN NEW.kind='agent' BEGIN SELECT RAISE(ABORT, 'failure'); END")
        for _ in range(21):
            with self.assertRaises(Exception):
                self.store.enroll_agent('owner', SearchAgentRequest(**CONFIG).as_agent())
        with closing(self.store._connect()) as connection, connection:
            connection.execute('DROP TRIGGER reject_enrollment')
        self.assertTrue(self.store.enroll_agent('owner', SearchAgentRequest(**CONFIG).as_agent()))

    def test_expired_subscriptions_never_dispatch(self):
        self.agent.update(expires_at="2020-01-01T00:00:00.000000+00:00",
                          next_run_at="2020-01-01T00:00:00.000000+00:00")
        self.store.save_agent('owner', self.agent)
        self.assertEqual(self.store.due_agents(stamp(datetime.now(timezone.utc))), [])
        self.assertIsNone(claim_agent(self.store, 'owner', self.agent))


class SearchAgentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = WorkspaceStore(Path(self.temp.name) / "workspace.db")
        self.searches = []
        self.lead = {"id": "retail-one", "title": "Desert Plaza", "url": "https://example.com/plaza",
                     "summary": "Asking price: $2,000,000. Building area: 15,000 SF."}
        def research(query):
            self.searches.append(query)
            return [self.lead]
        self.app = create_app(store=self.store, research_fn=research,
            analyze_fn=lambda *args, **kwargs: {"answer": "Investigate the rent roll.", "sources": []})
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.get("/api/status")
        self.owner = verify_browser_session(self.client.cookies.get("retailscope_session"), browser_signing_key())

    def create(self):
        response = self.client.post("/api/agents", json=CONFIG)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def wait_job(self, id):
        for _ in range(100):
            job = self.client.get("/api/research/" + id).json()
            if job["status"] not in {"running", "queued"}:
                return job
            time.sleep(.01)
        self.fail("Job did not finish")

    def test_run_produces_durable_inbox_read_state_and_report(self):
        agent = self.create()
        self.assertEqual(agent["cadence_hours"], 6)
        self.assertNotIn("run_id", agent)
        response = self.client.post(f'/api/agents/{agent["id"]}/run', json={})
        self.assertEqual(response.status_code, 202, response.text)
        job = self.wait_job(response.json()["id"])
        self.assertEqual(job["status"], "completed")
        inbox = self.client.get("/api/leads").json()["leads"]
        self.assertEqual(inbox[0]["agent_id"], agent["id"])
        self.assertIsNone(inbox[0]["read_at"])
        self.assertIn("facts", inbox[0])
        self.assertEqual(self.client.post('/api/leads/retail-one/read', json={}).status_code, 200)
        self.assertIsNotNone(self.client.get("/api/leads").json()["leads"][0]["read_at"])
        self.assertEqual(self.client.get('/api/conversations/' + job['id']).status_code, 200)
        # Rediscovery updates the source, not a duplicate inbox entry or reset receipt.
        self.store.upsert_leads(self.owner, [self.lead])
        self.assertEqual(len(self.client.get("/api/leads").json()["leads"]), 1)
        self.assertIsNotNone(self.client.get("/api/leads").json()["leads"][0]["read_at"])

    def test_owner_isolation_and_bounds(self):
        agent = self.create()
        stranger = TestClient(self.app)
        self.assertEqual(stranger.get('/api/agents').json(), {"agents": []})
        self.assertEqual(stranger.post(f'/api/agents/{agent["id"]}', json={"enabled": False}).status_code, 404)
        self.assertEqual(stranger.post(f'/api/agents/{agent["id"]}/run', json={}).status_code, 404)
        self.assertEqual(stranger.post('/api/leads/retail-one/read', json={}).status_code, 404)
        for _ in range(4):
            self.create()
        self.assertEqual(self.client.post('/api/agents', json=CONFIG).status_code, 409)
        self.assertEqual(self.client.post('/api/agents', json=CONFIG | {"max_price": 1}).status_code, 422)

    def test_internal_dispatch_requires_secret_and_claims_only_once(self):
        agent = self.create()
        self.store.patch_agent(self.owner, agent['id'], {"next_run_at": "2020-01-01T00:00:00.000000+00:00"})
        with patch.dict(os.environ, {"RETAILSCOPE_SCHEDULER_SECRET": "x" * 64}):
            self.assertEqual(self.client.post('/internal/schedules/tick', json={}).status_code, 401)
            self.assertEqual(len(self.searches), 0)
            headers = {"Authorization": "Bearer " + "x" * 64}
            response = self.client.post('/internal/schedules/tick', json={}, headers=headers)
            self.assertEqual(response.json(), {"ran": True, "status": "completed"})
            self.assertEqual(self.client.post('/internal/schedules/tick', json={}, headers=headers).json(), {"ran": False})
        self.assertEqual(len(self.searches), 1)
        saved = self.client.get('/api/agents').json()['agents'][0]
        self.assertEqual(saved['last_status'], 'completed')
        self.assertIsNotNone(saved['last_job_id'])

    def test_pause_and_quota_prevent_scheduled_provider_calls(self):
        agent = self.create()
        self.store.patch_agent(self.owner, agent['id'], {"next_run_at": "2020-01-01T00:00:00.000000+00:00"})
        headers = {"Authorization": "Bearer " + "x" * 64}
        with patch.dict(os.environ, {"RETAILSCOPE_SCHEDULER_SECRET": "x" * 64,
                                     "RETAILSCOPE_DAILY_CALL_LIMIT": "0"}):
            self.client.post(f'/api/agents/{agent["id"]}', json={"enabled": False})
            self.assertEqual(self.client.post('/internal/schedules/tick', json={}, headers=headers).json(), {"ran": False})
            self.client.post(f'/api/agents/{agent["id"]}', json={"enabled": True})
            self.assertTrue(self.client.post('/internal/schedules/tick', json={}, headers=headers).json()['deferred'])
        self.assertEqual(self.searches, [])
        self.assertEqual(self.store.get_agent(self.owner, agent['id'])['last_status'], 'failed')

    def test_interrupted_agent_is_not_permanently_running(self):
        agent = self.create()
        self.store.patch_agent(self.owner, agent['id'], {"last_status": "running",
            "lease_until": "2020-01-01T00:00:00.000000+00:00", "run_id": "old"})
        result = self.client.get('/api/agents').json()['agents'][0]
        self.assertEqual(result['last_status'], 'failed')
        self.assertIn('interrupted', result['last_error'])
        self.assertEqual(self.searches, [])

    def test_transient_admission_failure_keeps_due_run_retriable(self):
        agent = self.create()
        self.store.patch_agent(self.owner, agent['id'], {"next_run_at": "2020-01-01T00:00:00.000000+00:00"})
        headers = {"Authorization": "Bearer " + "x" * 64}
        with patch.dict(os.environ, {"RETAILSCOPE_SCHEDULER_SECRET": "x" * 64}):
            with patch.dict(os.environ, {"RETAILSCOPE_DAILY_CALL_LIMIT": "0"}):
                self.assertTrue(self.client.post('/internal/schedules/tick', json={}, headers=headers).json()['deferred'])
            self.assertEqual(self.store.get_agent(self.owner, agent['id'])['next_run_at'], "2020-01-01T00:00:00.000000+00:00")
            self.assertEqual(self.store.due_agents(stamp(datetime.now(timezone.utc))), [])
            self.store.patch_agent(self.owner, agent['id'], {'admission_after': None})
            result = self.client.post('/internal/schedules/tick', json={}, headers=headers).json()
            self.assertEqual(result, {"ran": True, "status": "completed"})
        self.assertEqual(len(self.searches), 1)

    def test_blocked_owner_does_not_starve_another_due_search(self):
        from app.web_api import UsageGate
        from fastapi import HTTPException
        first = self.create()
        second_client = TestClient(self.app)
        second_client.get('/api/status')
        second = second_client.post('/api/agents', json=CONFIG).json()
        second_owner = verify_browser_session(second_client.cookies.get('retailscope_session'), browser_signing_key())
        self.store.patch_agent(self.owner, first['id'], {'next_run_at': '2019-01-01T00:00:00.000000+00:00'})
        self.store.patch_agent(second_owner, second['id'], {'next_run_at': '2020-01-01T00:00:00.000000+00:00'})
        original = UsageGate.reserve
        def reserve(gate, owner, ip, kind):
            if owner == self.owner:
                raise HTTPException(429, 'Owner busy')
            return original(gate, owner, ip, kind)
        headers = {'Authorization': 'Bearer ' + 'x' * 64}
        with patch.dict(os.environ, {'RETAILSCOPE_SCHEDULER_SECRET': 'x' * 64}), patch.object(UsageGate, 'reserve', reserve):
            self.assertTrue(self.client.post('/internal/schedules/tick', json={}, headers=headers).json()['deferred'])
            self.assertEqual(self.client.post('/internal/schedules/tick', json={}, headers=headers).json(),
                             {'ran': True, 'status': 'completed'})
        self.assertEqual(len(self.searches), 1)
        self.assertEqual(self.store.list_leads(self.owner), [])
        self.assertEqual(len(self.store.list_leads(second_owner)), 1)

    def test_expiration_during_slow_enrollment_never_extends_lifetime(self):
        expires = int(self.client.cookies.get('retailscope_session').split('.')[2])
        now = time.time()
        with patch('app.web_api.time.time', return_value=now) as clock:
            def slow_listing(owner):
                clock.return_value = expires + 1
                return []
            with patch.object(self.store, 'list_agents', side_effect=slow_listing):
                result = self.client.post('/api/agents', json=CONFIG)
        self.assertEqual(result.status_code, 401)
        self.assertEqual(self.store.list_agents(self.owner), [])
