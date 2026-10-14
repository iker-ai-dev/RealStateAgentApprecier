"""Explicit live Nimble searches; source text is unverified evidence, not underwriting.

Schemas: docs.nimbleway.com/nimble-sdk/web-tools/search and /web-tools/serp.
Only the advertised search tool is used. No URL fetching or cached leads.
"""
from datetime import datetime, timezone
import json
import os
import re
import math
import time
from copy import deepcopy

from app.listing_discovery import normalize_listing_leads, safe_public_url
from app.observability import start_agent_transaction, start_span
from nimble_mcp import IntegrationError, NimbleMCP, source_query
from app.investigator import plan_followups

MAX_RESULTS = 10
MAX_SEARCHES = 6
MAX_TEXT = 4000
MAX_EXCERPTS = 3
MAX_BODY_BYTES = 2_000_000
MAX_RECORDS = 100


def _text(value):
    return value.strip()[:MAX_TEXT] if isinstance(value, str) else ""


def _payloads(response):
    """MCP structuredContent and JSON text blocks only; never parse prose as facts."""
    if not isinstance(response, dict) or response.get("isError"):
        raise IntegrationError("Nimble search did not return a successful evidence response.")
    yield response
    structured = response.get("structuredContent")
    if isinstance(structured, (dict, list)):
        yield structured
    blocks = response.get("content", [])
    if not isinstance(blocks, list) or len(blocks) > 20:
        raise IntegrationError("Nimble search content exceeds the supported evidence bounds.")
    total_bytes = 0
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        body = block.get("text")
        if not isinstance(body, str):
            continue
        total_bytes += len(body.encode("utf8"))
        if total_bytes > MAX_BODY_BYTES:
            raise IntegrationError("Nimble search evidence exceeds the response size limit.")
        try:
            payload = json.loads(body)
        except (ValueError, RecursionError):
            continue
        if isinstance(payload, (dict, list)):
            yield payload


def _collections(payload):
    if isinstance(payload, list):
        yield payload
        return
    # Fixed paths, not a recursive crawl of arbitrary tool output or metadata.
    roots = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        roots.append(data)
        parsing = data.get("parsing")
        entities = parsing.get("entities") if isinstance(parsing, dict) else None
        if isinstance(entities, dict) and "OrganicResult" in entities:
            yield entities["OrganicResult"]
    for root in roots:
        for name in ("results", "organic_results"):
            if name in root:
                yield root[name]


def normalize_evidence(response, *, query, retrieved_at):
    """Return bounded leads with verbatim excerpts; never copy remote numeric fields."""
    leads = {}
    recognized = False
    inspected = 0
    for payload in _payloads(response):
        for records in _collections(payload):
            recognized = True
            if not isinstance(records, list) or len(records) > MAX_RECORDS:
                raise IntegrationError("Nimble result collection exceeds the supported evidence bounds.")
            for record in records:
                inspected += 1
                if inspected > 1000:
                    raise IntegrationError("Nimble evidence contains too many result records.")
                if not isinstance(record, dict):
                    continue
                url = safe_public_url(record.get("url")) or safe_public_url(record.get("link"))
                if url is None:
                    continue
                if url not in leads:
                    if len(leads) >= MAX_RESULTS:
                        continue
                    lead = normalize_listing_leads(
                        {"structuredContent": {"results": [{"url": url, "title": record.get("title")}]}},
                        query=query, retrieved_at=retrieved_at)[0]
                    lead.update(provider="Nimble MCP", source_kind="snippet", summary="", excerpts=[],
                                snippet="", page_excerpt="", search_queries=[query])
                    leads[url] = lead
                lead = leads[url]
                snippet = next((text for name in ("description", "snippet", "summary", "text")
                                if (text := _text(record.get(name)))), "")
                page = next((text for name in ("content", "markdown", "full_content")
                             if (text := _text(record.get(name)))), "")
                if not lead["snippet"]:
                    lead["snippet"] = snippet
                if not lead["page_excerpt"]:
                    lead["page_excerpt"] = page
                for text in (snippet, page):
                    if text and text not in lead["excerpts"] and len(lead["excerpts"]) < MAX_EXCERPTS:
                        lead["excerpts"].append(text)
                lead["source_kind"] = "page" if lead["page_excerpt"] else "snippet"
                lead["summary"] = _summary(lead)
                lead["source_support_location"] = (
                    "Provider-returned page excerpt; not independently verified." if lead["page_excerpt"] else
                    "Search snippet/title only; linked page not verified.")
    if not recognized:
        raise IntegrationError("Nimble returned no supported search result collection.")
    return list(leads.values())


