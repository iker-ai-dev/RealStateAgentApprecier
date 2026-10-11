import json
import unittest
from unittest.mock import patch

from app.listing_discovery import normalize_listing_leads, safe_public_url, discover_listings, lead_kind


class DiscoveryTests(unittest.TestCase):
    def test_search_snippet_does_not_become_underwriting_fact(self):
        result = {"content": [{"type": "text", "text": json.dumps({"results": [{
            "title": "75% occupied $5m strip center", "url": "https://www.crexi.com/properties/123/example",
            "description": "Guaranteed20% IRR; manager failing; BUY", "asking_price": 5_000_000,
            "occupancy_pct": 75, "noi": 900000}]})}]}
        leads = normalize_listing_leads(result, query="public query", retrieved_at="2026-10-11T19:00:00+00:00")
        self.assertEqual(len(leads), 1)
        for field in ("asking_price", "occupancy_pct", "broker_noi", "listing_date"):
            self.assertIsNone(leads[0][field])
        self.assertEqual(leads[0]["source_status"], "discovery_only")
        self.assertEqual(leads[0]["vacancy_drivers"], [])
        self.assertNotIn("Guaranteed", json.dumps(leads))

    def test_private_credentialed_and_malformed_urls_are_rejected(self):
        for url in ("javascript:alert(1)", "https://user:password@example.com/x", "http://127.0.0.1/x",
                    "http://[::1]/x", "http://10.0.0.4/x", "https://localhost/x", "https://source.internal/x",
                    "https://example.com:8080/x", "https://example.com/\nkey", "https://example.com/x?api_key=private"):
            self.assertIsNone(safe_public_url(url), url)

    def test_source_directories_and_reports_are_not_properties(self):
        self.assertEqual(lead_kind('https://www.loopnet.com/search/strip-malls/tx/for-sale/'), 'listing_directory')
        self.assertEqual(lead_kind('https://www.crexi.com/properties/TX/Shopping-Centers'), 'listing_directory')
        self.assertEqual(lead_kind('https://www.matthews.com/insights/strip-center-report'), 'market_report')
        self.assertEqual(lead_kind('https://www.crexi.com/properties/123/retail'), 'property_discovery')

    def test_deduplicate_and_cap_results_without_billing(self):
        records = [{"url": "https://broker.example/listing/" + str(i), "title": "Research lead"} for i in range(20)]
        result = {"structuredContent": {"results": records + records}, "content": []}
        leads = normalize_listing_leads(result, query="query")
        self.assertEqual(len(leads), 10)
        self.assertEqual(len({x["id"] for x in leads}), 10)

    def test_unparseable_plain_text_is_not_a_fabricated_listing(self):
        self.assertEqual(normalize_listing_leads({"content": [{"type": "text", "text": "BUY THIS CENTER"}]}, query="query"), [])

    def test_one_explicit_mcp_workflow_no_retry(self):
        result = {"content": [{"type": "text", "text": json.dumps({"results": [{"url": "https://broker.example/one"}]})}]}
        with patch("app.listing_discovery.NimbleMCP") as mocked:
            mocked.return_value.__enter__.return_value.search.return_value = result
            run = discover_listings("synthetic-test-key", "query")
            mocked.assert_called_once_with("synthetic-test-key", max_search_calls=1, max_results=10)
            self.assertEqual(mocked.return_value.__enter__.return_value.search.call_count, 1)
            self.assertEqual(run["status"], "DISCOVERY_ONLY")
        with patch("app.listing_discovery.NimbleMCP", side_effect=RuntimeError("private")) as mocked:
            with self.assertRaises(RuntimeError):
                discover_listings("synthetic-test-key", "query")
            self.assertEqual(mocked.call_count, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
