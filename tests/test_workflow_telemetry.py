"""Exercise actual public workflows through an offline Sentry transport."""
import json
import os
import unittest
from unittest.mock import patch

import sentry_sdk
from sentry_sdk.transport import Transport

from app import observability as obs
from app import mcp_server
from app.deal_radar_ui import filter_leads, load_leads
from app.discovery_jobs import start_scan


class MemoryTransport(Transport):
    def __init__(self):
        super().__init__()
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)


class WorkflowTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"RETAILSCOPE_PUBLIC_DEMO": "true"}, clear=True)
        self.env.start()
        self.network = patch("socket.socket", side_effect=AssertionError("Network prohibited"))
        self.network.start()
        self.transport = MemoryTransport()
        self.assertTrue(obs.init_sentry(dsn="https://public@example.invalid/1",
                                       traces_sample_rate=1, transport=self.transport))

    def tearDown(self):
        obs._disable_owned_client()
        for scope in (sentry_sdk.get_current_scope(), sentry_sdk.get_isolation_scope(),
                      sentry_sdk.get_global_scope()):
            scope.clear()
            scope.set_client(None)
        self.network.stop()
        self.env.stop()

    def transactions(self):
        return [item.payload.json for envelope in self.transport.envelopes
                for item in envelope.items if item.type == "transaction"]

    def test_mcp_and_background_workers_emit_real_workflow_traces(self):
        self.assertEqual(len(mcp_server.list_shopping_centers()), 3)
        mcp_server.get_shopping_center_context("SC-001")
        mcp_server.appraise_shopping_center("SC-001")
        mcp_server.ask_retailscope("SC-001", "PRIVATE_INPUT_CANARY What are the risks?")
        records, _ = load_leads()
        job = start_scan(records, filter_leads)
        job.summary.result(timeout=5)
        job.poll()
        sentry_sdk.flush()
        transactions = self.transactions()
        names = [item["transaction"] for item in transactions]
        for name in ("retailscope_ai.mcp.list_centers", "retailscope_ai.mcp.get_context",
                     "retailscope_ai.mcp.appraise", "retailscope_ai.mcp.ask",
                     "retailscope_ai.discovery"):
            self.assertIn(name, names)
        self.assertEqual(names.count("retailscope_ai.discovery_source"), 3)
        self.assertTrue(all(item["contexts"]["trace"]["status"] == "ok" for item in transactions))
        operations = {span["op"] for item in transactions for span in item["spans"]}
        self.assertTrue({"db.load", "app.valuation", "ai.fallback", "discovery.scan",
                         "discovery.screen"}.issubset(operations))
        payload = b"".join(envelope.serialize() for envelope in self.transport.envelopes)
        for forbidden in (b"PRIVATE_INPUT_CANARY", b"Westgate", b"SC-001"):
            self.assertNotIn(forbidden, payload)

    def test_rejected_tool_input_has_failed_trace_and_correlated_error(self):
        with self.assertRaises(ValueError):
            mcp_server.get_shopping_center_context("PRIVATE_UNKNOWN_CANARY")
        sentry_sdk.flush()
        transaction = self.transactions()[0]
        self.assertNotEqual(transaction["contexts"]["trace"]["status"], "ok")
        errors = [item.payload.json for envelope in self.transport.envelopes
                  for item in envelope.items if item.type == "event"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["contexts"]["trace"]["trace_id"],
                         transaction["contexts"]["trace"]["trace_id"])
        self.assertNotIn("PRIVATE_UNKNOWN_CANARY", json.dumps(errors))

    def test_ask_invalid_property_is_traced_before_answer_generation(self):
        with self.assertRaises(ValueError):
            mcp_server.ask_retailscope("UNKNOWN", "What are the risks?")
        self.assertEqual(self.transactions()[0]["transaction"], "retailscope_ai.mcp.ask")
        self.assertEqual(self.transactions()[0]["contexts"]["trace"]["status"], "internal_error")

    def test_research_error_is_captured_once_and_cortex_fallback_is_degraded(self):
        from app.listing_discovery import discover_listings
        from app.services import cortex
        with patch("app.listing_discovery.NimbleMCP", side_effect=RuntimeError("PRIVATE_CANARY")):
            with self.assertRaises(RuntimeError):
                discover_listings("unused", "unused")
        errors = [item for envelope in self.transport.envelopes
                  for item in envelope.items if item.type == "event"]
        self.assertEqual(len(errors), 1)
        with patch.object(cortex, "get_snowflake_config", return_value={"cortex_model": "configured"}), \
             patch.object(cortex, "get_snowpark_session", side_effect=RuntimeError("PRIVATE_CANARY")), \
             patch.object(cortex, "render_research_answer", return_value="fallback"):
            self.assertEqual(cortex.answer_agent("risks", None, None, None, None, None), "fallback")
        self.assertEqual(self.transactions()[-1]["contexts"]["trace"]["status"], "internal_error")
        self.assertNotIn(b"PRIVATE_CANARY", b"".join(e.serialize() for e in self.transport.envelopes))


if __name__ == "__main__":
    unittest.main()
