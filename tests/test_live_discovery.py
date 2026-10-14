"""Live adapter fixtures and a real NimbleMCP handshake over an offline HTTP sink."""
import json
import os
import unittest
from unittest.mock import patch

from app.live_discovery import MAX_EXCERPTS, MAX_TEXT, _search_brief, normalize_evidence, research
from nimble_mcp import IntegrationError, MCP_URL, NimbleMCP

STAMP = "2026-10-12T12:00:00+00:00"
RECORD = {"title": "Synthetic retail center", "url": "https://broker.example/center",
          "description": "Advertised $4m and 75% occupancy; broker claims, not verified.",
          "asking_price": 4_000_000, "occupancy_pct": 75, "noi": 250_000}


def normalize(response):
    return normalize_evidence(response, query="synthetic market", retrieved_at=STAMP)


class Response:
    def __init__(self, value, status=200):
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        yield json.dumps(self.value).encode()


class FixtureSession:
    """HTTP boundary fixture; production research still constructs the actual client."""
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []
        self.closed = False

    def post(self, url, *, json, **kwargs):
        assert url == MCP_URL
        self.calls.append((json, kwargs))
        method = json["method"]
        if method == "notifications/initialized":
            return Response({}, 202)
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": [{"name": "nimble_search", "annotations": {"readOnlyHint": True},
                                 "inputSchema": {"type": "object", "properties": {
                                     "query": {"type": "string"}, "max_results": {"type": "integer"},
                                     "full_content": {"type": "boolean"},
                                     "include_answer": {"type": "boolean"}},
                                     "required": ["query", "max_results"], "additionalProperties": False}}]}
        elif method == "tools/call":
            result = next(self.outputs, {"content": [], "structuredContent": {"results": []}})
        else:
            raise AssertionError("Unexpected MCP operation")
        return Response({"jsonrpc": "2.0", "id": json["id"], "result": result})

    def close(self):
        self.closed = True


class EvidenceTests(unittest.TestCase):
    def test_known_response_variants_keep_description(self):
        variants = [{"results": [RECORD]}, {"data": {"results": [RECORD]}},
                    {"organic_results": [dict(RECORD, snippet="Organic snippet")]},
                    {"data": {"organic_results": [RECORD]}},
                    {"data": {"parsing": {"entities": {"OrganicResult": [RECORD]}}}},
                    [RECORD]]
        for payload in variants:
            for response in ({"structuredContent": payload},
                             {"content": [{"type": "text", "text": json.dumps(payload)}]}):
                with self.subTest(response=response):
                    lead = normalize(response)[0]
                    self.assertEqual(lead["summary"], RECORD["description"])
                    self.assertEqual(lead["source_kind"], "snippet")
                    self.assertEqual(lead["provider"], "Nimble MCP")
                    self.assertEqual(lead["retrieved_at"], STAMP)
                    for field in ("asking_price", "occupancy_pct", "broker_noi", "cap_rate", "gla_sf"):
                        self.assertIsNone(lead[field])

    def test_page_text_is_labeled_and_not_promoted_to_facts(self):
        lead = normalize({"results": [dict(RECORD, content="Provider page excerpt: projected NOI $300k.") ]})[0]
        self.assertEqual(lead["source_kind"], "page")
        self.assertIn("projected NOI", lead["page_excerpt"])
        self.assertEqual(len(lead["excerpts"]), 2)
        self.assertIsNone(lead["broker_noi"])
        self.assertIn("not independently verified", lead["source_support_location"])

    def test_text_only_record_and_link_alias(self):
        lead = normalize({"results": [{"link": "https://broker.example/other", "text": "Plain source excerpt"}]})[0]
        self.assertEqual(lead["summary"], "Plain source excerpt")
        self.assertEqual(lead["source_kind"], "snippet")
        self.assertEqual(lead["title"], "Public property research lead")

    def test_no_arbitrary_deep_extraction_or_unlinked_prose(self):
        for response in ({"metadata": {"secret": {"results": [RECORD]}}},
                         {"content": [{"type": "text", "text": "BUY THIS CENTER https://broker.example/x"}]}):
            with self.assertRaises(IntegrationError):
                normalize(response)
        self.assertEqual(normalize({"results": [], "metadata": {"results": [RECORD]}}), [])

    def test_unsafe_urls_are_not_evidence_or_fetch_targets(self):
        urls = ["http://127.0.0.1/a", "http://10.0.0.2", "https://user:pw@example.com", "https://[invalid",
                "javascript:alert(1)", "https://example.com/?access_token=private", "https://source.internal/x"]
        with patch("socket.socket", side_effect=AssertionError("No URL fetching")):
            self.assertEqual(normalize({"results": [dict(RECORD, url=url) for url in urls]}), [])

    def test_caps_deduplication_and_resource_semantics(self):
        records = [dict(RECORD, url=f"https://broker.example/{i}", description="x" * 5000) for i in range(20)]
        rows = normalize({"results": records + records})
        self.assertEqual(len(rows), 10)
        self.assertEqual(len(rows[0]["summary"]), 1200)
        self.assertTrue(all(len(text) <= MAX_TEXT for row in rows for text in row["excerpts"]))
        self.assertTrue(all(len(row["excerpts"]) <= MAX_EXCERPTS for row in rows))
        directory = normalize({"results": [dict(RECORD, url="https://www.crexi.com/properties/TX/Shopping-Centers")]})[0]
        self.assertEqual(directory["lead_kind"], "listing_directory")

    def test_malformed_and_oversized_payloads_fail_explicitly(self):
        for response in (None, {"isError": True}, {"results": {}}, {"results": [{}] * 101},
                         {"content": [{"type": "text", "text": "x" * 2_000_001}]}):
            with self.assertRaises(IntegrationError):
                normalize(response)


