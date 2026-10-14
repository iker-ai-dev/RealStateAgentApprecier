"""Small, synchronous, read-only Nimble MCP adapter, independent of any UI.

Supports handshake-based Streamable HTTP revisions through 2025-11-25.
This is intentionally not a general MCP SDK; see NIMBLE_SETUP.txt for limits.
"""
import json
import math
import re
import time

import requests
from jsonschema import validators

MCP_URL = "https://mcp.nimbleway.com/mcp"
SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")


class IntegrationError(RuntimeError):
    """Safe display message: never carries remote bodies, queries, or keys."""


def _domains(values):
    if isinstance(values, str):
        raise IntegrationError("Source domains must be a sequence of hostnames.")
    try:
        result = tuple(dict.fromkeys(values))
    except (TypeError, ValueError):
        raise IntegrationError("Source domains must be a sequence of hostnames.") from None
    if len(result) > 10 or any(not isinstance(v, str) or len(v) > 253 or
            not re.fullmatch(r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
                             r"[a-zA-Z]{2,63}", v) for v in result):
        raise IntegrationError("Use up to ten plain source hostnames, without paths or URLs.")
    return tuple(v.lower() for v in result)


def source_query(query, source_domains=()):
    """Site terms narrow discovery; they are not a guarantee of source access."""
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise IntegrationError("Use a nonempty research query of at most 4,000 characters.")
    domains = _domains(source_domains)
    return query.strip() + (" (" + " OR ".join("site:" + d for d in domains) + ")"
                            if domains else "")


def build_cre_query(address, market, property_type, as_of, *, source_domains=(),
                    public_records=True):
    """Build an inspectable query; an as-of phrase does not date-filter evidence."""
    terms = [address, market, property_type,
             "commercial real estate rents vacancy closed sales comparables", "as of", as_of]
    if any(not isinstance(v, str) for v in terms):
        raise IntegrationError("Research query fields must be text.")
    if not market.strip():
        raise IntegrationError("Enter a market for commercial real estate research.")
    if public_records:
        terms.append("county assessor recorder deed parcel public records")
    return source_query(" ".join(v.strip() for v in terms if v.strip()), source_domains)


def build_strip_center_queries(address, market, as_of, *, rent_domains=(),
                               demographic_domains=(), trend_domains=()):
    """Three discovery queries, with no asserted rent/demographic/property facts.

    Supply permitted broker/agency hostnames after selecting the subject market.
    Property NOI, asking price and strategy come from deal evidence/user input.
    """
    if any(not isinstance(v, str) for v in (address, market, as_of)) or not market.strip():
        raise IntegrationError("Supply a market and text query fields for strip-center research.")
    location = " ".join(v.strip() for v in (address, market) if v.strip())
    return {
        "rents": source_query(location + " open air strip shopping center retail asking rent "
            "effective rent per square foot year NNN gross lease date as of " + as_of,
            rent_domains),
        "demographics": source_query(market.strip() + " census ACS median household income "
            "population density persons per square mile tract geography survey vintage "
            "margin of error as of " + as_of, demographic_domains),
        "trends": source_query(market.strip() + " open air neighborhood community strip shopping "
            "center retail vacancy absorption supply rent growth market report period "
            "as of " + as_of, trend_domains),
    }


def build_market_discovery_queries(as_of, *, demographic_domains=(),
                                   projection_domains=(), retail_domains=()):
    """Nationwide discovery precedes property underwriting; no forecast is inferred."""
    if not isinstance(as_of, str) or not as_of.strip():
        raise IntegrationError("Supply a text as-of date for market discovery.")
    return {
        "measured_growth": source_query("United States metro county population change Census ACS "
            "median household income real inflation adjusted growth survey vintage geography "
            "margin of error as of " + as_of, demographic_domains),
        "population_projections": source_query("United States state demographer regional planning "
            "county metropolitan population projections 2030 2035 forecast release vintage "
            "assumptions uncertainty as of " + as_of, projection_domains),
        "retail_value_add": source_query("United States open air strip neighborhood shopping center "
            "retail vacancy absorption leasing demand rent growth dated market report partly vacant "
            "stable tenant base value add acquisition 1 million 10 million as of " + as_of,
            retail_domains),
    }


