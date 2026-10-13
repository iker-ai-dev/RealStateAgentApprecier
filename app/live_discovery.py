"""Explicit live Nimble searches; source text is unverified evidence, not underwriting.

Schemas: docs.nimbleway.com/nimble-sdk/web-tools/search and /web-tools/serp.
Only the existing search client is used. No URL fetching, retries, or cached leads.
"""
from datetime import datetime, timezone
import json
import os
import re

from app.listing_discovery import normalize_listing_leads, safe_public_url
from app.observability import start_agent_transaction, start_span
from nimble_mcp import IntegrationError, NimbleMCP, source_query

MAX_RESULTS = 10
MAX_SEARCHES = 3
MAX_TEXT = 2000
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
                lead["summary"] = (lead["snippet"] or lead["page_excerpt"])[:1200]
                lead["source_support_location"] = (
                    "Provider-returned page excerpt; not independently verified." if lead["page_excerpt"] else
                    "Search snippet/title only; linked page not verified.")
    if not recognized:
        raise IntegrationError("Nimble returned no supported search result collection.")
    return list(leads.values())


def research(query: str) -> list[dict]:
    """Spend at most three live searches; require an explicitly configured env key.

    Searches are unrestricted by domain so independent brokers and official market
    sources can be discovered. Provider page text is retained only if returned by
    search; the client requests full_content=False and never extracts linked URLs.
    Errors propagate explicitly; partial failures never substitute cached results.
    """
    query = source_query(query)
    key = os.getenv("NIMBLE_API_KEY")
    if not key or not key.strip():
        raise IntegrationError("Live discovery requires NIMBLE_API_KEY in the environment.")
    # In this retail workflow, "research centers in ..." is a command about
    # shopping centers, not a search for laboratories. Preserve named markets
    # such as Research Triangle and anchor the first (often only) search.
    brief = re.sub(r'^research\s+cent(?:er|re)s\b', 'shopping centers', query,
                   flags=re.IGNORECASE)
    prefixes = ("open-air strip shopping centers for sale broker listings ",
                "commercial retail market vacancy asking rents household income report ",
                "retail shopping center redevelopment investment properties ")
    queries = [prefix + brief[:4000 - len(prefix)] for prefix in prefixes]
    found = {}
    with start_agent_transaction("retailscope_ai.research"):
        with NimbleMCP(key, max_results=MAX_RESULTS, max_search_calls=MAX_SEARCHES) as client:
            for search in queries:
                with start_span("mcp.search", "nimble.search"):
                    response = client.search(search, source_domains=(), max_results=MAX_RESULTS)
                    stamp = datetime.now(timezone.utc).isoformat()
                    rows = normalize_evidence(response, query=search, retrieved_at=stamp)
                for lead in rows:
                    url = lead["url"]
                    if url not in found:
                        if len(found) < MAX_RESULTS:
                            found[url] = lead
                    else:
                        previous = found[url]
                        if search not in previous["search_queries"]:
                            previous["search_queries"].append(search)
                        for field in ("snippet", "page_excerpt"):
                            if not previous[field]:
                                previous[field] = lead[field]
                        for excerpt in lead["excerpts"]:
                            if excerpt not in previous["excerpts"] and len(previous["excerpts"]) < MAX_EXCERPTS:
                                previous["excerpts"].append(excerpt)
                        previous["summary"] = (previous["snippet"] or previous["page_excerpt"])[:1200]
                        previous["last_retrieved_at"] = lead["retrieved_at"]
                        if previous["page_excerpt"]:
                            previous["source_kind"] = "page"
                            previous["source_support_location"] = "Provider-returned page excerpt; not independently verified."
                if len(found) >= MAX_RESULTS:
                    break
    return list(found.values())[:MAX_RESULTS]
