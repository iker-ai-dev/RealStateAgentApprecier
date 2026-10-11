"""Optional, manual Sentry telemetry with a static payload allowlist.

Existing application APIs are retained. Property facts, labels supplied by users,
AI arguments/results and exception objects never become telemetry. Initialize
once after configuration; a True return means local configuration, not delivery.
"""
from __future__ import annotations

import importlib
import importlib.util
import math
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime
from threading import Lock
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

try:
    import sentry_sdk
except Exception:  # Optional SDK; application operation does not depend on it.
    sentry_sdk = None

try:
    import streamlit as st
except Exception:
    st = None

RELEASE = "retailscope-ai@0.1.0"
ENVIRONMENT = "application"
ERROR_MESSAGE = "RetailScope AI operation failed"
TRANSACTIONS = {
    "retailscope_ai.answer": "ai.pipeline",
    "retailscope_ai.research": "agent.research",
    "retailscope_ai.calculation": "agent.calculation",
}
STEPS = {
    "db.load": "Load shopping center dataset",
    "app.valuation": "Build commercial real estate valuation",
    "ai.prompt.build": "Build appraisal prompt",
    "ai.fallback": "Deterministic local appraisal answer",
    "ai.chat_completions.create.snowflake_cortex": "Snowflake Cortex ai_complete",
    "mcp.initialize": "nimble.initialize",
    "mcp.tools": "nimble.discover_tools",
    "mcp.search": "nimble.search",
}
STATUSES = frozenset(("ok", "internal_error", "cancelled", "unknown_error",
                      "deadline_exceeded", "unavailable", "resource_exhausted"))
_UNSET = object()
_client = None
_init_lock = Lock()


def _hex(value: Any, size: int) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % size, value) is not None


def _time(value: Any) -> Any:
    if type(value) in (int, float):
        try:
            return value if math.isfinite(value) else None
        except OverflowError:
            return None
    if type(value) is str and re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)", value):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            pass
    return None


def _base(event: dict[str, Any]) -> dict[str, Any]:
    safe = {"platform": "python", "release": RELEASE, "environment": ENVIRONMENT}
    if _hex(event.get("event_id"), 32):
        safe["event_id"] = event["event_id"]
    for key in ("timestamp", "start_timestamp"):
        value = _time(event.get(key))
        if value is not None:
            safe[key] = value
    return safe


def _trace(raw: dict[str, Any], op: str) -> dict[str, Any]:
    safe = {"op": op}
    for key, size in (("trace_id", 32), ("span_id", 16), ("parent_span_id", 16)):
        if _hex(raw.get(key), size):
            safe[key] = raw[key]
    status = raw.get("status")
    if type(status) is str and status in STATUSES:
        safe["status"] = status
    return safe


def _scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Rebuild only the fixed manual error event, never a raw exception."""
    hint["attachments"] = []
    tags = event.get("tags")
    if not isinstance(tags, dict) or tags.get("retailscope.telemetry") != "operation_error":
        return None
    safe = _base(event)
    safe.update(level="error", tags={"retailscope.telemetry": "operation_error"},
                exception={"values": [{"type": "OperationError", "value": ERROR_MESSAGE}]},
                fingerprint=["retailscope.operation_error"])
    return safe


def _scrub_transaction(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Keep known operations, generated IDs, valid times and fixed statuses."""
    hint["attachments"] = []
    name = event.get("transaction")
    if type(name) is not str or name not in TRANSACTIONS:
        return None
    contexts = event.get("contexts")
    raw_trace = contexts.get("trace") if isinstance(contexts, dict) else None
    safe = _base(event)
    safe.update(type="transaction", transaction=name, transaction_info={"source": "custom"},
                contexts={"trace": _trace(raw_trace if isinstance(raw_trace, dict) else {},
                                           TRANSACTIONS[name])}, spans=[])
    spans = event.get("spans")
    for raw in spans if isinstance(spans, list) else []:
        if not isinstance(raw, dict):
            continue
        op = raw.get("op")
        if type(op) is not str or op not in STEPS or raw.get("description") != STEPS[op]:
            continue
        span = _trace(raw, op)
        span["description"] = STEPS[op]
        for key in ("start_timestamp", "timestamp"):
            value = _time(raw.get(key))
            if value is not None:
                span[key] = value
        safe["spans"].append(span)
    return safe


def _sentry_config() -> dict[str, Any]:
    try:
        return dict(st.secrets.get("sentry", {})) if st is not None else {}
    except Exception:
        return {}


def _is_warehouse() -> bool:
    if "_snowflake" in sys.modules:
        return True
    try:
        return importlib.util.find_spec("_snowflake") is not None
    except (ImportError, ValueError):
        return False


def _resolve_dsn(runtime: str, supplied: Any) -> Any:
    # Warehouse must use its managed generic-string alias. No TOML/env fallback.
    if runtime == "warehouse":
        try:
            return importlib.import_module("_snowflake").get_generic_secret_string("SENTRY_DSN")
        except Exception:
            return None
    if supplied is not _UNSET:
        return supplied
    config = _sentry_config()
    # Explicit empty configuration disables sending instead of falling through.
    if "dsn" in config:
        return config["dsn"]
    return os.getenv("SENTRY_DSN")