def _validator(schema):
    """Accept local schema references only: no network schema resolution."""
    if not isinstance(schema, dict):
        raise IntegrationError("Nimble returned an unsupported tool schema; no search was sent.")
    pending = [schema]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any(k in item and (not isinstance(item[k], str) or not item[k].startswith("#"))
                   for k in ("$ref", "$dynamicRef", "$recursiveRef")):
                raise IntegrationError("Nimble returned an external schema reference; no search was sent.")
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    try:
        cls = validators.validator_for(schema)
        # Unknown declared dialects are rejected instead of silently reinterpreted.
        if "$schema" in schema and schema["$schema"] not in (
                "https://json-schema.org/draft/2020-12/schema",
                "https://json-schema.org/draft/2019-09/schema",
                "http://json-schema.org/draft-07/schema#",
                "http://json-schema.org/draft-06/schema#",
                "http://json-schema.org/draft-04/schema#"):
            raise ValueError("Unsupported dialect")
        cls.check_schema(schema)
        return cls(schema)
    except Exception:
        raise IntegrationError("Nimble returned an invalid tool schema; no search was sent.") from None


class NimbleMCP:
    """One client per workflow. Only nimble_search can be invoked.

    No automatic retries/replay, agent creation, crawl, extract, or remote writes.
    Read-only searches can still consume account credits. Do not share across threads.
    transport is an optional requests-compatible Session for partner tests.
    """
    def __init__(self, key, *, source_domains=(), max_results=5, max_search_calls=3,
                 timeout=(10, 15), request_deadline=60, max_response_bytes=2_000_000,
                 max_tool_pages=5, transport=None, workflow_deadline=None):
        if (not isinstance(key, str) or not key or len(key) > 8192 or
                key.lower().startswith("bearer ") or
                any(ord(c) < 33 or ord(c) > 126 for c in key)):
            raise IntegrationError("Provide a raw Nimble API key, without the Bearer prefix.")
        for value, low, high, label in ((max_results, 1, 10, "result count"),
                (max_search_calls, 1, 10, "search budget"),
                (max_response_bytes, 1024, 5_000_000, "response limit"),
                (max_tool_pages, 1, 10, "tool page limit")):
            if type(value) is not int or not low <= value <= high:
                raise IntegrationError("Nimble " + label + " is outside the supported bounds.")
        if (not isinstance(timeout, (tuple, list)) or len(timeout) != 2 or
                any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 45
                    for v in timeout) or type(request_deadline) not in (int, float) or
                not math.isfinite(request_deadline) or not 0 < request_deadline <= 120):
            raise IntegrationError("Use finite, bounded Nimble request timeouts.")
        self.source_domains = _domains(source_domains)
        self.max_results, self.max_search_calls = max_results, max_search_calls
        self.timeout, self.request_deadline = tuple(timeout), request_deadline
        if workflow_deadline is not None and (type(workflow_deadline) not in (int, float)
                or not math.isfinite(workflow_deadline)):
            raise IntegrationError("Use a finite Nimble workflow deadline.")
        self.workflow_deadline = workflow_deadline
        self.max_response_bytes, self.max_tool_pages = max_response_bytes, max_tool_pages
        self._http = transport if transport is not None else requests.Session()
        self._headers = {"Authorization": "Bearer " + key,
                         "Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json"}
        # Avoid implicit .netrc credentials and environment proxies in production.
        if transport is None:
            self._http.trust_env = False
        self._request_id = self._requests = self._search_calls = 0
        self._ready = self._closed = False
        self.protocol_version = None
        self.termination_status = "not_needed"

    def _message(self, value, request_id, *, sse=False):
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
            raise IntegrationError("Nimble returned a malformed MCP response.")
        if "method" in value:
            if sse and "id" not in value and isinstance(value["method"], str):
                return None  # Related progress/log notifications are untrusted data.
            raise IntegrationError("Nimble requested unsupported client interaction.")
        if type(value.get("id")) is not int or value["id"] != request_id:
            raise IntegrationError("Nimble returned an unexpected response ID.")
        if ("error" in value) == ("result" in value):
            raise IntegrationError("Nimble returned a malformed MCP response.")
        if "error" in value:
            raise IntegrationError("Nimble MCP rejected the request. Check account access and tool inputs.")
        if not isinstance(value["result"], dict):
            raise IntegrationError("Nimble returned a malformed MCP result.")
        return value["result"]

    def _read(self, response, request_id, started):
        media = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media not in ("application/json", "text/event-stream"):
            raise IntegrationError("Nimble returned an unsupported response content type.")
        size, buffer, data = 0, bytearray(), []
        # Small chunks permit clock and byte checks during slowly arriving streams.
        for chunk in response.iter_content(chunk_size=1):
            if (time.monotonic() - started > self.request_deadline or
                    self.workflow_deadline is not None and time.monotonic() >= self.workflow_deadline):
                raise IntegrationError("Nimble request exceeded its time budget; it was not retried.")
            size += len(chunk)
            if size > self.max_response_bytes:
                raise IntegrationError("Nimble response exceeded the configured size limit.")
            buffer.extend(chunk)
            if media == "text/event-stream" and buffer.endswith(b"\n"):
                line = bytes(buffer).rstrip(b"\r\n").decode("utf-8")
                buffer.clear()
                if line.startswith("data:"):
                    field = line[5:]
                    data.append(field[1:] if field.startswith(" ") else field)
                elif not line:
                    if data and any(data):
                        result = self._message(json.loads("\n".join(data)), request_id, sse=True)
                        if result is not None:
                            return result
                    data = []  # Empty SSE priming events and comments are allowed.
        if (time.monotonic() - started > self.request_deadline or
                self.workflow_deadline is not None and time.monotonic() >= self.workflow_deadline):
            raise IntegrationError("Nimble request exceeded its time budget; it was not retried.")
        if media == "application/json":
            return self._message(json.loads(bytes(buffer).decode("utf-8")), request_id)
        raise IntegrationError("Nimble closed the stream without a completed response; it was not retried.")

    def _rpc(self, method, params=None, *, notification=False):
        if self._closed:
            raise IntegrationError("This Nimble client is closed; open a new client.")
        remaining = (self.workflow_deadline - time.monotonic()
                     if self.workflow_deadline is not None else None)
        if remaining is not None and remaining <= 0:
            raise IntegrationError("Nimble workflow time budget exhausted.")
        if self._requests >= 2 + self.max_search_calls * (self.max_tool_pages + 1):
            raise IntegrationError("Nimble workflow request budget exhausted.")
        self._requests += 1
        self._request_id += 1
        request_id = self._request_id
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            payload["id"] = request_id
        started = time.monotonic()
        try:
            with self._http.post(MCP_URL, json=payload, headers=self._headers,
                    timeout=(tuple(min(t, max(0.001, remaining / 2)) for t in self.timeout)
                             if remaining is not None else self.timeout),
                    stream=True, allow_redirects=False) as response:
                status = response.status_code
                if status == 404 and "Mcp-Session-Id" in self._headers:
                    self._ready = False
                    self._headers.pop("Mcp-Session-Id", None)
                    raise IntegrationError("Nimble session expired; open a new client. No search was replayed.")
                if status in (401, 403):
                    raise IntegrationError("Nimble authentication or account access was denied.")
                if status == 429:
                    raise IntegrationError("Nimble rate or credit limit reached; no automatic retry was made.")
                if notification:
                    if status != 202:
                        raise IntegrationError("Nimble did not accept the MCP initialization notification.")
                    return {}
                if status != 200:
                    raise IntegrationError("Nimble returned HTTP %s; no automatic retry was made." % status)
                result = self._read(response, request_id, started)
                session = response.headers.get("Mcp-Session-Id")
                if session is not None:
                    if (not session or len(session) > 4096 or
                            any(ord(c) < 33 or ord(c) > 126 for c in session)):
                        raise IntegrationError("Nimble returned an invalid MCP session header.")
                    if method == "initialize":
                        self._headers["Mcp-Session-Id"] = session
                    elif session != self._headers.get("Mcp-Session-Id"):
                        raise IntegrationError("Nimble changed its MCP session unexpectedly.")
                return result
        except requests.RequestException:
            raise IntegrationError("Nimble transport failed; delivery is unverified and was not retried.") from None
        except (ValueError, UnicodeError, TypeError, KeyError, RecursionError):
            raise IntegrationError("Nimble returned an unreadable MCP response.") from None

    def __enter__(self):
        if self._ready or self._closed:
            raise IntegrationError("Use a new Nimble client for each workflow.")
        try:
            result = self._rpc("initialize", {"protocolVersion": SUPPORTED_PROTOCOLS[0],
                "capabilities": {}, "clientInfo": {"name": "cre-appraisal-agent", "version": "0.2.0"}})
            version = result.get("protocolVersion")
            if version not in SUPPORTED_PROTOCOLS:
                raise IntegrationError("Nimble negotiated an unsupported MCP revision; update the adapter.")
            if not isinstance(result.get("capabilities"), dict) or not isinstance(
                    result["capabilities"].get("tools"), dict):
                raise IntegrationError("This Nimble connection does not advertise MCP tools.")
            self.protocol_version = version
            self._headers["MCP-Protocol-Version"] = version
            self._rpc("notifications/initialized", notification=True)
            self._ready = True
            return self
        except Exception:
            self.close()
            raise

    def close(self):
        if self._closed:
            return
        try:
            if "Mcp-Session-Id" in self._headers:
                try:
                    with self._http.delete(MCP_URL, headers=self._headers, timeout=(5, 5),
                            stream=True, allow_redirects=False) as response:
                        self.termination_status = ("terminated" if 200 <= response.status_code < 300
                            else "unsupported" if response.status_code == 405 else "unverified")
                except requests.RequestException:
                    self.termination_status = "unverified"
        finally:
            self._headers.clear()
            self._http.close()
            self._ready, self._closed = False, True

    def __exit__(self, *args):
        self.close()

    def _search_tool(self):
        params, cursors, found = {}, set(), None
        for _ in range(self.max_tool_pages):
            page = self._rpc("tools/list", params)
            tools = page.get("tools")
            if not isinstance(tools, list) or any(not isinstance(t, dict) or
                    not isinstance(t.get("name"), str) for t in tools):
                raise IntegrationError("Nimble returned a malformed tool catalog.")
            for tool in tools:
                if tool["name"] == "nimble_search":
                    if found is not None:
                        raise IntegrationError("Nimble returned duplicate search tool definitions.")
                    found = tool
            cursor = page.get("nextCursor")
            if cursor is None:
                if found is None:
                    raise IntegrationError("This Nimble account does not expose nimble_search.")
                return found
            if not isinstance(cursor, str) or not cursor or cursor in cursors or len(cursor) > 4096:
                raise IntegrationError("Nimble returned an invalid tool pagination cursor.")
            cursors.add(cursor)
            params = {"cursor": cursor}
        raise IntegrationError("Nimble tool catalog exceeds the configured page limit; no search was sent.")

    def search(self, query, *, source_domains=None, max_results=None, full_content=False):
        if not self._ready:
            raise IntegrationError("Open the Nimble client with a context manager before searching.")
        if type(full_content) is not bool:
            raise IntegrationError("Full content must be a boolean.")
        count = self.max_results if max_results is None else max_results
        if type(count) is not int or not 1 <= count <= 10:
            raise IntegrationError("Request between one and ten Nimble search results.")
        domains = self.source_domains if source_domains is None else _domains(source_domains)
        query = source_query(query, domains)
        if self._search_calls >= self.max_search_calls:
            raise IntegrationError("Nimble search budget exhausted; open a new workflow explicitly.")
        self._search_calls += 1  # Failed attempts consume the budget too.
        tool = self._search_tool()
        schema = tool.get("inputSchema")
        validator = _validator(schema)
        props = schema.get("properties")
        if schema.get("type") != "object" or not isinstance(props, dict) or not all(
                k in props for k in ("query", "max_results")):
            raise IntegrationError("Nimble search schema lacks bounded query/result inputs; no search was sent.")
        args = {"query": query, "max_results": count}
        # Only documented parameters that the live MCP schema advertises are used.
        for name, value in (("include_answer", False), ("full_content", full_content),
                            ("include_domains", list(domains) if domains else None)):
            if name in props and value is not None:
                args[name] = value
        if full_content and "full_content" in args and not validator.is_valid(args):
            # Some catalogs expose the flag but constrain it to false. Do not
            # send an unsupported value or invent a different extraction tool.
            args["full_content"] = False
        try:
            validator.validate(args)
        except Exception:
            raise IntegrationError("Nimble search schema needs an updated adapter; no search was sent.") from None
        annotations = tool.get("annotations", {})
        if not isinstance(annotations, dict) or annotations.get("readOnlyHint") is False:
            raise IntegrationError("Nimble advertises this search tool as writing data; call blocked.")
        result = self._rpc("tools/call", {"name": "nimble_search", "arguments": args})
        if result.get("isError"):
            raise IntegrationError("Nimble search reported failure. Check account credits and query.")
        if not isinstance(result.get("content"), list) or any(not isinstance(item, dict)
                for item in result["content"]):
            raise IntegrationError("Nimble search returned malformed tool content.")
        if "outputSchema" in tool and "structuredContent" in result:
            try:
                _validator(tool["outputSchema"]).validate(result["structuredContent"])
            except Exception:
                raise IntegrationError("Nimble search output did not match its advertised schema.") from None
        return result