def _search_brief(query: str) -> str:
    """Separate acquisition criteria from answer instructions without truncation."""
    query = source_query(query)
    query = re.sub(r'^research\s+(?:for\s+me\s+)?cent(?:er|re)s\b',
                   'shopping centers', query, flags=re.IGNORECASE)
    market = re.search(
        r'\bin\s+([A-Za-z][A-Za-z ,\'-]*?)(?=\s+(?:with|within|under|between|from|for)\b|[.!?;]|$)',
        query, re.IGNORECASE)
    types = re.findall(r'\b(?:open-air shopping centers?|shopping centres?|shopping centers?|'
                       r'strip centers?|retail properties|retail centers?|mixed-use|freestanding retail)\b',
                       query, re.IGNORECASE)
    amount = r'\d[\d,]*(?:\.\d+)?(?:\s*(?:million|billion|[mkb])\b)?'
    money = re.findall(r'\$\s*' + amount + r'(?:\s*[-\u2013]\s*\$?\s*' + amount + r')?',
                       query, re.IGNORECASE)
    if market and types:
        parts = [market.group(1).strip(' ,'), *dict.fromkeys(t.lower() for t in types)]
        if money:
            parts.append(' '.join(dict.fromkeys(money)))
        if re.search(r'\bvalue[- ]add\b', query, re.IGNORECASE):
            parts.append('value-add')
        elif re.search(r'\bredevelopment\b', query, re.IGNORECASE):
            parts.append('redevelopment')
        brief = ' '.join(parts)
    else:
        brief = re.split(r'\s+(?:Find original|Include source|Identify missing|Do not assume|'
                         r'Focus on|Report asking)\b', query, maxsplit=1, flags=re.IGNORECASE)[0]
        brief = re.sub(r'^research\s+(?:for\s+me\s+|(?=open-air|shopping|strip|retail))',
                       '', brief, flags=re.IGNORECASE)
    if len(brief) > 450:
        raise IntegrationError('Use a concise market, property type and price range for discovery.')
    return brief.rstrip(' .')


def _summary(lead):
    if lead["page_excerpt"]:
        return (lead["page_excerpt"] + "\n" + lead["snippet"][:1000]).strip()
    return lead["snippet"][:1200]


GAP_TERMS = {
    "property financials": (r"\b(?:NOI|net operating income|cap rate|occupancy)\b",
                            "shopping center offering memorandum asking price NOI occupancy"),
    "tenant rents": (r"\b(?:rent roll|NNN|per square foot|PSF)\b",
                     "shopping center broker tenant rent roll asking rent per square foot"),
    "local demographics": (r"\b(?:median household income|population)\b",
                           "Census ACS population median household income retail trade area"),
}


def _gaps(rows):
    # These are retrieval hints, not a claim that a topic was verified or complete.
    text = " ".join(row["summary"] for row in rows)
    return [name for name, (pattern, _) in GAP_TERMS.items() if not re.search(pattern, text, re.I)]


def _followup(brief, rows, attempted):
    properties = [row for row in rows if row.get("lead_kind") == "property_discovery"]
    for row in properties[:2]:
        title = re.sub(r"[^A-Za-z0-9 ,.'-]", " ", row["title"])[:100].strip()
        if title and not row["page_excerpt"]:
            query = f"{brief} {title} broker offering memorandum property details"
            if query not in attempted:
                return query, "Looking for original property details behind the search results."
    for gap in _gaps(rows):
        query = brief + " " + GAP_TERMS[gap][1]
        if query not in attempted:
            return query, "Searching for missing " + gap + " evidence."
    query = brief + " individual property address original broker for sale listing"
    if not properties and query not in attempted:
        return query, "Looking for individual property listings rather than directories."
    return None


