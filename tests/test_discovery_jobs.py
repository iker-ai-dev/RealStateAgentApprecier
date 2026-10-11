"""Offline cached discovery lifecycle, isolation, and evidence contracts."""
from concurrent.futures import wait
from threading import Event
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

from app.deal_radar_ui import filter_leads, load_leads
from app.discovery_jobs import GROUPS, scan_source, source_group, start_scan


def finish(job):
    wait(job.workers.values(), timeout=3)
    job.poll()
    if job.summary is not None:
        wait([job.summary], timeout=3)
    return job.poll()


class DiscoveryJobs(unittest.TestCase):
    def test_saved_evidence_order_and_filter_semantics(self):
        records, warning = load_leads()
        self.assertIsNone(warning)
        job = finish(start_scan(records, filter_leads))
        self.assertEqual(job.result["leads"], filter_leads(records))
        self.assertEqual(job.result["observed"], len(records))
        self.assertEqual(list(job.workers), list(GROUPS))
        self.assertTrue(all(value == "Complete" for value in job.statuses().values()))
        self.assertFalse(job.active)
        self.assertIsNotNone(job.finished_at)
        self.assertIs(job.poll(), job)

    def test_nonblocking_and_bounded_futures(self):
        release = Event()
        entered = Event()

        def held(rows):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Test worker timeout")
            return scan_source(rows)

        with patch("app.discovery_jobs.scan_source", side_effect=held):
            job = start_scan([], filter_leads)
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(len(job.workers), 3)
                self.assertEqual(job.executor._max_workers, 3)
                self.assertTrue(job.poll().active)
                self.assertFalse(job.summary.done())
                self.assertIn("Running", job.statuses().values())
            finally:
                release.set()
                finish(job)

    def test_deep_snapshot_and_session_isolation(self):
        row = {"id": "a", "url": "https://broker.example/a", "missing_fields": ["T12"]}
        first = start_scan([row], filter_leads)
        row["missing_fields"].append("leases")
        second = start_scan([], filter_leads)
        finish(first)
        finish(second)
        self.assertEqual(first.result["leads"][0]["missing_fields"], ["T12"])
        self.assertEqual(second.result["leads"], [])
        self.assertIsNot(first.executor, second.executor)

    def test_resources_unknowns_and_source_vintage(self):
        records = [{"id": "lead", "retrieved_at": "2025-01-01", "active_status": "unknown"},
                   {"lead_kind": "listing_directory", "url": "https://crexi.com/properties/TX"}]
        job = finish(start_scan(records, filter_leads, {"include_unknown": False}))
        self.assertEqual(job.result["observed"], 1)
        self.assertEqual(job.result["resources"], 1)
        self.assertEqual(job.result["unknown_availability"], 1)
        self.assertEqual(job.result["leads"], [])
        self.assertEqual(job.result["vintages"], ["2025-01-01", "Unknown"])

    def test_failure_is_terminal_sanitized_and_refreshable(self):
        with patch("app.discovery_jobs.scan_source", side_effect=RuntimeError("private credential")):
            job = finish(start_scan([], filter_leads))
        self.assertFalse(job.active)
        self.assertIsNone(job.result)
        self.assertNotIn("private", job.error)
        self.assertEqual(job.statuses()["Screen & summarize"], "Error")
        self.assertIsNotNone(finish(start_scan([], filter_leads)).result)

    def test_screen_failure(self):
        job = start_scan([], lambda rows, **kwargs: 1 / 0)
        wait(job.workers.values(), timeout=3)
        job.poll()
        wait([job.summary], timeout=3)
        job.poll()
        self.assertFalse(job.active)
        self.assertIsNone(job.result)
        self.assertIn("screening failed", job.error)

    def test_bounds_and_no_network(self):
        for records in (None, [{}] * 101, ["not a record"]):
            with self.assertRaises(ValueError):
                start_scan(records, filter_leads)
        with patch("requests.sessions.Session.request", side_effect=AssertionError("No network")):
            self.assertEqual(finish(start_scan([], filter_leads)).result["observed"], 0)

    def test_malformed_url_and_automatic_pool_cleanup_without_poll(self):
        for url in ("https://[invalid", "https://[::1", "https://example.com/x"):
            self.assertEqual(source_group({"url": url}), GROUPS[2])
        job = start_scan([], filter_leads)
        job.summary.result(timeout=3)
        for thread in job.executor._threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())

    def test_obsolete_job_cancels_without_publishing(self):
        job = start_scan([], filter_leads)
        job.cancel()
        self.assertFalse(job.poll().active)
        self.assertIsNone(job.result)
        for thread in job.executor._threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())

    def test_public_demo_hides_live_research_and_filter_scan_agrees(self):
        script = "from app.deal_radar_ui import render_deal_radar\nrender_deal_radar()"
        with patch.dict("os.environ", {"RETAILSCOPE_PUBLIC_DEMO": "true"}), \
                patch("app.research_ui.render_listing_search") as live:
            app = AppTest.from_string(script).run()
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(any(item.label == "Optional live research connection" for item in app.expander))
            first = app.session_state["radar_discovery_job"]
            finish(first)
            app.checkbox[0].uncheck().run()
            current = finish(app.session_state["radar_discovery_job"])
            self.assertIsNot(first, current)
            self.assertEqual(len(current.result["leads"]),
                             len([button for button in app.button if button.label == "Review this lead"]))
            live.assert_not_called()


if __name__ == "__main__":
    unittest.main()