def _valid_dsn(value: Any) -> bool:
    if type(value) is not str or not value or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme == "https" and parsed.hostname and parsed.port in (None, 443)
                    and parsed.username and re.fullmatch(r"[A-Za-z0-9_-]+", parsed.username)
                    and parsed.password is None and re.fullmatch(r"/[0-9]+", parsed.path)
                    and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def _sample_rate(value: Any) -> float | None:
    try:
        rate = float(value) if type(value) in (int, float, str) else float("nan")
        return rate if math.isfinite(rate) and 0 <= rate <= 1 else None
    except (TypeError, ValueError, OverflowError):
        return None


def init_sentry(*, runtime: str | None = None, dsn: Any = _UNSET,
                traces_sample_rate: Any = _UNSET, transport: Any = None) -> bool:
    """Configure a private manual client after DSN configuration is available.

    No arguments preserves local Streamlit/env configuration. A warehouse uses
    the managed SENTRY_DSN alias exclusively. Explicit dsn=None disables local
    sending. transport is an offline test sink. Unknown/invalid config fails closed.
    Never adopt an SDK client initialized elsewhere with unsafe options.
    """
    global _client
    if sentry_sdk is None:
        return False
    selected = runtime or os.getenv("RETAILSCOPE_RUNTIME", "local")
    if selected not in ("local", "container", "warehouse"):
        _disable_owned_client()
        return False
    if _is_warehouse():
        selected = "warehouse"
    configured_dsn = _resolve_dsn(selected, dsn)
    if not _valid_dsn(configured_dsn):
        _disable_owned_client()
        return False
    config = {} if selected == "warehouse" else _sentry_config()
    configured_rate = traces_sample_rate
    if configured_rate is _UNSET:
        configured_rate = config.get("traces_sample_rate",
                                     0.2 if selected == "warehouse" else os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.2"))
    rate = _sample_rate(configured_rate)
    if rate is None:
        _disable_owned_client()
        return False
    with _init_lock:
        if _enabled():
            return True
        # Coexisting unowned SDK initialization cannot satisfy this policy.
        if sentry_sdk.is_initialized():
            return False
        try:
            sentry_sdk.init(
                dsn=configured_dsn, environment=ENVIRONMENT, release=RELEASE,
                server_name="retailscope-ai", debug=False, send_default_pii=False,
                include_local_variables=False, include_source_context=False,
                attach_stacktrace=False, max_breadcrumbs=0, max_request_body_size="never",
                default_integrations=False, auto_enabling_integrations=False, integrations=[],
                auto_session_tracking=False, traces_sample_rate=rate,
                trace_lifecycle="static", propagate_traces=False, trace_propagation_targets=[],
                profiles_sample_rate=0.0, profile_session_sample_rate=0.0,
                enable_logs=False, enable_metrics=False, send_client_reports=False,
                enable_backpressure_handling=False, stream_gen_ai_spans=False,
                before_send=_scrub_event, before_send_transaction=_scrub_transaction,
                before_breadcrumb=lambda breadcrumb, hint: None, transport=transport,
            )
            _client = sentry_sdk.get_client()
            return True
        except Exception:
            # SDK failures can contain a private DSN; never print/raise their details.
            _client = None
            return False


def _disable_owned_client() -> None:
    global _client
    with _init_lock:
        if _client is not None:
            _client.close()
            _client = None


def _enabled() -> bool:
    return (sentry_sdk is not None and _client is not None
            and sentry_sdk.is_initialized() and sentry_sdk.get_client() is _client)


def set_agent_context(property_id: str, property_name: str, data_source: str) -> None:
    """Compatibility no-op: never place property identifiers or facts in scope."""
    return None


def ai_track(name: str) -> Callable:
    """Compatibility identity decorator; never activate AI input/output capture."""
    def decorator(func: Callable) -> Callable:
        return func
    return decorator


@contextmanager
def start_span(op: str, name: str, data: dict[str, Any] | None = None) -> Iterator[Any]:
    """Time a known step; ignore data and never expose a mutable SDK span."""
    if not _enabled() or type(op) is not str or op not in STEPS or name != STEPS[op]:
        yield None
        return
    with sentry_sdk.start_span(op=op, name=STEPS[op]):
        yield None


@contextmanager
def start_agent_transaction(name: str, data: dict[str, Any] | None = None) -> Iterator[Any]:
    """Time a known workflow; ignore arbitrary data and inherited scope."""
    if not _enabled() or type(name) is not str or name not in TRANSACTIONS:
        yield None
        return
    with sentry_sdk.new_scope() as scope:
        scope.clear()
        with sentry_sdk.start_transaction(op=TRANSACTIONS[name], name=name):
            yield None


def capture_exception(exc: Exception) -> None:
    """Record a fixed operational failure without inspecting the exception."""
    if _enabled():
        sentry_sdk.capture_event({
            "level": "error", "tags": {"retailscope.telemetry": "operation_error"},
            "exception": {"values": [{"type": "OperationError", "value": ERROR_MESSAGE}]},
        })
