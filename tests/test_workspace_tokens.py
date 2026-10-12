import os
import unittest
from unittest.mock import patch

from app.workspace_tokens import (issue_token, verify_token, issue_browser_session,
                                  verify_browser_session)


class WorkspaceTokenTests(unittest.TestCase):
    def test_browser_cookie_requires_separate_signature(self):
        key = b"s" * 32
        owner = "a" * 64
        token = issue_browser_session(owner, key)
        self.assertEqual(verify_browser_session(token, key), owner)
        self.assertIsNone(verify_browser_session(owner, key))
        self.assertIsNone(verify_browser_session(token.replace("rsb1", "rs1"), key))
        self.assertIsNone(verify_browser_session(token, b"t" * 32))
    @patch.dict(os.environ, {"RETAILSCOPE_MCP_SIGNING_KEY": "a" * 64})
    def test_scoped_signed_and_expiring(self):
        with patch("app.workspace_tokens.time.time", return_value=1790000000):
            result = issue_token("b" * 64)
            self.assertEqual(verify_token(result["token"]), "b" * 64)
            with self.assertRaises(ValueError):
                verify_token(result["token"].replace("b", "c", 1))
        with patch("app.workspace_tokens.time.time", return_value=result["expires_at"]):
            with self.assertRaises(ValueError):
                verify_token(result["token"])

    @patch.dict(os.environ, {"RETAILSCOPE_MCP_SIGNING_KEY": ""})
    def test_unconfigured_fails_closed(self):
        with self.assertRaises(RuntimeError):
            issue_token("b" * 64)
