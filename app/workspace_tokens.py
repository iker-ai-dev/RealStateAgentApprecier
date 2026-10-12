"""Expiring, workspace-scoped bearer tokens for coding-agent access."""
import hashlib
import hmac
import os
import re
import time
from pathlib import Path
import secrets

TOKEN_LIFETIME = 7 * 24 * 60 * 60
SESSION_LIFETIME = 30 * 24 * 60 * 60


def browser_signing_key() -> bytes:
    value = os.getenv("RETAILSCOPE_SESSION_SIGNING_KEY", "")
    if not value and os.getenv("RENDER") == "true":
        value = os.getenv("RETAILSCOPE_MCP_SIGNING_KEY", "")
    if len(value) >= 32:
        return value.encode()
    if os.getenv("RENDER") == "true":
        raise RuntimeError("A persistent session signing key is required for the hosted workspace.")
    path = Path(".retailscope/session-signing.key")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(secrets.token_bytes(32))
    key = path.read_bytes()
    if len(key) != 32:
        raise RuntimeError("Invalid local session signing key.")
    return key


def issue_browser_session(owner: str, key: bytes) -> str:
    expires = int(time.time()) + SESSION_LIFETIME
    payload = f"rsb1.{owner}.{expires}"
    signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def verify_browser_session(cookie: str, key: bytes) -> str | None:
    match = re.fullmatch(r"rsb1\.([a-f0-9]{64})\.([0-9]{10})\.([a-f0-9]{64})", cookie)
    if not match:
        return None
    owner, expiration, signature = match.groups()
    expected = hmac.new(key, cookie.rsplit(".", 1)[0].encode(), hashlib.sha256).hexdigest()
    now = int(time.time())
    if not hmac.compare_digest(signature, expected) or not now < int(expiration) <= now + SESSION_LIFETIME:
        return None
    return owner


def _key():
    value = os.getenv("RETAILSCOPE_MCP_SIGNING_KEY", "")
    if len(value) < 32:
        raise RuntimeError("MCP workspace access is not configured.")
    return value.encode()


def issue_token(owner: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{64}", owner):
        raise ValueError("Invalid workspace identity.")
    expires = int(time.time()) + TOKEN_LIFETIME
    payload = f"rs1.{owner}.{expires}"
    signature = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()
    return {"token": payload + "." + signature, "expires_at": expires}


def verify_token(token: str) -> str:
    if not isinstance(token, str) or len(token) > 256:
        raise ValueError("Invalid workspace token.")
    match = re.fullmatch(r"rs1\.([a-f0-9]{64})\.([0-9]{10})\.([a-f0-9]{64})", token)
    if not match:
        raise ValueError("Invalid workspace token.")
    owner, expiration, signature = match.groups()
    payload = token.rsplit(".", 1)[0]
    expected = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("Invalid workspace token.")
    now = int(time.time())
    if not now < int(expiration) <= now + TOKEN_LIFETIME:
        raise ValueError("Expired workspace token.")
    return owner
