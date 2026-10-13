"""Durable search subscriptions, claimed before any paid work starts."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import secrets
import re
from decimal import Decimal
from typing import Literal

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


def next_slot(now, cadence_hours=6):
    """UTC slots aligned to the six-hour dispatcher, strictly after now."""
    if type(cadence_hours) is not int or cadence_hours not in (6, 12, 24):
        raise ValueError("Cadence must be 6, 12, or 24 hours.")
    now = now.astimezone(timezone.utc)
    slot = now.replace(hour=(now.hour // cadence_hours) * cadence_hours,
                                             minute=17, second=0, microsecond=0)
    if slot <= now:
        slot += timedelta(hours=cadence_hours)
    return stamp(slot)


class SearchAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    city: str = Field(min_length=2, max_length=100)
    min_price: int = Field(ge=0, le=1_000_000_000)
    max_price: int = Field(gt=0, le=1_000_000_000)
    strategy: str = Field(min_length=2, max_length=200)
    cadence_hours: Literal[6, 12, 24] = 6
    conversation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

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
        return {**self.model_dump(exclude={"conversation_id"}), "id": secrets.token_hex(16), "query": query,
                "enabled": True, "created_at": stamp(now),
                "expires_at": stamp(now + timedelta(days=30)),
                "next_run_at": next_slot(now, self.cadence_hours), "last_run_at": None, "last_status": None,
                "last_job_id": None, "last_error": None, "lease_until": None, "run_id": None,
                "admission_after": None}


class AgentToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


class AgentDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @field_validator("message")
    @classmethod
    def clean(cls, value):
        if not value.strip():
            raise ValueError("Enter a scheduling request.")
        return value.strip()


class AgentDraft(BaseModel):
    name: str | None = None
    city: str | None = None
    min_price: int | None = None
    max_price: int | None = None
    strategy: str | None = None
    cadence_hours: Literal[6, 12, 24] | None = 6


DRAFT_LABELS = {"name": "Name", "city": "City", "min_price": "Minimum budget",
                "max_price": "Maximum budget", "strategy": "Strategy", "cadence_hours": "Search interval"}


def draft_summary(result: dict) -> str:
    """Readable saved history; user-supplied values stay literal Markdown text."""
    lines = [result["message"], ""]
    for field, label in DRAFT_LABELS.items():
        value = result["draft"].get(field)
        if value is None:
            text = "Not specified"
        elif field in {"min_price", "max_price"}:
            text = f"${value:,}"
        elif field == "cadence_hours":
            text = f"Every {value} hours"
        else:
            text = re.sub(r"([\\`*_{}\[\]()<>#!|])", r"\\\1", " ".join(str(value).split()))
        lines.append(f"- {label}: {text}")
    if result["warnings"]:
        lines.extend(["", "Please note:"])
        lines.extend("- " + warning for warning in result["warnings"])
    return "\n".join(lines)


def parse_agent_draft(message: str) -> dict:
    """Extract explicit common phrases only; never invoke a model or enroll."""
    draft = AgentDraft()
    warnings = []
    city = re.search(
        r"\b(?:in|near|around|city\s*:)\s+([A-Za-z][A-Za-z .'-]*(?:,\s*[A-Za-z .'-]+)?)"
        r"(?=\s+(?:with|for|under|below|between|from|up to|budget|every|daily|twice|priced|at|focused|focusing|and)\b|[;$\d]|$)",
        message, re.I)
    if city:
        # Stopwords must be applied separately: a greedy location match can
        # otherwise consume words from a subsequent budget or schedule clause.
        value = re.split(r"\s+(?:with|for|under|below|between|from|up to|budget|every|daily|twice|priced|at|focused|focusing|and)\b",
                         city.group(1), maxsplit=1, flags=re.I)[0].strip(" ., ")
        if 2 <= len(value) <= 100:
            draft.city = value
            draft.name = (value + " search")[:80]

    number = r"(\d+(?:,\d{3})*(?:\.\d+)?)"
    unit = r"(billion|million|thousand|[bmk])?"
    amount = r"\$?\s*" + number + r"\s*" + unit + r"\b"

    def dollars(value, suffix):
        scale = {"b": 1_000_000_000, "billion": 1_000_000_000,
                 "m": 1_000_000, "million": 1_000_000, "k": 1000, "thousand": 1000}
        return int(Decimal(value.replace(",", "")) * scale.get((suffix or "").lower(), 1))

    price_range = re.search(amount + r"\s*(?:-|to|and)\s*" + amount, message, re.I)
    if price_range and ("$" in price_range.group() or price_range[2] or price_range[4]
                        or re.search(r"\b(?:budget|price|priced)\b", message, re.I)):
        draft.min_price = dollars(price_range[1], price_range[2] or price_range[4])
        draft.max_price = dollars(price_range[3], price_range[4] or price_range[2])
    else:
        ceiling = re.search(r"\b(?:under|below|up to|maximum|max|budget(?:\s+of)?)\s*:?\s*" + amount,
                            message, re.I)
        if ceiling:
            draft.min_price = 0
            draft.max_price = dollars(ceiling[1], ceiling[2])
    if draft.max_price is not None and not (
            0 <= draft.min_price < draft.max_price <= 1_000_000_000):
        draft.min_price = draft.max_price = None
        warnings.append("Specify a valid budget range up to $1 billion.")

    strategies = [(r"\bvalue[ -]add\b", "value-add"),
                  (r"\bredevelopment\b", "redevelopment"),
                  (r"\bcash[ -]flow\b", "cash flow"),
                  (r"\bstabili[sz]ed\b", "stabilized"),
                  (r"\bcore[ -]plus\b", "core-plus")]
    selected = [label for pattern, label in strategies if re.search(pattern, message, re.I)]
    if selected:
        draft.strategy = ", ".join(selected)
    else:
        focus = re.search(r"\b(?:focus(?:ed|ing)? on|strategy\s*:)\s+(.+?)(?=[.;]|$)", message, re.I)
        if focus:
            draft.strategy = re.split(r"\s+(?:every|daily)\b", focus[1], flags=re.I)[0].strip()[:200] or None
            if draft.strategy and len(draft.strategy) < 2:
                draft.strategy = None

    interval = re.search(r"\bevery\s+(\d+|six|twelve|twenty-four)\s*(hours?|hrs?|h|days?)\b", message, re.I)
    if interval:
        word = interval[1].lower()
        hours = {"six": 6, "twelve": 12, "twenty-four": 24}.get(word)
        if hours is None:
            hours = int(word)
        if interval[2].lower().startswith("day"):
            hours *= 24
        draft.cadence_hours = hours if hours in (6, 12, 24) else None
    elif re.search(r"\b(?:twice (?:a |per )?day|twice daily)\b", message, re.I):
        draft.cadence_hours = 12
    elif re.search(r"\b(?:daily|every day|once (?:a |per )?day)\b", message, re.I):
        draft.cadence_hours = 24
    elif re.search(r"\b(?:hourly|weekly|monthly|every|interval)\b", message, re.I):
        draft.cadence_hours = None
    if draft.cadence_hours is None:
        warnings.append("Choose a supported interval: every 6, 12, or 24 hours.")
    fields = draft.model_dump()
    missing = [key for key, value in fields.items() if value is None]
    return {"schema_version": 1, "parser": "deterministic", "draft": fields,
            "missing_fields": missing, "warnings": warnings, "ready": not missing,
            "requires_confirmation": True,
            "message": ("Review and confirm this draft to create a background agent."
                        if not missing else "Complete these fields before confirming: " +
                        ", ".join(DRAFT_LABELS[key].lower() for key in missing) + ".")}


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
               "next_run_at": next_slot(now, agent.get("cadence_hours", 6)), "last_run_at": when,
               "last_status": "running", "last_error": None}
    expected = {key: agent.get(key) for key in ("run_id", "next_run_at", "enabled", "lease_until")}
    return run_id if store.patch_agent(owner, agent["id"], changes, expected) else None


def public_agent(agent):
    return {key: value for key, value in agent.items() if key not in {"run_id", "lease_until", "admission_after"}}
