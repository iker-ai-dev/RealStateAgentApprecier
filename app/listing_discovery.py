"""Normalize public search leads without inferring financial facts from snippets."""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from urllib.parse import urlsplit, parse_qsl

from nimble_mcp import NimbleMCP, IntegrationError
from app.observability import start_agent_transaction, start_span

DEFAULT_DOMAINS = ("crexi.com", "loopnet.com", "marcusmillichap.com", "matthews.com")


def safe_public_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 for c in value):
        return None
    try:
        p = urlsplit(value)
        if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
            return None
        if p.port not in (None, 80, 443) or p.hostname.lower() in ("localhost", "localhost.localdomain"):
            return None
        if any(k.lower() in {"token", "access_token", "api_key", "apikey", "key", "password", "authorization"}
               for k, _ in parse_qsl(p.query)):
            return None
        try:
            if not ipaddress.ip_address(p.hostname).is_global:
                return None
        except ValueError:
            if "." not in p.hostname or p.hostname.lower().endswith((".local", ".internal")):
                return None
        return value
    except (ValueError, TypeError):
        return None


def lead_kind(url):
    """Distinguish search directories and research articles from possible properties."""
    parsed = urlsplit(url)
    path = parsed.path.lower()
    host = parsed.hostname.lower()
    if any(segment in path for segment in ("/insights/", "/research/", "/reports/", "/news/")):
        return "market_report"
    if "/search/" in path or path.rstrip("/") in ("/search", "/properties"):
        return "listing_directory"
    if host.endswith("crexi.com"):
        pieces = path.strip("/").split("/")
        if len(pieces) >= 2 and pieces[0] == "properties" and not pieces[1].isdigit():
            return "listing_directory"
    return "property_discovery"


def normalize_listing_leads(result, *, query, retrieved_at=None):
    """Known URL/title only; price, occupancy, NOI and income stay unknown until verified."""
    stamp = retrieved_at or datetime.now(timezone.utc).isoformat()
    payloads = []
    if isinstance(result, dict):
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            payloads.append(structured)
        for block in result.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                body = block["text"]
                if len(body.encode()) > 2_000_000:
                    raise IntegrationError("Search output exceeds the listing normalization limit.")
                try:
                    value = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    payloads.append(value)
    leads, seen = [], set()
    for payload in payloads:
        records = payload.get("results", [])
        if not isinstance(records, list) or len(records) > 100:
            continue
        for item in records:
            if not isinstance(item, dict):
                continue
            url = safe_public_url(item.get("url") or item.get("link"))
            if url is None or url in seen:
                continue
            seen.add(url)
            title = item.get("title")
            title = title[:200] if isinstance(title, str) and title.strip() else "Public property research lead"
            leads.append({"id": hashlib.sha256(url.encode()).hexdigest()[:16], "title": title,
                "url": url, "city": None, "state": None, "asking_price": None,
                "occupancy_pct": None, "gla_sf": None, "broker_noi": None, "cap_rate": None,
                "source_name": urlsplit(url).hostname, "source_status": "discovery_only", "lead_kind": lead_kind(url),
                "retrieved_at": stamp, "listing_date": None, "active_status": "unknown",
                "property_type": "unknown", "query": query,
                "why_investigate": "A search lead to verify against the acquisition screen; financial and property-type fit are unknown.",
                "missing_fields": ["asking_price", "occupancy_pct", "property_type", "current_listing_status",
                                   "trade_area_income", "rent_roll", "T12", "vacancy_cause"],
                "vacancy_drivers": [], "source_support_location": "Search result URL/title only; linked page not verified."})
            if len(leads) == 10:
                return leads
    return leads


def discover_listings(api_key, query, *, source_domains=DEFAULT_DOMAINS):
    with start_agent_transaction("retailscope_ai.research"):
        with NimbleMCP(api_key, max_search_calls=1, max_results=10) as client:
            with start_span("mcp.search", "nimble.search"):
                evidence = client.search(query, source_domains=source_domains, max_results=10)
        return {"query": query, "leads": normalize_listing_leads(evidence, query=query),
                "evidence": evidence, "status": "DISCOVERY_ONLY"}
