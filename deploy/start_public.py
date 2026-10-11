"""Supervise the public demo UI, MCP and their shared HTTP gateway."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main():
    root = Path(__file__).resolve().parents[1]
    port = int(os.getenv("PORT", "10000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    config = (root / "deploy/nginx.conf").read_text().replace("__PORT__", str(port))
    config_path = Path("/tmp/retailscope-nginx.conf")
    config_path.write_text(config)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("SNOWFLAKE_", "NIMBLE_"))
           and key != "RETAILSCOPE_REQUIRE_SNOWFLAKE"}
    env["RETAILSCOPE_PUBLIC_DEMO"] = "true"
    commands = [
        [sys.executable, "-m", "streamlit", "run", "streamlit_app.py",
         "--server.port=8501", "--server.address=127.0.0.1", "--server.headless=true"],
        [sys.executable, "-m", "app.mcp_http"],
        ["nginx", "-c", str(config_path), "-g", "daemon off;"],
    ]
    children = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    exit_code = 0
    try:
        for command in commands:
            children.append(subprocess.Popen(command, cwd=root, env=env))
        while not stopping:
            failed = next((child for child in children if child.poll() is not None), None)
            if failed is not None:
                exit_code = failed.returncode or 1
                break
            time.sleep(0.25)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