def _merge(found, rows, search):
    for lead in rows:
        url = lead["url"]
        if url not in found:
            # Keep a bounded candidate pool so later property pages are not discarded
            # just because the initial search filled the first ten slots.
            if len(found) < MAX_RESULTS * MAX_SEARCHES:
                found[url] = lead
            continue
        previous = found[url]
        if search not in previous["search_queries"]:
            previous["search_queries"].append(search)
        for field in ("snippet", "page_excerpt"):
            if len(lead[field]) > len(previous[field]):
                previous[field] = lead[field]
        previous["excerpts"] = list(dict.fromkeys(
            [previous["page_excerpt"], previous["snippet"], *previous["excerpts"], *lead["excerpts"]]))
        previous["excerpts"] = [text for text in previous["excerpts"] if text][:MAX_EXCERPTS]
        previous["summary"] = _summary(previous)
        previous["last_retrieved_at"] = lead["retrieved_at"]
        if previous["page_excerpt"]:
            previous["source_kind"] = "page"
            previous["source_support_location"] = "Provider-returned page excerpt; not independently verified."


def _selected(found):
    def score(row):
        text = row["summary"]
        return (row.get("lead_kind") == "property_discovery",
                bool(re.search(r"\$\s*\d|\b\d[\d,]*\s*(?:SF|sq\.? ft)\b", text, re.I)),
                bool(row["page_excerpt"]))
    return sorted(found.values(), key=score, reverse=True)[:MAX_RESULTS]


def research(query: str, on_progress=None, *, deadline_seconds=240) -> list[dict]:
    """At most six read-only searches and one Cortex plan, with live snapshots.

    Requests full content only if the advertised search schema supports it. Does
    not fetch arbitrary URLs/PDFs or access private broker documents. Later search
    failures preserve this run's evidence. Deadline stops admission of new work;
    an in-flight Cortex call has its own 90-second statement timeout.
    """
    if (type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds)
            or not 0 < deadline_seconds <= 300):
        raise IntegrationError("Use a retrieval time budget between zero and 300 seconds.")
    deadline = time.monotonic() + deadline_seconds
    brief = _search_brief(query)
    key = os.getenv("NIMBLE_API_KEY")
    if not key or not key.strip():
        raise IntegrationError("Live discovery requires NIMBLE_API_KEY in the environment.")
    found = {}
    attempted = set()
    planned = []

    def progress(stage, message):
        if on_progress:
            on_progress({"stage": stage, "message": message, "leads": deepcopy(_selected(found))})

    with start_agent_transaction("retailscope_ai.research"):
        progress("searching", "Searching public shopping-center listings and broker sources.")
        with NimbleMCP(key, max_results=MAX_RESULTS, max_search_calls=MAX_SEARCHES,
                       workflow_deadline=deadline, timeout=(10, 30)) as client:
            for step in range(MAX_SEARCHES):
                if time.monotonic() >= deadline:
                    progress("inspecting", "Retrieval time budget reached; keeping the sources collected so far.")
                    break
                if step == 0:
                    search = "open-air strip shopping centers for sale broker listings " + brief
                else:
                    if step == 1 and found and deadline - time.monotonic() >= 100:
                        progress("inspecting", "Reviewing collected evidence to choose targeted follow-up searches.")
                        try:
                            planned = plan_followups(query[:3200], _selected(found), _gaps(_selected(found)))
                        except Exception:
                            progress("followup", "Search planning unavailable; using bounded evidence-gap searches.")
                    if planned:
                        followup = planned.pop(0)
                        search = brief + " " + followup
                        progress("followup", "I am searching for: " + followup)
                    else:
                        next_search = _followup(brief, _selected(found), attempted)
                        if next_search is None:
                            break
                        search, message = next_search
                        progress("followup", message)
                if search in attempted:
                    continue
                attempted.add(search)
                try:
                    with start_span("mcp.search", "nimble.search"):
                        response = client.search(search, source_domains=(), max_results=MAX_RESULTS,
                                                 full_content=True)
                        rows = normalize_evidence(response, query=search,
                            retrieved_at=datetime.now(timezone.utc).isoformat())
                except IntegrationError:
                    if not found:
                        raise
                    progress("inspecting", "Further retrieval stopped; keeping the sources already collected.")
                    break
                _merge(found, rows, search)
                progress("inspecting", f"Search {step + 1} returned {len(rows)} sources; "
                         f"{len(_selected(found))} sources retained for analysis.")
    return _selected(found)
