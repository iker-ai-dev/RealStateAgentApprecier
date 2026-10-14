import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.provider_health import ProviderHealth, _nimble, _result, _snowflake
from app.web_api import create_app
from app.workspace_store import WorkspaceStore


class ProviderHealthTests(unittest.TestCase):
    def test_unconfigured_does_not_connect(self):
        with patch("app.provider_health.get_snowflake_config", return_value={}), \
                patch("app.provider_health.get_snowpark_session") as connect, \
                patch.dict(os.environ, {"NIMBLE_API_KEY": ""}), \
                patch("app.provider_health.NimbleMCP") as nimble:
            self.assertEqual(_snowflake(time.monotonic() + 1)["state"], "not_configured")
            self.assertEqual(_nimble(time.monotonic() + 1)["state"], "not_configured")
            connect.assert_not_called()
            nimble.assert_not_called()

    def test_snowflake_select_only_and_cleanup(self):
        session = Mock()
        session.sql.return_value.collect.return_value = [(1,)]
        with patch("app.provider_health.get_snowflake_config", return_value={"account": "a"}), \
                patch("app.provider_health.get_managed_snowpark_session", return_value=None), \
                patch("app.provider_health.get_snowpark_session", return_value=session):
            self.assertEqual(_snowflake(time.monotonic() + 5)["state"], "connected")
            session.sql.assert_called_once_with("SELECT 1")
            session.close.assert_called_once()
            session.sql.return_value.collect.side_effect = RuntimeError("secret")
            with self.assertRaises(RuntimeError):
                _snowflake(time.monotonic() + 5)
            self.assertEqual(session.close.call_count, 2)

    def test_nimble_handshake_catalog_only(self):
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "private-key"}), \
                patch("app.provider_health.NimbleMCP") as factory:
            client = factory.return_value.__enter__.return_value
            self.assertEqual(_nimble(time.monotonic() + 5)["state"], "connected")
            client._search_tool.assert_called_once_with()
            client.search.assert_not_called()
            factory.return_value.__exit__.assert_called_once()

    def test_cache_and_redaction(self):
        good = Mock(return_value=_result("connected", "OK"))
        bad = Mock(side_effect=RuntimeError("password=supersecret"))
        health = ProviderHealth(probes={"snowflake": good, "nimble": bad})
        first = health.check()
        self.assertEqual(first["providers"]["nimble"]["state"], "error")
        self.assertNotIn("supersecret", str(first))
        self.assertEqual(set(first["providers"]["nimble"]), {"state", "checked_at", "message"})
        self.assertEqual(health.check(), first)
        first["providers"].clear()
        self.assertEqual(len(health.check()["providers"]), 2)
        good.assert_called_once()
        bad.assert_called_once()
        health._expires = 0
        health.check()
        self.assertEqual(good.call_count, 2)

    def test_concurrent_calls_single_flight(self):
        def probe(deadline):
            time.sleep(.03)
            return _result("connected", "OK")
        good = Mock(side_effect=probe)
        health = ProviderHealth(probes={"snowflake": good, "nimble": good})
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: health.check(), range(5)))
        self.assertEqual(good.call_count, 2)
        self.assertTrue(all(result == results[0] for result in results))

    def test_deadline_and_no_accumulating_stuck_workers(self):
        release = threading.Event()
        def probe(deadline):
            release.wait(1)
            return _result("connected", "OK")
        slow = Mock(side_effect=probe)
        health = ProviderHealth(timeout=.04, ttl=0, probes={"snowflake": slow, "nimble": slow})
        try:
            started = time.monotonic()
            result = health.check()
            self.assertLess(time.monotonic() - started, .3)
            self.assertTrue(all(p["state"] == "error" for p in result["providers"].values()))
            health.check()
            self.assertEqual(slow.call_count, 2)
        finally:
            release.set()
            for worker in health._workers.values():
                worker.join(1)

    def test_endpoint_session_post_only_and_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(store=WorkspaceStore(Path(directory) / "store.db"),
                             research_fn=lambda q: [], analyze_fn=lambda *a: {})
            with TestClient(app) as client, \
                    patch("app.provider_health.provider_health.check") as check, \
                    patch("app.web_api.get_snowflake_config", return_value={"account": "a"}), \
                    patch.dict(os.environ, {"NIMBLE_API_KEY": "key"}):
                check.return_value = {"providers": {name: _result("connected", "OK")
                                                    for name in ("snowflake", "nimble")}}
                self.assertEqual(client.post("/api/providers/check", json={}).status_code, 401)
                check.assert_not_called()
                status = client.get("/api/status").json()
                self.assertEqual(status["providers"], {"snowflake": True, "nimble": True})
                check.assert_not_called()
                self.assertIn(client.get("/api/providers/check").status_code, (404, 405))
                self.assertEqual(client.post("/api/providers/check", json={},
                    headers={"Origin": "https://evil.example"}).status_code, 403)
                response = client.post("/api/providers/check", json={})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), check.return_value)
                self.assertEqual(response.headers["cache-control"], "no-store")
                check.assert_called_once()


if __name__ == "__main__":
    unittest.main()
