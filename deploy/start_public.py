"""Supervise the workspace API, MCP and their shared HTTP gateway."""
import base64
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def nginx_configuration(root, port, live):
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    config = (root / "deploy/nginx.conf").read_text().replace("__PORT__", str(port))
    config = config.replace("__HEALTH_PORT__", "8501" if live else "8502")
    config = config.replace("__MCP_PORT__", "8501" if live else "8502")
    # Render's Cloudflare edge overwrites this header, unlike client-supplied XFF.
    # Other deployments must not trust it; use the actual socket peer there.
    config = config.replace("__CLIENT_IP_SOURCE__",
                            "$http_cf_connecting_ip" if os.getenv("RENDER") == "true" else "$remote_addr")
    return config


def main():
    root = Path(__file__).resolve().parents[1]
    port = int(os.getenv("PORT", "10000"))
    live = os.getenv("RETAILSCOPE_APP_MODE") == "live"
    config = nginx_configuration(root, port, live)
    config_path = Path("/tmp/retailscope-nginx.conf")
    config_path.write_text(config)
    demo_env = {key: value for key, value in os.environ.items()
           if not key.startswith(("SNOWFLAKE_", "NIMBLE_"))
           and key != "RETAILSCOPE_REQUIRE_SNOWFLAKE"}
    demo_env["RETAILSCOPE_PUBLIC_DEMO"] = "true"
    env = dict(os.environ) if live else demo_env
    key_path = None
    if live:
        env.pop("RETAILSCOPE_PUBLIC_DEMO", None)
        # Render supplies secrets as environment variables; the connector expects
        # a private key file. Keep the material out of the image and command line.
        encoded_key = env.pop("SNOWFLAKE_PRIVATE_KEY_B64", None)
        if encoded_key:
            key_path = Path("/tmp/retailscope-snowflake.p8")
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(base64.b64decode(encoded_key, validate=True))
            env["SNOWFLAKE_PRIVATE_KEY_FILE"] = str(key_path)
    commands = [
        ([sys.executable, "-m", "uvicorn", "app.web_api:create_app", "--factory",
          "--host", "127.0.0.1", "--port", "8501", "--no-access-log",
          "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"] if live else
         [sys.executable, "-m", "streamlit", "run", "streamlit_app.py",
          "--server.port=8501", "--server.address=127.0.0.1", "--server.headless=true"]),
    ]
    if not live:
        commands.append([sys.executable, "-m", "app.mcp_http"])
    commands.append(["nginx", "-c", str(config_path), "-g", "daemon off;"])
    children = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    exit_code = 0
    try:
        for index, command in enumerate(commands):
            # Only the application process receives live provider credentials.
            child_env = env if index == 0 else demo_env
            children.append(subprocess.Popen(command, cwd=root, env=child_env))
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
        if key_path is not None:
            key_path.unlink(missing_ok=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
