import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.investigator import plan_followups


class InvestigatorTests(unittest.TestCase):
    def provider(self, queries):
        return Mock(complete=Mock(return_value=SimpleNamespace(analysis={"queries": queries})))

    def test_one_bounded_structured_call(self):
        provider = self.provider(["Phoenix strip center rent roll", "Phoenix strip center rent roll"])
        queries = plan_followups("Phoenix", [{"title": "Untrusted", "summary": "x" * 5000}] * 20,
                                 ["tenant rents"], provider=provider)
        self.assertEqual(queries, ["Phoenix strip center rent roll"])
        provider.complete.assert_called_once()
        prompt = provider.complete.call_args.args[0]
        self.assertLess(len(prompt), 16000)
        self.assertIn("untrusted", prompt)

    def test_invalid_output_never_becomes_search(self):
        for queries in (["site:private.example secrets"], ["https://broker.example"],
                        ["www.broker.com"], ["x" * 241], ["     "], ["one query"] * 3):
            with self.subTest(queries=queries), self.assertRaises(Exception):
                plan_followups("Phoenix", [], [], provider=self.provider(queries))

    def test_empty_plan_and_failure_no_retry(self):
        self.assertEqual(plan_followups("Phoenix", [], [], provider=self.provider([])), [])
        provider = Mock()
        provider.complete.side_effect = RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            plan_followups("Phoenix", [], [], provider=provider)
        provider.complete.assert_called_once()
