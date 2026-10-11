import os
import sys
import unittest
from types import ModuleType
from unittest.mock import patch

from starlette.testclient import TestClient

from app import data_access
from app import research_ui
from app.mcp_http import configure_public_http, mcp


class PublicMCPTests(unittest.TestCase):
    def test_public_demo_never_reads_live_research_credentials(self):
        with patch.dict(os.environ, {"RETAILSCOPE_PUBLIC_DEMO": "true", "NIMBLE_API_KEY": "private"}), \
                patch.object(research_ui.importlib, "import_module", side_effect=AssertionError("secret lookup")):
            self.assertEqual(research_ui._nimble_key(), "")
            self.assertIsNone(research_ui.render_research("Phoenix"))
            self.assertIsNone(research_ui.render_listing_search())

    def test_public_mode_never_uses_snowflake_config_or_session(self):
        with patch.dict(os.environ, {"RETAILSCOPE_PUBLIC_DEMO": "true",
                                     "SNOWFLAKE_ACCOUNT": "private"}), \
                patch.object(data_access, "get_snowpark_session") as session, \
                patch.object(data_access, "_warn") as warn:
            self.assertEqual(data_access.get_snowflake_config(), {})
            self.assertEqual(data_access.load_dataset().source, "Sample CSV data")
            with self.assertRaisesRegex(RuntimeError, "Public demo"):
                data_access.load_dataset(require_snowflake=True)
            session.assert_not_called()
            warn.assert_not_called()

    def test_unconfigured_sample_data_does_not_attempt_snowflake_or_warn(self):
        for cfg in ({}, {"property_table": "CUSTOM_PROPERTIES", "cortex_model": "demo"}):
            with self.subTest(cfg=cfg), \
                    patch.dict(os.environ, {"RETAILSCOPE_PUBLIC_DEMO": "false"}), \
                    patch.object(data_access, "get_snowflake_config", return_value=cfg), \
                    patch.dict(sys.modules, {"_snowflake": None}), \
                    patch.object(data_access.importlib.util, "find_spec", return_value=None), \
                    patch.object(data_access, "get_snowpark_session") as session, \
                    patch.object(data_access, "_warn") as warn:
                # A blocked module is not a hosted runtime.
                del sys.modules["_snowflake"]
                dataset = data_access.load_dataset()
                self.assertEqual(dataset.source, "Sample CSV data")
                self.assertFalse(dataset.properties.empty)
                with self.assertRaisesRegex(RuntimeError, "not configured.*not substituted"):
                    data_access.load_dataset(require_snowflake=True)
                session.assert_not_called()
                warn.assert_not_called()

    def test_configured_private_failure_remains_visible(self):
        with patch.dict(os.environ, {"RETAILSCOPE_PUBLIC_DEMO": "false"}), \
                patch.object(data_access, "get_snowflake_config", return_value={"account": "private"}), \
                patch.object(data_access, "get_snowpark_session", side_effect=RuntimeError("connection failed")) as session, \
                patch.object(data_access, "_warn") as warn:
            self.assertEqual(data_access.load_dataset().source, "Sample CSV data")
            warn.assert_called_once_with(
                "Snowflake data could not be loaded; explicitly using synthetic sample data."
            )
            with self.assertRaisesRegex(RuntimeError, "Snowflake data could not be loaded.*not substituted"):
                data_access.load_dataset(require_snowflake=True)
            self.assertEqual(session.call_count, 2)
            self.assertEqual(warn.call_count, 1)

    def test_hosted_runtime_still_uses_active_session_without_local_config(self):
        with patch.dict(os.environ, {"RETAILSCOPE_PUBLIC_DEMO": "false"}), \
                patch.object(data_access, "get_snowflake_config", return_value={}), \
                patch.dict(sys.modules, {"_snowflake": ModuleType("_snowflake")}), \
                patch.object(data_access, "get_snowpark_session") as session, \
                patch.object(data_access, "_warn") as warn:
            frame = data_access.pd.DataFrame({"PROPERTY_ID": ["test"]})
            session.return_value.table.return_value.limit.return_value.to_pandas.return_value = frame
            dataset = data_access.load_dataset(require_snowflake=True)
            self.assertEqual(dataset.source, "Snowflake")
            self.assertEqual(list(dataset.properties.columns), ["property_id"])
            session.assert_called_once_with({})
            warn.assert_not_called()

    def test_stateless_http_and_host_origin_protection(self):
        with patch.dict(os.environ, {}, clear=True):
            configure_public_http()
            with TestClient(mcp.streamable_http_app(), base_url="http://localhost") as client:
                self.assertEqual(client.get("/healthz").status_code, 200)
                payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "1"}}}
                headers = {"Accept": "application/json, text/event-stream"}
                result = client.post("/mcp", json=payload, headers=headers)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["result"]["serverInfo"]["name"], "RetailScope AI")
                self.assertNotIn("mcp-session-id", result.headers)
                self.assertEqual(client.post("/mcp", json=payload, headers={
                    **headers, "Host": "evil.example"}).status_code, 421)
                self.assertEqual(client.post("/mcp", json=payload, headers={
                    **headers, "Origin": "https://evil.example"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
