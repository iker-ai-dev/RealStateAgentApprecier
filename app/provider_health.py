"""Explicit, read-only connector probes; never searches or model inference."""
from copy import deepcopy
from datetime import datetime, timezone
from queue import Empty, Queue
import os
import threading
import time

from app.data_access import get_snowflake_config, get_snowpark_session, get_managed_snowpark_session
from nimble_mcp import NimbleMCP

CACHE_SECONDS = 60
CHECK_SECONDS = 24
PROVIDERS = ("snowflake", "nimble")


def _result(state, message):
    return {"state": state, "checked_at": datetime.now(timezone.utc).isoformat(),
            "message": message}


def _snowflake(deadline):
    cfg = get_snowflake_config()
    if not cfg.get("account"):
        return _result("not_configured", "Snowflake credentials are not configured.")
    managed = get_managed_snowpark_session()
    session = managed if managed is not None else get_snowpark_session(cfg)
    try:
        remaining = max(1, int(deadline - time.monotonic()))
        rows = session.sql("SELECT 1").collect(statement_params={
            "STATEMENT_TIMEOUT_IN_SECONDS": str(remaining)})
        if not rows or rows[0][0] != 1:
            raise ValueError("Unexpected probe result")
        return _result("connected", "Snowflake SELECT 1 succeeded; inference was not tested.")
    finally:
        if managed is None:
            session.close()


def _nimble(deadline):
    key = os.getenv("NIMBLE_API_KEY", "").strip()
    if not key:
        return _result("not_configured", "Nimble credentials are not configured.")
    with NimbleMCP(key, timeout=(3, 3), request_deadline=8,
                   workflow_deadline=deadline, max_tool_pages=3) as client:
        # Reuse the adapter's bounded catalog pagination without calling search.
        client._search_tool()
        return _result("connected", "Nimble MCP initialized and search tool listed; no search was run.")


class ProviderHealth:
    def __init__(self, *, timeout=CHECK_SECONDS, ttl=CACHE_SECONDS, probes=None):
        self.timeout, self.ttl = timeout, ttl
        self.probes = probes or {"snowflake": _snowflake, "nimble": _nimble}
        self._lock = threading.Lock()
        self._cache = None
        self._expires = 0
        self._workers = {}

    def check(self):
        deadline = time.monotonic() + self.timeout
        if not self._lock.acquire(timeout=self.timeout):
            return {"providers": {name: _result("error", "Connection check is busy; try again shortly.")
                                  for name in PROVIDERS}}
        try:
            if self._cache is not None and time.monotonic() < self._expires:
                return deepcopy(self._cache)
            results, pending, queue = {}, set(), Queue()

            def run(name):
                try:
                    value = self.probes[name](deadline)
                except Exception:
                    # SDK exceptions can contain credentials or remote response bodies.
                    value = _result("error", "Connection check failed. Check credentials and provider availability.")
                queue.put((name, value))

            for name in PROVIDERS:
                worker = self._workers.get(name)
                if worker is not None and worker.is_alive():
                    results[name] = _result("error", "Previous connection check is still finishing; try again later.")
                    continue
                # SDK/DNS calls cannot always be interrupted. Keep at most one
                # worker per provider, even after a response deadline expires.
                worker = threading.Thread(target=run, args=(name,), daemon=True,
                                          name="provider-health-" + name)
                self._workers[name] = worker
                pending.add(name)
                worker.start()
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    name, value = queue.get(timeout=remaining)
                except Empty:
                    break
                pending.remove(name)
                results[name] = value
            for name in pending:
                results[name] = _result("error", "Connection check timed out; try again later.")
            self._cache = {"providers": results}
            self._expires = time.monotonic() + self.ttl
            return deepcopy(self._cache)
        finally:
            self._lock.release()


provider_health = ProviderHealth()
