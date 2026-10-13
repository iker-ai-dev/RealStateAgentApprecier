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
from contextvars import ContextVar
from datetime import datetime
from threading import Lock
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

try:
    import sentry_sdk
    import sentry_sdk.logger as sentry_logger
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
    "retailscope_ai.mcp.list_centers": "mcp.server",
    "retailscope_ai.mcp.get_context": "mcp.server",
    "retailscope_ai.mcp.appraise": "mcp.server",
    "retailscope_ai.mcp.ask": "mcp.server",
    "retailscope_ai.mcp.list_leads": "mcp.server",
    "retailscope_ai.mcp.research": "mcp.server",
    "retailscope_ai.mcp.research_status": "mcp.server",
    "retailscope_ai.mcp.scenario": "mcp.server",
    "retailscope_ai.discovery": "agent.discovery",
    "retailscope_ai.discovery_source": "agent.discovery_source",
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
    "discovery.scan": "Scan cached public listing sources",
    "discovery.screen": "Screen cached public listing leads",
}
WORKFLOW_MESSAGES = {
    "started": "RetailScope workflow started",
    "completed": "RetailScope workflow completed",
    "failed": "RetailScope workflow failed",
}
STATUSES = frozenset(("ok", "internal_error", "cancelled", "unknown_error",
                      "deadline_exceeded", "unavailable", "resource_exhausted"))
USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "guardrail_tokens")
VALIDATION_REASONS = frozenset((
    "validation_unknown", "response_schema", "paragraph_count", "paragraph_text",
    "citation_limit", "citation_source", "missing_evidence", "metric_reference",
    "quotation_source", "quotation_mismatch", "numeric_provenance",
    "conversation_citations", "conversation_claim", "host_markup",
    "completion_type", "completion_metadata",
))
VALIDATION_MESSAGE = "RetailScope validation attempt"
VALIDATION_PREFIX = "retailscope.validation."
_UNSET = object()
_client = None
_environment = ENVIRONMENT
_release = RELEASE
_init_lock = Lock()
_error_captured = ContextVar("retailscope_error_captured", default=False)


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
    safe = {"platform": "python", "release": _release, "environment": _environment}
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


def _validation_fields(raw: Any, *, history: bool = False) -> dict[str, Any]:
    """Rebuild complete enum-only records; never coerce or stringify inputs."""
    if not isinstance(raw, dict):
        return {}
    safe = {}
    prefixes = [VALIDATION_PREFIX]
    if history:
        prefixes.extend(f"{VALIDATION_PREFIX}attempt_{n}." for n in (1, 2))
    for prefix in prefixes:
        attempt = raw.get(prefix + "attempt")
        outcome, reason = raw.get(prefix + "outcome"), raw.get(prefix + "reason")
        if (type(attempt) is not int or attempt not in (1, 2)
                or type(outcome) is not str or outcome not in ("passed", "rejected")
                or type(reason) is not str
                or (reason != "none" if outcome == "passed" else reason not in VALIDATION_REASONS)):
            continue
        safe.update({prefix + "attempt": attempt, prefix + "outcome": outcome,
                     prefix + "reason": reason})
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
    contexts = event.get("contexts")
    raw_trace = contexts.get("trace") if isinstance(contexts, dict) else None
    if isinstance(raw_trace, dict):
        op = raw_trace.get("op")
        if type(op) is str and op in (*TRANSACTIONS.values(), *STEPS):
            trace = _trace(raw_trace, op)
            if "trace_id" in trace and "span_id" in trace:
                safe["contexts"] = {"trace": trace}
                diagnostics = _validation_fields(raw_trace.get("data"), history=True)
                if diagnostics:
                    trace["data"] = diagnostics
                    if (diagnostics.get(VALIDATION_PREFIX + "outcome") == "rejected"
                            and diagnostics.get(VALIDATION_PREFIX + "attempt") == 2):
                        safe["fingerprint"] = ["retailscope.validation", diagnostics[VALIDATION_PREFIX + "reason"]]
    name = event.get("transaction")
    if type(name) is str and name in TRANSACTIONS:
        safe["transaction"] = name
    return safe


