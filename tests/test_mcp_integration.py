import os
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from streamlit.util import calc_hash

from app import agent_api, data_access, observability

ROOT = Path(__file__).resolve().parents[1]


class MCPIntegrationTests(unittest.TestCase):
    def test_demo_install_can_list_and_appraise(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            agent_api, "get_snowflake_config", return_value={}
        ), patch.object(data_access, "get_snowpark_session", side_effect=RuntimeError("offline")):
            self.assertEqual(len(agent_api.list_properties()), 3)
            self.assertGreater(agent_api.appraise_property("SC-001")["valuation"]["blended_value"], 0)

    def test_configured_clients_do_not_receive_demo_inventory_on_failure(self):
        with patch.object(agent_api, "get_snowflake_config", return_value={"account": "configured"}), \
                patch.object(data_access, "get_snowpark_session", side_effect=RuntimeError("offline")):
            with self.assertRaisesRegex(RuntimeError, "Demo data was not substituted"):
                agent_api.list_properties()

    def test_explicit_live_mode_fails_without_configuration(self):
        with patch.dict(os.environ, {"RETAILSCOPE_REQUIRE_SNOWFLAKE": "true"}, clear=True), \
                patch.object(agent_api, "get_snowflake_config", return_value={}), \
                patch.object(data_access, "get_snowpark_session", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                agent_api.appraise_property("SC-001")

    def test_landing_route_has_install_commands_in_both_entrypoints(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(observability, "init_sentry", return_value=False):
            for entry in ("streamlit_app.py", "snowflake_streamlit_app.py"):
                with self.subTest(entry=entry):
                    app = AppTest.from_file(str(ROOT / entry), default_timeout=20)
                    app.secrets = {}
                    app.run()
                    # AppTest.switch_page currently supports only file-based pages.
                    app._page_hash = calc_hash("landing")
                    app.run()
                    self.assertEqual(len(app.exception), 0)
                    commands = "\n".join(x.value for x in app.code)
                    for client in ("codex", "claude", "opencode"):
                        self.assertIn(client + " mcp add", commands)
                    self.assertIn('"mcpServers"', commands)


if __name__ == "__main__":
    unittest.main()
