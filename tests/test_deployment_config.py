import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy.start_public import nginx_configuration

ROOT = Path(__file__).resolve().parents[1]


class DeploymentConfigTests(unittest.TestCase):
    @patch.dict(os.environ, {"RENDER": "false"})
    def test_local_ignores_user_forwarded_headers(self):
        config = nginx_configuration(ROOT, 18765, True)
        self.assertNotIn("__", config)
        self.assertNotIn("$http_cf_connecting_ip", config)
        self.assertIn("127.0.0.1:8501/healthz", config)

    @patch.dict(os.environ, {"RENDER": "true"})
    def test_hosted_uses_overwritten_edge_header(self):
        config = nginx_configuration(ROOT, 10000, True)
        self.assertIn("map $http_cf_connecting_ip $public_client_ip", config)
        self.assertNotIn("$http_x_forwarded_for", config)

    @unittest.skipUnless(shutil.which("nginx"), "Nginx binary is not installed")
    def test_nginx_accepts_generated_configuration(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf") as config:
            config.write(nginx_configuration(ROOT, 18765, True))
            config.flush()
            result = subprocess.run([shutil.which("nginx"), "-t", "-c", config.name],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
