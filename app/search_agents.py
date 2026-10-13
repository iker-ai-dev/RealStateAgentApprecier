"""Durable six-hour search subscriptions, claimed before any paid work starts."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import secrets

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now():
    return datetime.now(timezone.utc)


def scheduler_key(session_key: bytes) -> str:
    """Domain-separated dispatch-only credential; never a browser session key."""
    explicit = os.getenv("RETAILSCOPE_SCHEDULER_SECRET")
    if explicit is not None:
        return explicit if len(explicit) >= 32 else ""
    return hmac.new(session_key, b"retailscope:background-dispatch:v1", hashlib.sha256).hexdigest()


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def next_slot(now):
    """UTC 00:17, 06:17, 12:17, 18:17, aligned to the scheduled dispatcher."""
    now = now.astimezone(timezone.utc)
    slot = now.replace(hour=(now.hour // 6) * 6,
                                             minute=17, second=0, microsecond=0)
    if slot <= now:
        slot += timedelta(hours=6)
    return stamp(slot)


class SearchAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    city: str = Field(min_length=2, max_length=100)
    min_price: int = Field(ge=0, le=1_000_000_000)
    max_price: int = Field(gt=0, le=1_000_000_000)
    strategy: str = Field(min_length=2, max_length=200)

    @field_validator("name", "city", "strategy")
    @classmethod
    def clean(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Enter a valid search value.")
        return value

    @model_validator(mode="after")
    def price_order(self):
        if self.max_price <= self.min_price:
            raise ValueError("Maximum budget must be greater than minimum budget.")
        return self

    def as_agent(self):
        now = utc_now()
        query = (f"Research for me open-air shopping centers and strip centers for sale in {self.city}, "
                 f"United States, with an asking price from ${self.min_price:,} to ${self.max_price:,}. "
                 f"Focus on {self.strategy}. Find original property listings and report asking price, "
                 "area, occupancy, cap rate, NOI, tenant rents, population and household income when "
                 "sources provide them. Identify missing evidence; do not assume a property fits the budget.")
        return {**self.model_dump(), "id": secrets.token_hex(16), "query": query,
                "enabled": True, "cadence_hours": 6, "created_at": stamp(now),
                "expires_at": stamp(now + timedelta(days=30)),
                "next_run_at": next_slot(now), "last_run_at": None, "last_status": None,
                "last_job_id": None, "last_error": None, "lease_until": None, "run_id": None,
                "admission_after": None}


class AgentToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


def claim_agent(store, owner, agent, *, scheduled=False, now=None):
    now = now or utc_now()
    when = stamp(now)
    if agent.get("expires_at", "") <= when:
        return None
    if scheduled and (not agent["enabled"] or agent["next_run_at"] > when):
        return None
    if agent.get("lease_until") and agent["lease_until"] > when:
        return None
    run_id = secrets.token_hex(16)
    changes = {"run_id": run_id, "lease_until": stamp(now + timedelta(minutes=20)),
               "admission_after": None,
               "next_run_at": next_slot(now), "last_run_at": when,
               "last_status": "running", "last_error": None}
    expected = {key: agent.get(key) for key in ("run_id", "next_run_at", "enabled", "lease_until")}
    return run_id if store.patch_agent(owner, agent["id"], changes, expected) else None


def public_agent(agent):
    return {key: value for key, value in agent.items() if key not in {"run_id", "lease_until", "admission_after"}}