class ResearchTests(unittest.TestCase):
    def setUp(self):
        planner = patch("app.live_discovery.plan_followups", return_value=[])
        self.planner = planner.start()
        self.addCleanup(planner.stop)

    def test_verbose_acquisition_request_keeps_market_types_and_budget(self):
        query = ('Research for me open-air shopping centers and strip centers for sale in '
                 'Phoenix, AZ, United States, with an asking price between $1 million and $10 million. '
                 'Focus on value-add opportunities. Find original property listings and report asking '
                 'price, area, occupancy, cap rate, annual NOI, tenant rents, population and median '
                 'household income when sources provide them. Include source links and distinguish '
                 'source-reported facts from unknowns. Do not assume a property fits the budget.')
        brief = _search_brief(query)
        self.assertEqual(brief, 'Phoenix, AZ, United States open-air shopping centers strip centers '
                         '$1 million $10 million value-add')
        session = FixtureSession([{'content': [], 'structuredContent': {'results': []}}] * 3)
        with patch.dict(os.environ, {'NIMBLE_API_KEY': 'synthetic'}, clear=True), \
                patch('nimble_mcp.requests.Session', return_value=session):
            research(query)
        queries = [call['params']['arguments']['query'] for call, _ in session.calls
                   if call['method'] == 'tools/call']
        self.assertEqual(len(queries), 5)
        for query in queries:
            self.assertLess(len(query), 200)
            self.assertIn(brief, query)
            self.assertNotIn('report asking', query)

    def test_criteria_at_end_of_long_request_are_not_truncated(self):
        query = ('Please inspect the evidence carefully. ' * 60 +
                 'Find strip centers in Phoenix, AZ with asking prices from $1,000,000 to $10,000,000.')
        self.assertEqual(_search_brief(query), 'Phoenix, AZ strip centers $1,000,000 $10,000,000')
        with self.assertRaisesRegex(IntegrationError, 'concise'):
            _search_brief('Unstructured instructions ' * 100)

    def test_compact_price_range_and_named_market_are_preserved(self):
        self.assertEqual(_search_brief('Find strip centers in Phoenix under $1-10M.'),
                         'Phoenix strip centers $1-10M')
        self.assertEqual(_search_brief('Research strip centers in Research Triangle with budget $1M-$10M.'),
                         'Research Triangle strip centers $1M-$10M')

    def test_missing_key_and_invalid_query_make_no_requests(self):
        with patch.dict(os.environ, {}, clear=True), patch("nimble_mcp.requests.Session") as session:
            with self.assertRaisesRegex(IntegrationError, "NIMBLE_API_KEY"):
                research("synthetic market")
            for query in (None, "", " ", "x" * 4001):
                with self.assertRaises(IntegrationError):
                    research(query)
            session.assert_not_called()

    def test_real_client_bounded_searches_handshake_and_no_arbitrary_http(self):
        outputs = [{"content": [], "structuredContent": {"results": [dict(RECORD, url=f"https://broker.example/{i}")]}}
                   for i in range(3)]
        session = FixtureSession(outputs)
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "synthetic-key"}, clear=True), \
                patch("nimble_mcp.requests.Session", return_value=session), \
                patch("socket.socket", side_effect=AssertionError("No external network")):
            rows = research("Austin TX")
        self.assertEqual(len(rows), 3)
        searches = [call for call, _ in session.calls if call["method"] == "tools/call"]
        self.assertEqual(len(searches), 4)
        self.assertEqual(searches[0]["params"]["arguments"]["query"],
                         "open-air strip shopping centers for sale broker listings Austin TX")
        for search in searches:
            args = search["params"]["arguments"]
            self.assertEqual(search["params"]["name"], "nimble_search")
            self.assertEqual(args["max_results"], 10)
            self.assertTrue(args["full_content"])
            self.assertFalse(args["include_answer"])
        self.assertTrue(session.closed)
        self.assertTrue(all(row["summary"] and row["retrieved_at"] for row in rows))
        self.assertNotIn("synthetic-key", json.dumps(rows))

    def test_ten_results_do_not_prevent_deeper_followups(self):
        records = [dict(RECORD, url=f"https://broker.example/{i}") for i in range(10)]
        session = FixtureSession([{"content": [], "structuredContent": {"results": records}}])
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "synthetic"}, clear=True), \
                patch("nimble_mcp.requests.Session", return_value=session):
            self.assertEqual(len(research("Austin TX")), 10)
        self.assertEqual(sum(call["method"] == "tools/call" for call, _ in session.calls), 4)

    def test_first_search_resolves_retail_intent_without_changing_market_names(self):
        for brief, expected in (
                ('Research centers in Phoenix with redevelopment potential',
                 'Phoenix shopping centers redevelopment'),
                ('Research Triangle shopping centers', 'Research Triangle shopping centers')):
            with self.subTest(brief=brief):
                records = [dict(RECORD, url=f'https://broker.example/{i}') for i in range(10)]
                session = FixtureSession([{'content': [], 'structuredContent': {'results': records}}])
                with patch.dict(os.environ, {'NIMBLE_API_KEY': 'synthetic'}, clear=True), \
                        patch('nimble_mcp.requests.Session', return_value=session):
                    research(brief)
                searches = [call for call, _ in session.calls if call['method'] == 'tools/call']
                self.assertLessEqual(len(searches), 6)
                self.assertEqual(searches[0]['params']['arguments']['query'],
                                 'open-air strip shopping centers for sale broker listings ' + expected)

    def test_failure_never_retries_and_retains_current_run_partial_results(self):
        session = FixtureSession([{"content": [], "structuredContent": {"results": [RECORD]}},
                                  {"content": [], "isError": True}])
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "synthetic"}, clear=True), \
                patch("nimble_mcp.requests.Session", return_value=session):
            events = []
            rows = research("Austin TX", on_progress=events.append)
            self.assertEqual(rows[0]["url"], RECORD["url"])
            self.assertIn("Further retrieval stopped", events[-1]["message"])
        self.assertEqual(sum(call["method"] == "tools/call" for call, _ in session.calls), 2)
        self.assertTrue(session.closed)

    def test_cross_search_dedup_keeps_snippet_and_later_page_evidence(self):
        session = FixtureSession([
            {"content": [], "structuredContent": {"results": [RECORD]}},
            {"content": [], "structuredContent": {"results": [dict(RECORD, description="Later snippet",
                                                                     content="Later page excerpt")]}},
            {"content": [], "structuredContent": {"results": [dict(RECORD, description="Third snippet")]}}])
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "synthetic"}, clear=True), \
                patch("nimble_mcp.requests.Session", return_value=session):
            rows = research("Austin TX")
        self.assertEqual(len(rows), 1)
        self.assertIn("Later page excerpt", rows[0]["summary"])
        self.assertEqual(rows[0]["source_kind"], "page")
        self.assertEqual(rows[0]["page_excerpt"], "Later page excerpt")
        self.assertIn("Later snippet", rows[0]["excerpts"])
        self.assertIn("Later page excerpt", rows[0]["excerpts"])
        self.assertEqual(len(rows[0]["search_queries"]), 3)
        self.assertTrue(rows[0]["last_retrieved_at"])

    def test_unstructured_maximum_query_fails_instead_of_silently_truncating(self):
        session = FixtureSession([{"content": [], "structuredContent": {"results": []}} for _ in range(3)])
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "synthetic"}, clear=True), \
                patch("nimble_mcp.requests.Session", return_value=session):
            with self.assertRaisesRegex(IntegrationError, 'concise'):
                research("x" * 4000)
        searches = [call for call, _ in session.calls if call['method'] == 'tools/call']
        self.assertEqual(searches, [])

    def test_model_followups_snapshots_and_budget(self):
        self.planner.return_value = ["specific broker asking price", "local rent roll evidence"]
        session = FixtureSession([{"content": [], "structuredContent": {"results": [RECORD]}}] * 8)
        events = []
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "synthetic"}, clear=True), \
                patch("nimble_mcp.requests.Session", return_value=session):
            rows = research("Austin TX", on_progress=events.append)
        calls = [call["params"]["arguments"] for call, _ in session.calls if call["method"] == "tools/call"]
        self.assertEqual(len(calls), 6)
        self.assertIn("specific broker asking price", calls[1]["query"])
        self.planner.assert_called_once()
        first_snapshot = next(event for event in events if event["leads"])
        self.assertEqual(len(first_snapshot["leads"][0]["search_queries"]), 1)
        self.assertEqual(len(rows[0]["search_queries"]), 6)
        self.assertEqual({event["stage"] for event in events}, {"searching", "inspecting", "followup"})

    def test_planner_failure_is_honest_and_does_not_lose_sources(self):
        self.planner.side_effect = RuntimeError("PRIVATE_ERROR")
        session = FixtureSession([{"content": [], "structuredContent": {"results": [RECORD]}}])
        events = []
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "synthetic"}, clear=True), \
                patch("nimble_mcp.requests.Session", return_value=session):
            rows = research("Austin TX", events.append)
        self.assertTrue(rows)
        self.assertIn("planning unavailable", json.dumps(events))
        self.assertNotIn("PRIVATE_ERROR", json.dumps(events))

    def test_short_deadline_skips_model_and_invalid_budget_fails(self):
        session = FixtureSession([{"content": [], "structuredContent": {"results": [RECORD]}}])
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "synthetic"}, clear=True), \
                patch("nimble_mcp.requests.Session", return_value=session):
            research("Austin TX", deadline_seconds=30)
        self.planner.assert_not_called()
        for value in (0, 301, float("inf"), True):
            with self.assertRaises(IntegrationError):
                research("Austin TX", deadline_seconds=value)

    def test_later_full_page_replaces_directory_in_bounded_result_set(self):
        directories = [dict(RECORD, url=f"https://directory{i}.example/search/") for i in range(10)]
        page = dict(RECORD, content="Original property page asking price $4m, occupancy 75%.")
        session = FixtureSession([{"content": [], "structuredContent": {"results": directories}},
                                  {"content": [], "structuredContent": {"results": [page]}}])
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "synthetic"}, clear=True), \
                patch("nimble_mcp.requests.Session", return_value=session):
            rows = research("Austin TX")
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["url"], RECORD["url"])
        self.assertIn("Original property", rows[0]["summary"])

    def test_first_search_failure_is_not_reported_as_success(self):
        session = FixtureSession([{"content": [], "isError": True}])
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "synthetic"}, clear=True), \
                patch("nimble_mcp.requests.Session", return_value=session):
            with self.assertRaises(IntegrationError):
                research("Austin TX")
        self.planner.assert_not_called()

    def test_deadline_preserves_results_and_stops_followups(self):
        session = FixtureSession([{"content": [], "structuredContent": {"results": [RECORD]}}])
        clock = [0]
        events = []
        def progress(event):
            events.append(event)
            if event["leads"]:
                clock[0] = 241
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "synthetic"}, clear=True), \
                patch("nimble_mcp.requests.Session", return_value=session), \
                patch("app.live_discovery.time.monotonic", side_effect=lambda: clock[0]):
            rows = research("Austin TX", progress)
        self.assertEqual(len(rows), 1)
        self.assertIn("time budget reached", events[-1]["message"])
        self.assertEqual(sum(call["method"] == "tools/call" for call, _ in session.calls), 1)
        self.planner.assert_not_called()


