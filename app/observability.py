from __future__ import annotations

import os
from contextlib import contextmanager, nullcontext
from typing import Any, Callable, Iterator

import streamlit as st


try:
    import sentry_sdk
except Exception:  # pragma: no cover - optional dependency guard
    sentry_sdk = None

try:
    from sentry_sdk.ai.monitoring import ai_track as _sentry_ai_track
except Exception:  # pragma: no cover - SDK version guard
    _sentry_ai_track = None


SENSITIVE_KEYS = {
    "password",
    "token",
    "secret",
    "dsn",
    "authorization",
    "cookie",
    "set-cookie",
    "question",
    "prompt",
    "messages",
    "tenant",
    "tenants",
}


def init_sentry() -> bool:
    """Initialize Sentry once when DSN configuration is present."""
    if sentry_sdk is None:
        return False

    if sentry_sdk.is_initialized():
        return True

    config = _sentry_config()
    dsn = config.get("dsn") or os.getenv("SENTRY_DSN")
    if not dsn:
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=config.get("environment") or os.getenv("SENTRY_ENVIRONMENT", "local"),
        release=config.get("release") or os.getenv("SENTRY_RELEASE"),
        traces_sample_rate=float(config.get("traces_sample_rate", os.getenv("SENTRY_TRACES_SAMPLE_RATE", 0.2))),
        profiles_sample_rate=float(config.get("profiles_sample_rate", os.getenv("SENTRY_PROFILES_SAMPLE_RATE", 0.0))),
        send_default_pii=False,
        before_send=_scrub_event,
    )
    return True


def set_agent_context(property_id: str, property_name: str, data_source: str) -> None:
    if sentry_sdk is None or not sentry_sdk.is_initialized():
        return

    sentry_sdk.set_tag("agent.kind", "retailscope-ai")
    sentry_sdk.set_tag("property.id", property_id)
    sentry_sdk.set_tag("data.source", data_source)
    sentry_sdk.set_context(
        "property",
        {
            "id": property_id,
            "name": property_name,
            "data_source": data_source,
        },
    )


def ai_track(name: str) -> Callable:
    if _sentry_ai_track is not None:
        return _sentry_ai_track(name)

    def decorator(func: Callable) -> Callable:
        return func

    return decorator


@contextmanager
def start_span(op: str, name: str, data: dict[str, Any] | None = None) -> Iterator[Any]:
    if sentry_sdk is None or not sentry_sdk.is_initialized():
        yield None
        return

    with sentry_sdk.start_span(op=op, name=name) as span:
        if data:
            for key, value in data.items():
                span.set_data(key, value)
        yield span


@contextmanager
def start_agent_transaction(name: str, data: dict[str, Any] | None = None) -> Iterator[Any]:
    if sentry_sdk is None or not sentry_sdk.is_initialized():
        yield None
        return

    with sentry_sdk.start_transaction(op="ai.pipeline", name=name) as transaction:
        if data:
            for key, value in data.items():
                transaction.set_data(key, value)
        yield transaction


def capture_exception(exc: Exception) -> None:
    if sentry_sdk is not None and sentry_sdk.is_initialized():
        sentry_sdk.capture_exception(exc)


def _sentry_config() -> dict[str, Any]:
    try:
        return dict(st.secrets.get("sentry", {}))
    except Exception:
        return {}


def _scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    return _scrub_value(event)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                clean[key] = "[Filtered]"
            else:
                clean[key] = _scrub_value(item)
        return clean
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    return value