def _scrub_log(log: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """SDK before_send_log(Log, Hint): rebuild approved lifecycle logs only."""
    hint["attachments"] = []
    if not isinstance(log, dict) or not isinstance(log.get("attributes"), dict):
        return None
    attrs = log["attributes"]
    if attrs.get("retailscope.telemetry") == "validation":
        diagnostics = _validation_fields(attrs)
        if (log.get("body") != VALIDATION_MESSAGE or not diagnostics
                or attrs.get("retailscope.workflow") != "retailscope_ai.answer"
                or not _hex(log.get("trace_id"), 32) or not _hex(log.get("span_id"), 16)
                or type(log.get("time_unix_nano")) is not int or log["time_unix_nano"] <= 0):
            return None
        return {"body": VALIDATION_MESSAGE, "severity_text": "info", "severity_number": 9,
                "time_unix_nano": log["time_unix_nano"], "trace_id": log["trace_id"],
                "span_id": log["span_id"], "attributes": {
                    "retailscope.telemetry": "validation", "retailscope.workflow": "retailscope_ai.answer",
                    "sentry.environment": _environment, "sentry.release": _release, **diagnostics}}
    name, phase = attrs.get("retailscope.workflow"), attrs.get("retailscope.phase")
    if (attrs.get("retailscope.telemetry") != "workflow"
            or type(name) is not str or name not in TRANSACTIONS
            or type(phase) is not str or phase not in WORKFLOW_MESSAGES
            or log.get("body") != WORKFLOW_MESSAGES[phase]
            or not _hex(log.get("trace_id"), 32) or not _hex(log.get("span_id"), 16)
            or type(log.get("time_unix_nano")) is not int or log["time_unix_nano"] <= 0):
        return None
    failed = phase == "failed"
    return {
        "body": WORKFLOW_MESSAGES[phase],
        "severity_text": "error" if failed else "info",
        "severity_number": 17 if failed else 9,
        "time_unix_nano": log["time_unix_nano"],
        "trace_id": log["trace_id"], "span_id": log["span_id"],
        "attributes": {"retailscope.telemetry": "workflow", "retailscope.workflow": name,
                       "retailscope.phase": phase, "sentry.environment": _environment,
                       "sentry.release": _release,
                       **(_validation_fields(attrs) if name == "retailscope_ai.answer" and phase != "started" else {})},
    }


def _runtime_labels(runtime: str) -> tuple[str, str]:
    environment = "application"
    if runtime == "warehouse":
        environment = "snowflake"
    elif runtime == "render" or os.getenv("RENDER") == "true" or (
            runtime == "container" and os.getenv("RETAILSCOPE_PUBLIC_DEMO") == "true"):
        environment = "render-live" if os.getenv("RETAILSCOPE_APP_MODE") == "live" else "render-demo"
    release = RELEASE
    for key in ("RENDER_GIT_COMMIT", "GITHUB_SHA"):
        sha = os.getenv(key)
        if type(sha) is str and re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            release = sha.lower()
            break
    return environment, release


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
    if name == "retailscope_ai.answer" and isinstance(raw_trace, dict):
        raw_data = raw_trace.get("data")
        if isinstance(raw_data, dict):
            counters = {key: raw_data[key] for key in
                        ("retailscope.cortex.calls", *(f"retailscope.cortex.{k}" for k in USAGE_KEYS))
                        if type(raw_data.get(key)) is int and 0 <= raw_data[key] <= 1_000_000_000}
            if counters:
                counters["retailscope.billing_provider"] = "Snowflake Cortex"
                safe["contexts"]["trace"]["data"] = counters
            diagnostics = _validation_fields(raw_data, history=True)
            if diagnostics:
                safe["contexts"]["trace"].setdefault("data", {}).update(diagnostics)
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
    global _client, _environment, _release
    if sentry_sdk is None:
        return False
    selected = runtime or os.getenv("RETAILSCOPE_RUNTIME", "local")
    if selected not in ("local", "container", "warehouse", "render"):
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
            _environment, _release = _runtime_labels(selected)
            sentry_sdk.init(
                dsn=configured_dsn, environment=_environment, release=_release,
                server_name="retailscope-ai", debug=False, send_default_pii=False,
                include_local_variables=False, include_source_context=False,
                attach_stacktrace=False, max_breadcrumbs=0, max_request_body_size="never",
                default_integrations=False, auto_enabling_integrations=False, integrations=[],
                auto_session_tracking=False, traces_sample_rate=rate,
                trace_lifecycle="static", propagate_traces=False, trace_propagation_targets=[],
                profiles_sample_rate=0.0, profile_session_sample_rate=0.0,
                enable_logs=True, enable_metrics=False, send_client_reports=False,
                enable_backpressure_handling=False, stream_gen_ai_spans=False,
                before_send=_scrub_event, before_send_transaction=_scrub_transaction,
                before_send_log=_scrub_log,
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


def record_cortex_usage(usage: dict[str, Any]) -> None:
    """Aggregate provider-reported counters, never estimated cost or AI content."""
    if not _enabled() or not isinstance(usage, dict):
        return
    try:
        current = sentry_sdk.get_current_scope().span
        transaction = current.containing_transaction if current is not None else None
        if transaction is None or transaction.name != "retailscope_ai.answer":
            return
        data = transaction.get_trace_context().get("data", {})
        calls = data.get("retailscope.cortex.calls", 0)
        transaction.set_data("retailscope.cortex.calls", calls + 1)
        for key in USAGE_KEYS:
            attribute = f"retailscope.cortex.{key}"
            value = usage.get(key)
            # A missing counter in any call makes the aggregate unknown.
            previous = data.get(attribute, 0 if calls == 0 else None)
            total = (previous + value if type(previous) is int and type(value) is int
                     and 0 <= value <= 1_000_000_000 else None)
            transaction.set_data(attribute, total)
    except Exception:
        pass  # Optional telemetry must never change inference behavior.


def record_validation(attempt: int, outcome: str, reason: str = "none") -> None:
    """Record at most two validation results per answer, without exception content.

    Callers assign reason codes at validation raise sites, never by parsing messages.
    Outcomes are passed/rejected; rejection reasons are VALIDATION_REASONS and a
    passed result always has reason none. Unknown rejection reasons become
    validation_unknown. Invalid attempts/outcomes and duplicate attempts are ignored.
    Attempts are control-flow
    ordinals only; financial numbers and other arbitrary numeric fields are forbidden.
    """
    try:
        if not _enabled():
            return
        if type(reason) is not str or reason not in VALIDATION_REASONS:
            reason = "validation_unknown"
        fields = _validation_fields({VALIDATION_PREFIX + "attempt": attempt,
                                     VALIDATION_PREFIX + "outcome": outcome,
                                     VALIDATION_PREFIX + "reason": "none" if outcome == "passed" else reason})
        if not fields:
            return
        current = sentry_sdk.get_current_scope().span
        transaction = current.containing_transaction if current is not None else None
        if transaction is None or transaction.name != "retailscope_ai.answer":
            return
        data = transaction.get_trace_context().get("data", {})
        if attempt <= data.get(VALIDATION_PREFIX + "attempt", 0):
            return
        for key, value in fields.items():
            transaction.set_data(key, value)
            transaction.set_data(key.replace(VALIDATION_PREFIX, f"{VALIDATION_PREFIX}attempt_{attempt}."), value)
        sentry_logger.info(VALIDATION_MESSAGE, attributes={
            "retailscope.telemetry": "validation", "retailscope.workflow": "retailscope_ai.answer", **fields})
    except Exception:
        pass  # Diagnostics cannot invalidate a paid result or trigger a retry.


@contextmanager
def start_span(op: str, name: str, data: dict[str, Any] | None = None) -> Iterator[Any]:
    """Time a known step; ignore data and never expose a mutable SDK span."""
    if not _enabled() or type(op) is not str or op not in STEPS or name != STEPS[op]:
        yield None
        return
    with sentry_sdk.start_span(op=op, name=STEPS[op]) as span:
        yield None
        if span.status is None:
            span.set_status("ok")


@contextmanager
def start_agent_transaction(name: str, data: dict[str, Any] | None = None) -> Iterator[Any]:
    """Time a known workflow; ignore arbitrary data and inherited scope."""
    if not _enabled() or type(name) is not str or name not in TRANSACTIONS:
        yield None
        return
    with sentry_sdk.new_scope() as scope:
        scope.clear()
        with sentry_sdk.start_transaction(op=TRANSACTIONS[name], name=name) as transaction:
            token = _error_captured.set(False)
            try:
                _workflow_log(name, "started")
                try:
                    yield None
                except Exception as exc:
                    transaction.set_status("internal_error")
                    if not _error_captured.get():
                        capture_exception(exc)
                    _workflow_log(name, "failed")
                    raise
                else:
                    if transaction.status is None:
                        transaction.set_status("ok")
                    _workflow_log(name, "completed" if transaction.status == "ok" else "failed")
            finally:
                _error_captured.reset(token)


def _workflow_log(name: str, phase: str) -> None:
    # Static strings only; the hook also removes SDK/scope-added attributes.
    try:
        current = sentry_sdk.get_current_scope().span
        transaction = current.containing_transaction if current is not None else None
        diagnostics = (_validation_fields(transaction.get_trace_context().get("data"))
                       if transaction is not None and name == "retailscope_ai.answer" and phase != "started" else {})
        logger = sentry_logger.error if phase == "failed" else sentry_logger.info
        logger(WORKFLOW_MESSAGES[phase], attributes={
            "retailscope.telemetry": "workflow", "retailscope.workflow": name,
            "retailscope.phase": phase, **diagnostics,
        })
    except Exception:
        pass  # Optional telemetry must not change application behavior.


def capture_exception(exc: Exception) -> None:
    """Record a fixed operational failure without inspecting the exception."""
    if _enabled():
        try:
            span = sentry_sdk.get_current_scope().span
            if span is not None:
                transaction = span.containing_transaction
                if transaction is not None and transaction.name in TRANSACTIONS:
                    span.set_status("internal_error")
                    transaction.set_status("internal_error")
                    _error_captured.set(True)
            sentry_sdk.capture_event({
                "level": "error", "tags": {"retailscope.telemetry": "operation_error"},
                "exception": {"values": [{"type": "OperationError", "value": ERROR_MESSAGE}]},
                "contexts": {"trace": {
                    **(span.get_trace_context() if span is not None else {}),
                    "data": (_validation_fields(transaction.get_trace_context().get("data"), history=True)
                             if span is not None and transaction is not None
                             and transaction.name == "retailscope_ai.answer" else {}),
                }},
            })
        except Exception:
            pass