class SearchCapabilityTests(unittest.TestCase):
    def test_full_content_only_when_advertised_and_supported(self):
        for capability in (None, {"type": "boolean"}, {"const": False}):
            with self.subTest(capability=capability):
                session = FixtureSession([{"content": [], "structuredContent": {"results": []}}])
                properties = {"query": {"type": "string"}, "max_results": {"type": "integer"}}
                if capability is not None:
                    properties["full_content"] = capability
                tool = {"name": "nimble_search", "annotations": {"readOnlyHint": True},
                        "inputSchema": {"type": "object", "properties": properties,
                                        "additionalProperties": False}}
                with NimbleMCP("synthetic", transport=session) as client:
                    with patch.object(client, "_search_tool", return_value=tool):
                        client.search("retail listings", full_content=True)
                arguments = next(call["params"]["arguments"] for call, _ in session.calls
                                 if call["method"] == "tools/call")
                if capability is None:
                    self.assertNotIn("full_content", arguments)
                else:
                    self.assertEqual(arguments["full_content"], capability != {"const": False})

    def test_expired_deadline_does_not_initialize_or_search(self):
        session = FixtureSession([])
        with self.assertRaisesRegex(IntegrationError, "time budget"):
            with NimbleMCP("synthetic", transport=session, workflow_deadline=0):
                pass
        self.assertEqual(session.calls, [])
        self.assertTrue(session.closed)

    def test_callback_failure_stops_further_paid_work(self):
        session = FixtureSession([])
        def cancelled(event):
            raise RuntimeError("cancelled")
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "synthetic"}, clear=True), \
                patch("nimble_mcp.requests.Session", return_value=session):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                research("Austin TX", cancelled)
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
