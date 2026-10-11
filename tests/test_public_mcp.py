import os
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from app import data_access
from app.mcp_http import configure_public_http, mcp


class PublicMCPTests(unittest.TestCase):
    def test_public_mode_never_uses_snowflake_config_or_session(self):
        with patch.dict(os.environ, {"RETAILSCOPE_PUBLIC_DEMO": "true",
                                     "SNOWFLAKE_ACCOUNT": "private"}), \
                patch.object(data_access, "get_snowpark_session", side_effect=AssertionError("private access")):
            self.assertEqual(data_access.get_snowflake_config(), {})
            self.assertEqual(data_access.load_dataset().source, "Sample CSV data")
            with self.assertRaisesRegex(RuntimeError, "Public demo"):
                data_access.load_dataset(require_snowflake=True)

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
