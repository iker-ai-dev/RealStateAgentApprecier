from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from app import workspace_store as storage


def _reserve_in_process(args):
    path, limit, attempts = args
    store = storage.WorkspaceStore(path)
    return sum(store.reserve_daily_usage(limit) for _ in range(attempts))


class SQLiteWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "nested" / "workspace.db"
        self.store = storage.WorkspaceStore(self.path)

    def test_empty_is_not_sample_data(self):
        self.assertEqual(self.store.list_leads("owner-a"), [])
        self.assertEqual(self.store.list_conversations("owner-a"), [])
        self.assertEqual(self.store.messages("owner-a", "conversation"), [])
        self.assertIsNone(self.store.get_job("owner-a", "missing"))

    def test_daily_usage_is_durable_and_never_refunded_for_failed_jobs(self):
        self.assertTrue(self.store.reserve_daily_usage(2))
        job = self.store.create_job("owner", "paid run")
        self.store.update_job("owner", job["id"], "failed", error="Provider failed")
        reopened = storage.WorkspaceStore(self.path)
        self.assertTrue(reopened.reserve_daily_usage(2))
        self.assertFalse(storage.WorkspaceStore(self.path).reserve_daily_usage(2))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT count FROM daily_usage WHERE id=?", ("daily",)).fetchone()[0], 2)

    def test_daily_usage_rolls_over_only_to_a_later_utc_day(self):
        today = storage._utc_day()
        tomorrow = (datetime.fromisoformat(today) + timedelta(days=1)).date().isoformat()
        self.assertTrue(self.store.reserve_daily_usage(1))
        self.assertFalse(self.store.reserve_daily_usage(1))
        with patch.object(storage, "_utc_day", return_value=tomorrow):
            self.assertTrue(self.store.reserve_daily_usage(1))
            self.assertFalse(storage.WorkspaceStore(self.path).reserve_daily_usage(1))
        # A delayed old-day caller cannot reset the new-day budget.
        self.assertFalse(self.store.reserve_daily_usage(1))

    def test_daily_usage_is_atomic_across_threads_and_store_instances(self):
        stores = [self.store, storage.WorkspaceStore(self.path)]
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda index: stores[index % 2].reserve_daily_usage(7), range(30)))
        self.assertEqual(sum(results), 7)
        self.assertFalse(self.store.reserve_daily_usage(7))

    def test_daily_usage_is_atomic_across_processes(self):
        with multiprocessing.get_context("spawn").Pool(4) as pool:
            results = pool.map(_reserve_in_process, [(str(self.path), 11, 10)] * 4)
        self.assertEqual(sum(results), 11)
        self.assertFalse(storage.WorkspaceStore(self.path).reserve_daily_usage(11))

    def test_daily_usage_missing_counter_is_not_reseeded_on_restart(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DELETE FROM daily_usage WHERE id=?", ("daily",))
        self.assertFalse(self.store.reserve_daily_usage(10))
        self.assertFalse(storage.WorkspaceStore(self.path).reserve_daily_usage(10))

    def test_daily_usage_duplicate_or_corrupt_counters_fail_closed(self):
        today = storage._utc_day()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DROP TABLE daily_usage")
            connection.execute("CREATE TABLE daily_usage (id TEXT, date TEXT, count)")
        for rows in (
            [("daily", today, 0), ("daily", today, 0)],
            [("daily", "not-a-date", 0)], [("daily", today, -1)],
            [("daily", today, 0.5)], [("daily", today, "0")],
        ):
            with self.subTest(rows=rows):
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    connection.execute("DELETE FROM daily_usage")
                    connection.executemany("INSERT INTO daily_usage VALUES (?, ?, ?)", rows)
                self.assertFalse(storage.WorkspaceStore(self.path).reserve_daily_usage(10))
                with closing(sqlite3.connect(self.path)) as connection:
                    self.assertEqual(connection.execute("SELECT * FROM daily_usage").fetchall(), rows)

    def test_daily_usage_zero_denies_and_invalid_limits_do_not_write(self):
        self.assertFalse(self.store.reserve_daily_usage(0))
        for limit in (-1, True, 1.5, "3", 2**63):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.reserve_daily_usage(limit)
        self.assertTrue(self.store.reserve_daily_usage(1))

    def test_upsert_persistence_and_owner_isolation(self):
        self.store.upsert_leads("owner-a", [{"id": "shared", "title": "First"}])
        self.store.upsert_leads("owner-b", [{"id": "shared", "title": "Private"}])
        self.store.upsert_leads("owner-a", [{"id": "shared", "title": "Updated"}])
        reopened = storage.WorkspaceStore(self.path)
        self.assertEqual(reopened.list_leads("owner-a"), [{"id": "shared", "title": "Updated"}])
        self.assertEqual(reopened.list_leads("owner-b"), [{"id": "shared", "title": "Private"}])
        self.assertEqual(reopened.list_leads("owner-c"), [])

    def test_url_identity_is_stable_without_mutating_input(self):
        lead = {"url": "https://example.com/property", "title": "Actual lead"}
        self.store.upsert_leads("owner", [lead, lead])
        self.assertNotIn("id", lead)
        self.assertEqual(len(self.store.list_leads("owner")), 1)
        self.assertIn("id", self.store.list_leads("owner")[0])

    def test_jobs_are_durable_and_foreign_updates_are_inert(self):
        job = self.store.create_job("owner-a", "Find actual leads")
        self.assertEqual(set(job), {"id", "status", "query", "created_at", "updated_at"})
        self.assertEqual(job["status"], "queued")
        self.assertIsNone(self.store.get_job("owner-b", job["id"]))
        self.store.update_job("owner-b", job["id"], "failed", error="Foreign update")
        self.assertEqual(self.store.get_job("owner-a", job["id"]), job)
        self.store.update_job("owner-a", job["id"], "running")
        self.store.update_job("owner-a", job["id"], "complete", leads=[{"id": "lead"}])
        reopened = storage.WorkspaceStore(self.path)
        result = reopened.get_job("owner-a", job["id"])
        self.assertEqual(result["query"], job["query"])
        self.assertEqual(result["created_at"], job["created_at"])
        self.assertEqual(result["leads"], [{"id": "lead"}])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(reopened.list_leads("owner-a"), [])
        reopened.update_job("owner-a", job["id"], "failed", error="Real error")
        reopened.update_job("owner-a", job["id"], "retrying")
        self.assertEqual(reopened.get_job("owner-a", job["id"])["error"], "Real error")
        reopened.update_job("owner-a", "unknown", "running")
        self.assertIsNone(reopened.get_job("owner-a", "unknown"))

    def test_messages_are_ordered_durable_and_conversation_scoped(self):
        self.store.append_message("a", "shared", "user", "First")
        self.store.append_message("a", "other", "user", "Other conversation")
        self.store.append_message("b", "shared", "assistant", "Other owner")
        self.store.append_message("a", "shared", "assistant", "Second")
        reopened = storage.WorkspaceStore(self.path)
        self.assertEqual(reopened.messages("a", "shared"), [
            {"role": "user", "content": "First"}, {"role": "assistant", "content": "Second"},
        ])
        self.assertEqual(reopened.messages("b", "shared"), [{"role": "assistant", "content": "Other owner"}])

    def test_message_metadata_is_durable_scoped_and_preserves_legacy_shape(self):
        metadata = {"sources": [{"url": "https://example.com/evidence", "title": "Evidence"}],
                    "model": "actual-model", "usage": {"total_tokens": 12}}
        self.store.append_message("owner", "conversation", "user", "Question")
        self.store.append_message("owner", "conversation", "assistant", "Answer", metadata=metadata)
        metadata["sources"][0]["title"] = "Modified input"
        reopened = storage.WorkspaceStore(self.path)
        messages = reopened.messages("owner", "conversation")
        self.assertEqual(messages[0], {"role": "user", "content": "Question"})
        self.assertEqual(messages[1], {"role": "assistant", "content": "Answer",
                                      "sources": [{"url": "https://example.com/evidence", "title": "Evidence"}],
                                      "model": "actual-model", "usage": {"total_tokens": 12}})
        self.assertEqual(reopened.messages("other", "conversation"), [])
        self.store.append_message("owner", "empty", "assistant", "Empty metadata", metadata={})
        self.assertEqual(self.store.messages("owner", "empty"), [{"role": "assistant", "content": "Empty metadata"}])
        self.store.append_message("owner", "partial", "assistant", "Sources only", metadata={"sources": []})
        self.assertEqual(self.store.messages("owner", "partial"), [{"role": "assistant", "content": "Sources only", "sources": []}])

    def test_message_metadata_rejects_unknown_fields_and_unbounded_json(self):
        invalid = [{"role": "system"}, {"conversation_id": "other"}, {"extra": "field"}, [],
                   {"usage": float("nan")}, {"sources": ["x" * storage.MAX_JSON_BYTES]}]
        for metadata in invalid:
            with self.subTest(metadata_type=type(metadata)), self.assertRaises(ValueError):
                self.store.append_message("owner", "conversation", "assistant", "Answer", metadata=metadata)
        self.assertEqual(self.store.messages("owner", "conversation"), [])

    def test_conversations_are_durable_owner_scoped_and_preview_first_user(self):
        self.store.append_message("a", "shared", "system", "Not the preview")
        self.store.append_message("b", "shared", "user", "Other owner's private preview")
        self.store.append_message("a", "shared", "user", "First user message")
        self.store.append_message("a", "shared", "user", "Later user message")
        self.store.append_message("a", "no-user", "assistant", "No user preview")
        self.store.append_message("b", "private", "user", "Private conversation")
        reopened = storage.WorkspaceStore(self.path)
        records = reopened.list_conversations("a")
        self.assertEqual([record["id"] for record in records], ["no-user", "shared"])
        self.assertEqual([record["preview"] for record in records], ["", "First user message"])
        self.assertEqual(set(records[0]), {"id", "updated_at", "preview"})
        self.assertEqual(reopened.list_conversations("unknown"), [])
        self.assertEqual(reopened.messages("a", "shared")[1],
                         {"role": "user", "content": "First user message"})

    def test_conversations_are_capped_latest_first_and_preview_is_100_characters(self):
        for index in range(102):
            stamp = f"2026-01-01T00:{index // 60:02}:{index % 60:02}+00:00"
            with patch.object(storage, "_now", return_value=stamp):
                self.store.append_message("owner", f"conversation-{index:03}", "user", "x" * 150)
        records = self.store.list_conversations("owner")
        self.assertEqual(len(records), 100)
        self.assertEqual(records[0]["id"], "conversation-101")
        self.assertEqual(records[-1]["id"], "conversation-002")
        self.assertTrue(all(record["preview"] == "x" * 100 for record in records))
        with patch.object(storage, "_now", return_value="2026-01-02T00:00:00+00:00"):
            self.store.append_message("owner", "conversation-000", "assistant", "Latest reply")
        latest = self.store.list_conversations("owner")[0]
        self.assertEqual(latest["id"], "conversation-000")
        self.assertEqual(latest["updated_at"], "2026-01-02T00:00:00+00:00")
        self.assertEqual(latest["preview"], "x" * 100)

    def test_conversation_owner_is_validated_and_injection_is_literal(self):
        malicious = "' OR 1=1; --"
        self.store.append_message(malicious, malicious, "user", "Literal content")
        self.assertEqual(self.store.list_conversations(malicious)[0]["id"], malicious)
        self.assertEqual(self.store.list_conversations("other"), [])
        with self.assertRaises(ValueError):
            self.store.list_conversations("")

    def test_sql_injection_is_stored_as_data(self):
        value = "' OR 1=1; DROP TABLE jobs; --"
        self.store.upsert_leads(value, [{"id": value, "title": value}])
        self.store.append_message(value, value, "user", value)
        job = self.store.create_job(value, value)
        self.store.update_job(value, job["id"], value, error=value)
        self.assertEqual(self.store.list_leads(value)[0]["title"], value)
        self.assertEqual(self.store.messages(value, value)[0]["content"], value)
        self.assertEqual(self.store.get_job(value, job["id"])["error"], value)
        self.assertEqual(self.store.list_leads("other"), [])

    def test_validation_is_bounded_and_batch_is_not_partially_written(self):
        invalid_leads = [
            [{"id": "valid"}, {"id": "bad", "number": float("nan")}],
            [{"id": "huge", "value": "x" * storage.MAX_JSON_BYTES}],
            [{"id": "bad", "value": object()}],
            [{"id": "bad", 1: "non-string key"}],
            [{}], [{"id": ""}], [{"id": "x"}] * (storage.MAX_LEADS_PER_BATCH + 1),
        ]
        nested = {}
        for _ in range(storage.MAX_JSON_DEPTH + 1):
            nested = {"nested": nested}
        invalid_leads.append([{"id": "nested", "value": nested}])
        for leads in invalid_leads:
            with self.subTest(leads_type=str(type(leads))), self.assertRaises(ValueError):
                self.store.upsert_leads("owner", leads)
        self.assertEqual(self.store.list_leads("owner"), [])
        for owner in (None, "", " ", "x" * 257):
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                self.store.list_leads(owner)
        with self.assertRaises(ValueError):
            self.store.append_message("a", "c", "invalid", "content")
        with self.assertRaises(ValueError):
            self.store.append_message("a", "c", "user", "x" * (storage.MAX_TEXT_BYTES + 1))

    def test_oversized_job_update_rolls_back(self):
        job = self.store.create_job("owner", "q" * 16_384)
        leads = [{"id": "lead", "value": "x" * (storage.MAX_JSON_BYTES - 100)}]
        with self.assertRaises(ValueError):
            self.store.update_job("owner", job["id"], "complete", leads=leads)
        self.assertEqual(self.store.get_job("owner", job["id"]), job)

    def test_read_limit_and_corrupt_json_fail_explicitly(self):
        with patch.object(storage, "MAX_READ_RECORDS", 1):
            self.store.upsert_leads("owner", [{"id": "1"}, {"id": "2"}])
            with self.assertRaises(ValueError):
                self.store.list_leads("owner")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE leads SET payload=? WHERE owner=? AND id=?", ("broken", "owner", "1"))
        with self.assertRaises(ValueError):
            self.store.list_leads("owner")

    def test_configured_path_and_provider(self):
        with patch.dict(os.environ, {"RETAILSCOPE_DB_PATH": str(self.path), "RETAILSCOPE_STORAGE": "sqlite"}):
            self.assertEqual(storage.get_store().path, self.path.resolve())
        with self.assertRaises(ValueError):
            storage.WorkspaceStore(":memory:")
        with patch.dict(os.environ, {"RETAILSCOPE_STORAGE": "unknown"}), self.assertRaises(ValueError):
            storage.get_store()

    def test_concurrent_job_patches_preserve_independent_fields(self):
        job = self.store.create_job("owner", "query")
        other_store = storage.WorkspaceStore(self.path)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.store.update_job, "owner", job["id"], "complete", [{"id": "lead"}]),
                executor.submit(other_store.update_job, "owner", job["id"], "failed", error="worker error"),
            ]
            for future in futures:
                future.result()
        result = self.store.get_job("owner", job["id"])
        self.assertEqual(result["leads"], [{"id": "lead"}])
        self.assertEqual(result["error"], "worker error")

    def test_expected_status_blocks_stale_workers_and_preserves_terminal_job(self):
        job = self.store.create_job("owner", "query")
        self.assertFalse(self.store.update_job("owner", job["id"], "complete", expected_status="running"))
        self.assertEqual(self.store.get_job("owner", job["id"]), job)
        self.assertTrue(self.store.update_job("owner", job["id"], "running", expected_status="queued"))
        self.assertTrue(self.store.update_job("owner", job["id"], "failed", error="Expired", expected_status="running"))
        terminal = self.store.get_job("owner", job["id"])
        self.assertFalse(self.store.update_job("owner", job["id"], "complete",
                                             analysis={"answer": "Late report"}, expected_status="running"))
        self.assertFalse(self.store.update_job("owner", job["id"], "failed",
                                             error="Late failure", expected_status="running"))
        self.assertEqual(self.store.get_job("owner", job["id"]), terminal)
        self.assertFalse(self.store.update_job("other", job["id"], "running", expected_status="failed"))
        self.assertFalse(self.store.update_job("owner", "missing", "running", expected_status="queued"))
        # Omitting the precondition retains the existing unconditional contract.
        self.assertTrue(self.store.update_job("owner", job["id"], "retrying"))
        with self.assertRaises(ValueError):
            self.store.update_job("owner", job["id"], "running", expected_status="")

    def test_concurrent_terminal_transitions_have_only_one_winner(self):
        job = self.store.create_job("owner", "query")
        self.store.update_job("owner", job["id"], "running", expected_status="queued")
        other_store = storage.WorkspaceStore(self.path)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.store.update_job, "owner", job["id"], "complete", expected_status="running"),
                executor.submit(other_store.update_job, "owner", job["id"], "failed", expected_status="running"),
            ]
            results = [future.result() for future in futures]
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.store.get_job("owner", job["id"])["status"], "complete" if results[0] else "failed")

    def test_paths_remain_stable_and_returned_data_is_detached(self):
        self.assertTrue(self.store.path.is_absolute())
        self.store.upsert_leads("owner", [{"id": "lead", "facts": ["original"]}])
        self.store.list_leads("owner")[0]["facts"].append("changed")
        self.assertEqual(self.store.list_leads("owner")[0]["facts"], ["original"])

    def test_pending_jobs_survive_restart_and_recovery_is_bounded(self):
        first = self.store.create_job("owner-a", "first")
        second = self.store.create_job("owner-b", "second")
        finished = self.store.create_job("owner-c", "finished")
        failed = self.store.create_job("owner-d", "failed")
        self.store.update_job("owner-b", second["id"], "running")
        self.store.update_job("owner-c", finished["id"], "complete")
        self.store.update_job("owner-d", failed["id"], "failed")
        reopened = storage.WorkspaceStore(self.path)
        self.assertEqual(reopened.pending_jobs(1), [{"owner": "owner-a", "job": first}])
        pending = reopened.pending_jobs()
        self.assertEqual([item["owner"] for item in pending], ["owner-a", "owner-b"])
        self.assertEqual(pending[1]["job"]["status"], "running")
        self.assertGreater(pending[1]["job"]["updated_at"], second["updated_at"])
        self.assertEqual(reopened.recover_interrupted_jobs(1), 1)
        self.assertEqual(reopened.get_job("owner-a", first["id"])["status"], "failed")
        self.assertIn("Interrupted", reopened.get_job("owner-a", first["id"])["error"])
        self.assertEqual(reopened.get_job("owner-b", second["id"])["status"], "running")
        self.assertEqual(reopened.recover_interrupted_jobs(), 1)
        self.assertEqual(reopened.recover_interrupted_jobs(), 0)
        self.assertEqual(reopened.pending_jobs(), [])

    def test_legacy_jobs_expose_authoritative_column_timestamp(self):
        job = {"id": "legacy", "status": "running", "query": "old job", "created_at": "2026-01-01T00:00:00+00:00"}
        stamp = "2026-01-02T00:00:00+00:00"
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("INSERT INTO jobs(owner, id, payload, updated_at) VALUES (?, ?, ?, ?)",
                               ("owner", "legacy", json.dumps(job), stamp))
        self.assertEqual(self.store.get_job("owner", "legacy")["updated_at"], stamp)
        self.assertEqual(self.store.pending_jobs()[0]["job"]["updated_at"], stamp)

    def test_analysis_is_durable_and_does_not_overwrite_evidence(self):
        job = self.store.create_job("owner", "query")
        leads = [{"id": "lead", "source_status": "discovery_only", "title": "Source evidence"}]
        self.store.update_job("owner", job["id"], "running", leads=leads)
        analysis = {"answer": "Model report", "sources": [{"url": "https://example.com"}],
                    "usage": {"total_tokens": 10}, "model": "configured-model"}
        self.store.update_job("owner", job["id"], "complete", analysis=analysis)
        analysis["answer"] = "Modified input"
        result = storage.WorkspaceStore(self.path).get_job("owner", job["id"])
        self.assertEqual(result["leads"], leads)
        self.assertEqual(result["analysis"]["answer"], "Model report")
        self.store.update_job("other", job["id"], "complete", analysis={"answer": "foreign"})
        self.assertEqual(self.store.get_job("owner", job["id"]), result)
        self.store.update_job("owner", job["id"], "complete")
        self.assertEqual(self.store.get_job("owner", job["id"])["analysis"], result["analysis"])
        with self.assertRaises(ValueError):
            self.store.update_job("owner", job["id"], "complete", analysis=[])
        with self.assertRaises(ValueError):
            self.store.update_job("owner", job["id"], "complete", analysis={"answer": "x" * storage.MAX_JSON_BYTES})

    def test_recovery_limits_are_validated_before_database_access(self):
        for limit in (0, -1, True, "1", storage.MAX_READ_RECORDS + 1):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.pending_jobs(limit)

    def test_recovery_preserves_partial_evidence_and_analysis(self):
        job = self.store.create_job("owner", "query")
        leads = [{"id": "lead", "title": "Collected evidence"}]
        analysis = {"answer": "Partial report", "sources": [], "usage": {}, "model": "actual"}
        self.store.update_job("owner", job["id"], "running", leads=leads, analysis=analysis)
        reopened = storage.WorkspaceStore(self.path)
        reopened.recover_interrupted_jobs()
        recovered = reopened.get_job("owner", job["id"])
        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(recovered["leads"], leads)
        self.assertEqual(recovered["analysis"], analysis)


class SnowflakeWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.sql.return_value.collect.return_value = []
        self.factory = Mock(return_value=self.session)
        self.store = storage.SnowflakeWorkspaceStore(self.factory)

    def test_construction_does_not_connect_or_provision(self):
        self.factory.assert_not_called()
        self.assertIn("payload VARIANT", storage.SNOWFLAKE_SCHEMA_SQL)
        self.assertIn("PRIMARY KEY (record_type, owner, id)", storage.SNOWFLAKE_SCHEMA_SQL)

    def test_daily_quota_seed_is_admin_only_and_does_not_reset_existing_counter(self):
        self.assertIn("INSERT INTO RETAILSCOPE_WORKSPACE_RECORDS", storage.SNOWFLAKE_QUOTA_SQL)
        self.assertIn("WHERE NOT EXISTS", storage.SNOWFLAKE_QUOTA_SQL)
        self.assertIn("CONVERT_TIMEZONE('UTC'", storage.SNOWFLAKE_QUOTA_SQL)
        self.assertIn("record_type='quota' AND owner='system' AND id='daily'", storage.SNOWFLAKE_QUOTA_SQL)
        self.factory.assert_not_called()

    def test_daily_usage_uses_one_atomic_singleton_update_with_bound_limits(self):
        self.session.sql.return_value.collect.return_value = [(1,)]
        with patch.object(storage, "_utc_day", return_value="2026-10-12"):
            self.assertTrue(self.store.reserve_daily_usage(10))
        self.session.sql.assert_called_once()
        sql, params = self.session.sql.call_args.args[0], self.session.sql.call_args.kwargs["params"]
        self.assertIn("UPDATE RETAILSCOPE_WORKSPACE_RECORDS", sql)
        self.assertIn("SELECT COUNT(*)", sql)
        self.assertIn("id='daily')=1", sql)
        self.assertIn("payload:date::STRING<?", sql)
        self.assertIn("TRY_TO_NUMBER(payload:count::STRING, 38, 0)<?", sql)
        self.assertIn("IS_INTEGER(payload:count)", sql)
        self.assertIn("TRY_TO_DATE", sql)
        self.assertNotIn("MERGE", sql)
        self.assertNotIn("INSERT", sql)
        self.assertEqual(params, ["2026-10-12"] * 4 + [10])
        self.assertEqual(sql.count("?"), len(params))
        self.session.sql.return_value.collect.assert_called_once_with(
            statement_params={"STATEMENT_TIMEOUT_IN_SECONDS": "30"}, log_on_exception=False,
        )

    def test_daily_usage_missing_duplicate_or_ambiguous_results_deny(self):
        for result in ([], [(0,)], [(2,)], [(1,), (1,)]):
            with self.subTest(result=result):
                self.session.sql.return_value.collect.return_value = result
                self.assertFalse(self.store.reserve_daily_usage(10))

    def test_daily_usage_provider_error_propagates_without_retry_and_closes_owned_session(self):
        owned = storage.SnowflakeWorkspaceStore(self.factory, close_owned_sessions=True)
        self.session.sql.return_value.collect.side_effect = RuntimeError("quota unavailable")
        with self.assertRaisesRegex(RuntimeError, "quota unavailable"):
            owned.reserve_daily_usage(10)
        self.session.sql.assert_called_once()
        self.session.close.assert_called_once()

    def test_daily_usage_invalid_limits_or_zero_never_connect(self):
        self.assertFalse(self.store.reserve_daily_usage(0))
        for limit in (-1, True, 1.5, "3", 2**63):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.reserve_daily_usage(limit)
        self.factory.assert_not_called()

    def test_every_snowflake_execution_has_timeout_and_private_logging(self):
        collect = self.session.sql.return_value.collect
        operations = [
            lambda: self.store.list_leads("owner"),
            lambda: self.store.upsert_leads("owner", [{"id": "one"}, {"id": "two"}]),
            lambda: self.store.create_job("owner", "query"),
            lambda: self.store.update_job("owner", "job", "running"),
            lambda: self.store.get_job("owner", "job"),
            lambda: self.store.append_message("owner", "conversation", "user", "content"),
            lambda: self.store.messages("owner", "conversation"),
            lambda: self.store.list_conversations("owner"),
            lambda: self.store.pending_jobs(),
            lambda: self.store.reserve_daily_usage(10),
        ]
        expected = {"statement_params": {"STATEMENT_TIMEOUT_IN_SECONDS": "30"}, "log_on_exception": False}
        for operation in operations:
            with self.subTest(operation=operation):
                collect.reset_mock()
                operation()
                self.assertGreater(collect.call_count, 0)
                for call in collect.call_args_list:
                    self.assertEqual(call.args, ())
                    self.assertEqual(call.kwargs, expected)
        collect.reset_mock()
        collect.side_effect = [[(0,)], []]
        self.store.update_job("owner", "unknown", "failed")
        self.assertEqual(collect.call_count, 2)
        self.assertTrue(all(call.kwargs == expected for call in collect.call_args_list))

    def test_merge_values_and_ownership_are_bound(self):
        malicious = "' OR 1=1; --"
        self.store.upsert_leads(malicious, [{"id": malicious, "title": malicious}])
        sql = self.session.sql.call_args.args[0]
        params = self.session.sql.call_args.kwargs["params"]
        self.assertIn("MERGE INTO RETAILSCOPE_WORKSPACE_RECORDS", sql)
        self.assertIn("target.owner=source.owner", sql)
        self.assertIn("target.record_type=source.record_type", sql)
        self.assertIn("target.id=source.id", sql)
        self.assertIn("PARSE_JSON(?)", sql)
        self.assertNotIn(malicious, sql)
        self.assertEqual(params[:3], ["lead", malicious, malicious])
        self.assertEqual(json.loads(params[3])["title"], malicious)
        self.session.sql.return_value.collect.assert_called_once()

    def test_selects_scope_owner_id_and_conversation(self):
        stamp = "2026-01-01T00:00:00+00:00"
        self.session.sql.return_value.collect.return_value = [(json.dumps({"id": "job"}), stamp)]
        self.assertEqual(self.store.get_job("owner", "job"), {"id": "job", "updated_at": stamp})
        sql, params = self.session.sql.call_args.args[0], self.session.sql.call_args.kwargs["params"]
        self.assertIn("record_type=? AND owner=? AND id=?", sql)
        self.assertEqual(params[:3], ["job", "owner", "job"])
        self.session.sql.return_value.collect.return_value = [({"role": "user", "content": "hello"},)]
        self.assertEqual(self.store.messages("owner", "conversation"), [{"role": "user", "content": "hello"}])
        sql, params = self.session.sql.call_args.args[0], self.session.sql.call_args.kwargs["params"]
        self.assertIn("payload:conversation_id::STRING=?", sql)
        self.assertIn("ORDER BY updated_at, id", sql)
        self.assertEqual(params[:3], ["message", "owner", "conversation"])
        self.session.sql.return_value.collect.return_value = []
        self.assertIsNone(self.store.get_job("other", "job"))
        self.assertEqual(self.store.list_leads("other"), [])
        self.assertEqual(self.session.sql.call_args.kwargs["params"][0:2], ["lead", "other"])

    def test_job_creation_and_message_append_use_unique_record_ids(self):
        first = self.store.create_job("owner", "actual query")
        second = self.store.create_job("owner", "actual query")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["status"], "queued")
        self.store.append_message("owner", "conversation", "user", "Actual message")
        params = self.session.sql.call_args.kwargs["params"]
        self.assertEqual(params[:2], ["message", "owner"])
        self.assertEqual(json.loads(params[3]), {
            "conversation_id": "conversation", "role": "user", "content": "Actual message",
        })

    def test_message_metadata_is_bound_and_restored_without_internal_fields(self):
        metadata = {"sources": [{"url": "https://example.com/evidence"}],
                    "model": "actual-model", "usage": {"total_tokens": 12}}
        self.store.append_message("owner", "conversation", "assistant", "Answer", metadata=metadata)
        params = self.session.sql.call_args.kwargs["params"]
        payload = json.loads(params[3])
        self.assertEqual(payload, {"conversation_id": "conversation", "role": "assistant", "content": "Answer", **metadata})
        self.session.sql.return_value.collect.return_value = [(json.dumps(payload),)]
        self.assertEqual(self.store.messages("owner", "conversation"), [{"role": "assistant", "content": "Answer", **metadata}])
        self.session.sql.reset_mock()
        with self.assertRaises(ValueError):
            self.store.append_message("owner", "conversation", "assistant", "Answer", metadata={"owner": "other"})
        self.session.sql.assert_not_called()

    def test_job_updates_are_atomic_scoped_and_bound(self):
        self.session.sql.return_value.collect.return_value = [(1,)]
        self.store.update_job("owner", "job", "complete", leads=[{"id": "actual"}], error="'quoted'")
        sql, params = self.session.sql.call_args.args[0], self.session.sql.call_args.kwargs["params"]
        self.assertIn("UPDATE RETAILSCOPE_WORKSPACE_RECORDS", sql)
        self.assertIn("WHERE record_type=? AND owner=? AND id=?", sql)
        self.assertIn("OBJECT_INSERT", sql)
        self.assertIn("OCTET_LENGTH", sql)
        self.assertNotIn("'quoted'", sql)
        self.assertEqual(params[3:6], ["job", "owner", "job"])
        self.assertEqual(json.loads(params[0]), "complete")
        self.assertEqual(json.loads(params[1]), [{"id": "actual"}])
        self.assertEqual(json.loads(params[2]), "'quoted'")
        self.assertEqual(sql.count("?"), len(params))
        self.assertEqual(params[-1], storage.MAX_JSON_BYTES)

    def test_expected_status_is_bound_in_atomic_snowflake_update(self):
        self.session.sql.return_value.collect.return_value = [(1,)]
        expected = "running' OR 1=1 --"
        self.assertTrue(self.store.update_job("owner", "job", "complete", expected_status=expected))
        sql, params = self.session.sql.call_args.args[0], self.session.sql.call_args.kwargs["params"]
        self.assertIn("WHERE record_type=? AND owner=? AND id=?", sql)
        self.assertIn("AND payload:status::STRING=?", sql)
        self.assertNotIn(expected, sql)
        self.assertEqual(params[-1], expected)
        self.assertEqual(sql.count("?"), len(params))
        self.assertEqual(self.session.sql.return_value.collect.call_args.kwargs,
                         {"statement_params": {"STATEMENT_TIMEOUT_IN_SECONDS": "30"}, "log_on_exception": False})

    def test_snowflake_status_mismatch_is_false_not_a_size_failure(self):
        stamp = "2026-01-01T00:00:00+00:00"
        self.session.sql.return_value.collect.side_effect = [
            [(0,)], [({"id": "job", "status": "failed"}, stamp)],
        ]
        self.assertFalse(self.store.update_job("owner", "job", "complete", expected_status="running"))
        self.session.sql.return_value.collect.side_effect = [[(0,)], []]
        self.assertFalse(self.store.update_job("other", "job", "complete", expected_status="running"))
        self.session.sql.return_value.collect.side_effect = [
            [(0,)], [({"id": "job", "status": "running"}, stamp)],
        ]
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.store.update_job("owner", "job", "complete", expected_status="running")
        self.session.sql.reset_mock()
        with self.assertRaises(ValueError):
            self.store.update_job("owner", "job", "complete", expected_status="")
        self.session.sql.assert_not_called()

    def test_oversized_update_fails_but_foreign_job_remains_inert(self):
        self.session.sql.return_value.collect.side_effect = [[(0,)], [(json.dumps({"id": "job"}), "2026-01-01T00:00:00+00:00")]]
        with self.assertRaises(ValueError):
            self.store.update_job("owner", "job", "complete")
        self.session.sql.return_value.collect.side_effect = [[(0,)], []]
        self.store.update_job("other", "job", "complete")
        self.assertEqual(self.session.sql.call_args.kwargs["params"][:3], ["job", "other", "job"])

    def test_shared_validation_prevents_queries(self):
        with self.assertRaises(ValueError):
            self.store.upsert_leads("owner", [{"id": "bad", "number": float("inf")}])
        with self.assertRaises(ValueError):
            self.store.create_job("", "query")
        self.factory.assert_not_called()

    def test_storage_errors_propagate_without_local_fallback(self):
        self.factory.side_effect = RuntimeError("Snowflake unavailable")
        with self.assertRaisesRegex(RuntimeError, "Snowflake unavailable"):
            self.store.list_leads("owner")

    def test_read_limits_and_invalid_json_are_not_silently_hidden(self):
        self.session.sql.return_value.collect.return_value = [("not JSON",)]
        with self.assertRaises(ValueError):
            self.store.list_leads("owner")
        self.session.sql.return_value.collect.return_value = [({},), ({},)]
        with patch.object(storage, "MAX_READ_RECORDS", 1), self.assertRaises(ValueError):
            self.store.list_leads("owner")

    def test_explicit_provider_uses_configured_session_factory(self):
        with patch.dict(os.environ, {"RETAILSCOPE_STORAGE": "snowflake"}), \
                patch("app.data_access.get_snowflake_config", return_value={"account": "private"}), \
                patch("snowflake.snowpark.Session.get_active_session", return_value=None), \
                patch("app.data_access.get_snowpark_session", return_value=self.session) as factory:
            store = storage.get_store()
            self.assertIsInstance(store, storage.SnowflakeWorkspaceStore)
            factory.assert_not_called()
            store.list_leads("owner")
            factory.assert_called_once_with({"account": "private"})
            self.session.close.assert_called_once()

    def test_owned_sessions_close_after_every_operation_and_errors(self):
        owned = storage.SnowflakeWorkspaceStore(self.factory, close_owned_sessions=True)
        operations = [
            lambda: owned.list_leads("owner"),
            lambda: owned.upsert_leads("owner", [{"id": "one"}, {"id": "two"}]),
            lambda: owned.create_job("owner", "query"),
            lambda: owned.update_job("owner", "job", "running"),
            lambda: owned.get_job("owner", "job"),
            lambda: owned.append_message("owner", "c", "user", "hello"),
            lambda: owned.messages("owner", "c"),
            lambda: owned.list_conversations("owner"),
            lambda: owned.pending_jobs(),
            lambda: owned.reserve_daily_usage(10),
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                self.session.close.reset_mock()
                operation()
                self.session.close.assert_called_once()
        self.session.sql.return_value.collect.side_effect = RuntimeError("query failed")
        self.session.close.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "query failed"):
            owned.upsert_leads("owner", [{"id": "lead"}])
        self.session.close.assert_called_once()

    def test_borrowed_sessions_are_never_entered_or_closed(self):
        session = MagicMock()
        session.sql.return_value.collect.return_value = []
        borrowed = storage.SnowflakeWorkspaceStore(lambda: session)
        borrowed.list_leads("owner")
        borrowed.update_job("owner", "job", "running")
        session.__enter__.assert_not_called()
        session.__exit__.assert_not_called()
        session.close.assert_not_called()

    def test_contextmanaged_factory_controls_cleanup(self):
        events = []

        @contextmanager
        def factory():
            events.append("enter")
            try:
                yield self.session
            finally:
                events.append("exit")

        store = storage.SnowflakeWorkspaceStore(factory, contextmanaged_factory=True)
        store.list_leads("owner")
        self.session.sql.return_value.collect.side_effect = RuntimeError("query failed")
        with self.assertRaises(RuntimeError):
            store.pending_jobs()
        self.assertEqual(events, ["enter", "exit", "enter", "exit"])
        self.session.close.assert_not_called()

    def test_default_factory_does_not_close_managed_active_session(self):
        with patch.dict(os.environ, {"RETAILSCOPE_STORAGE": "snowflake"}), \
                patch("snowflake.snowpark.Session.get_active_session", return_value=self.session), \
                patch("app.data_access.get_snowpark_session") as create:
            store = storage.get_store()
            store.list_leads("owner")
            self.session.sql.return_value.collect.side_effect = RuntimeError("query failed")
            with self.assertRaises(RuntimeError):
                store.list_leads("owner")
            create.assert_not_called()
            self.session.close.assert_not_called()

    def test_default_factory_closes_owned_session_on_query_error(self):
        with patch.dict(os.environ, {"RETAILSCOPE_STORAGE": "snowflake"}), \
                patch("snowflake.snowpark.Session.get_active_session", return_value=None), \
                patch("app.data_access.get_snowpark_session", return_value=self.session):
            self.session.sql.return_value.collect.side_effect = RuntimeError("query failed")
            with self.assertRaises(RuntimeError):
                storage.get_store().list_leads("owner")
            self.session.close.assert_called_once()

    def test_active_session_lookup_failure_is_not_retried_or_replaced(self):
        with patch.dict(os.environ, {"RETAILSCOPE_STORAGE": "snowflake"}), \
                patch("snowflake.snowpark.Session.get_active_session", side_effect=RuntimeError("ambiguous sessions")) as active, \
                patch("app.data_access.get_snowpark_session") as create:
            with self.assertRaisesRegex(RuntimeError, "ambiguous sessions"):
                storage.get_store().list_leads("owner")
            active.assert_called_once()
            create.assert_not_called()

    def test_pending_jobs_return_owner_job_and_bound_statuses(self):
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.session.sql.return_value.collect.return_value = [
            ("owner-a", json.dumps({"id": "first", "status": "queued"}), stamp),
            ("owner-b", {"id": "second", "status": "running"}, stamp),
        ]
        records = self.store.pending_jobs(2)
        self.assertEqual([record["owner"] for record in records], ["owner-a", "owner-b"])
        self.assertEqual(records[1]["job"]["status"], "running")
        self.assertEqual(records[0]["job"]["updated_at"], stamp.isoformat(timespec="microseconds"))
        sql, params = self.session.sql.call_args.args[0], self.session.sql.call_args.kwargs["params"]
        self.assertIn("payload:status::STRING IN (?, ?)", sql)
        self.assertIn("ORDER BY updated_at, owner, id LIMIT ?", sql)
        self.assertEqual(params, ["job", "queued", "running", 2])
        self.session.sql.reset_mock()
        with self.assertRaises(ValueError):
            self.store.pending_jobs(0)
        self.session.sql.assert_not_called()

    def test_conversation_query_is_owner_scoped_bounded_and_returns_summaries(self):
        malicious = "' OR 1=1; --"
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.session.sql.return_value.collect.return_value = [("conversation", stamp, "First user")]
        records = self.store.list_conversations(malicious)
        self.assertEqual(records, [{"id": "conversation",
                                   "updated_at": stamp.isoformat(timespec="microseconds"),
                                   "preview": "First user"}])
        sql, params = self.session.sql.call_args.args[0], self.session.sql.call_args.kwargs["params"]
        self.assertIn("WHERE record_type=? AND owner=?", sql)
        self.assertIn("FROM scoped WHERE role=?", sql)
        self.assertIn("ORDER BY updated_at, message_id", sql)
        self.assertIn("ORDER BY recent.updated_at DESC, recent.id", sql)
        self.assertIn("LIMIT ?", sql)
        self.assertNotIn(malicious, sql)
        self.assertEqual(params, ["message", malicious, 100, 100, "user"])
        self.assertEqual(sql.count("?"), len(params))
        self.session.sql.return_value.collect.return_value = []
        self.assertEqual(self.store.list_conversations("other"), [])
        self.session.sql.reset_mock()
        with self.assertRaises(ValueError):
            self.store.list_conversations("")
        self.session.sql.assert_not_called()

    def test_analysis_patch_is_bound_and_does_not_patch_leads(self):
        analysis = {"answer": "'quoted' model report", "sources": [], "usage": {}, "model": "actual"}
        self.store.update_job("owner", "job", "complete", analysis=analysis)
        sql, params = self.session.sql.call_args.args[0], self.session.sql.call_args.kwargs["params"]
        self.assertIn("'analysis'", sql)
        self.assertNotIn("'leads'", sql)
        self.assertNotIn(analysis["answer"], sql)
        self.assertEqual(json.loads(params[1]), analysis)
        self.assertEqual(params[2:5], ["job", "owner", "job"])
        self.assertEqual(sql.count("?"), len(params))
        self.session.sql.reset_mock()
        with self.assertRaises(ValueError):
            self.store.update_job("owner", "job", "complete", analysis={"usage": float("nan")})
        self.session.sql.assert_not_called()

    def test_recovery_is_explicit_bounded_and_never_retries_provider_errors(self):
        stamp = "2026-01-01T00:00:00+00:00"
        self.session.sql.return_value.collect.side_effect = [
            [("owner", {"id": "job", "status": "running"}, stamp)], [(1,)],
        ]
        self.assertEqual(self.store.recover_interrupted_jobs(1), 1)
        params = self.session.sql.call_args.kwargs["params"]
        self.assertEqual(json.loads(params[0]), "failed")
        self.assertIn("Interrupted", json.loads(params[1]))
        self.assertEqual(params[2:5], ["job", "owner", "job"])
        self.session.sql.return_value.collect.side_effect = RuntimeError("provider unavailable")
        self.session.sql.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            self.store.recover_interrupted_jobs()
        self.session.sql.assert_called_once()


if __name__ == "__main__":
    unittest.main()
