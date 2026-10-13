from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import ExitStack
import os
from threading import Event
import unittest
from unittest.mock import Mock, patch

from app import workspace_store as storage


def session():
    result = Mock()
    result.sql.return_value.collect.return_value = []
    return result


class WorkspaceSessionPoolTests(unittest.TestCase):
    def pool(self, factory=None, **kwargs):
        pool = storage._WorkspaceSessionPool(factory or Mock(side_effect=session), **kwargs)
        self.addCleanup(pool.close)
        return pool

    def test_lazy_reuse_and_idempotent_shutdown(self):
        create = Mock(side_effect=session)
        pool = self.pool(create)
        create.assert_not_called()
        with pool.lease() as first:
            pass
        with pool.lease() as second:
            self.assertIs(first, second)
        create.assert_called_once()
        first.close.assert_not_called()
        pool.close()
        pool.close()
        first.close.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            with pool.lease():
                self.fail("Closed pool leased a session")

    def test_capacity_and_wait_are_bounded(self):
        create = Mock(side_effect=session)
        pool = self.pool(create, size=2, wait_timeout=0.01)
        with ExitStack() as stack:
            first = stack.enter_context(pool.lease())
            second = stack.enter_context(pool.lease())
            self.assertIsNot(first, second)
            with self.assertRaisesRegex(TimeoutError, "no operation was started"):
                with pool.lease():
                    self.fail("Exceeded capacity")
            self.assertEqual(create.call_count, 2)
        with pool.lease():
            self.assertEqual(create.call_count, 2)

    def test_age_expiry_replaces_session_before_next_operation(self):
        pool = self.pool(max_age=10)
        with patch.object(storage, "monotonic", return_value=0):
            with pool.lease() as first:
                pass
        with patch.object(storage, "monotonic", return_value=10):
            with pool.lease() as second:
                self.assertIsNot(first, second)
                first.close.assert_called_once()

    def test_age_expiry_does_not_interrupt_active_transaction(self):
        pool = self.pool(max_age=10)
        with patch.object(storage, "monotonic", return_value=0) as clock:
            with pool.lease() as first:
                clock.return_value = 10
                first.close.assert_not_called()
            first.close.assert_called_once()

    def test_use_limit_retires_session(self):
        pool = self.pool(max_uses=2)
        with pool.lease() as first:
            pass
        first.close.assert_not_called()
        with pool.lease() as second:
            self.assertIs(first, second)
        first.close.assert_called_once()
        with pool.lease() as third:
            self.assertIsNot(first, third)

    def test_shutdown_defers_closing_active_lease(self):
        pool = self.pool()
        with pool.lease() as active:
            pool.close()
            active.close.assert_not_called()
        active.close.assert_called_once()

    def test_authentication_failure_releases_slot_without_retry(self):
        create = Mock(side_effect=[RuntimeError("authentication failed"), session()])
        pool = self.pool(create, size=1, wait_timeout=0.01)
        with self.assertRaisesRegex(RuntimeError, "authentication failed"):
            with pool.lease():
                self.fail("Authentication should fail")
        create.assert_called_once()
        with pool.lease():
            self.assertEqual(create.call_count, 2)

    def test_errors_and_cancellation_discard_without_masking_or_retry(self):
        for error in (RuntimeError("query failed"), KeyboardInterrupt()):
            with self.subTest(error=type(error)):
                first, second = session(), session()
                first.close.side_effect = RuntimeError("close failed")
                create = Mock(side_effect=[first, second])
                pool = self.pool(create, size=1, wait_timeout=0.01)
                with self.assertRaises(type(error)) as caught:
                    with pool.lease():
                        raise error
                self.assertIs(caught.exception, error)
                create.assert_called_once()
                first.close.assert_called_once()
                with pool.lease() as replacement:
                    self.assertIs(replacement, second)

    def test_close_error_does_not_turn_successful_write_into_failure(self):
        owned = session()
        owned.close.side_effect = RuntimeError("close failed")
        pool = self.pool(Mock(return_value=owned), max_uses=1)
        store = storage.SnowflakeWorkspaceStore(pool.lease, contextmanaged_factory=True)
        store.upsert_leads("owner", [{"id": "lead"}])
        owned.sql.assert_called_once()
        owned.close.assert_called_once()

    def test_failed_writes_are_not_replayed_and_next_request_gets_new_session(self):
        for operation in (
            lambda store: store.upsert_leads("owner", [{"id": "a"}, {"id": "b"}]),
            lambda store: store.reserve_daily_usage(5),
            lambda store: store.update_job("owner", "job", "complete"),
        ):
            with self.subTest(operation=operation):
                first, second = session(), session()
                first.sql.return_value.collect.side_effect = RuntimeError("ambiguous write")
                create = Mock(side_effect=[first, second])
                pool = self.pool(create)
                store = storage.SnowflakeWorkspaceStore(pool.lease, contextmanaged_factory=True)
                with self.assertRaisesRegex(RuntimeError, "ambiguous write"):
                    operation(store)
                create.assert_called_once()
                first.sql.assert_called_once()
                first.close.assert_called_once()
                self.assertEqual(store.list_leads("other-owner"), [])
                self.assertEqual(create.call_count, 2)

    def test_commit_and_rollback_failure_preserve_original_error_and_discard(self):
        owned = session()
        statements = []

        def sql(statement, params=None):
            statements.append(statement)
            result = Mock()
            if statement == "COMMIT":
                result.collect.side_effect = RuntimeError("ambiguous commit")
            elif statement == "ROLLBACK":
                result.collect.side_effect = RuntimeError("rollback failed")
            else:
                result.collect.return_value = []
            return result

        owned.sql.side_effect = sql
        create = Mock(return_value=owned)
        pool = self.pool(create)
        store = storage.SnowflakeWorkspaceStore(pool.lease, contextmanaged_factory=True)
        with patch.object(store, "_reserve_agent_slot", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "ambiguous commit"):
                store.enroll_agent("owner", {"id": "agent", "expires_at": "later"})
        self.assertEqual(statements[0], "BEGIN")
        self.assertEqual(statements[-2:], ["COMMIT", "ROLLBACK"])
        self.assertEqual(len(statements), 4)
        create.assert_called_once()
        owned.close.assert_called_once()

    def test_transaction_lease_excludes_reads_until_commit(self):
        owned = session()
        begun, release, read_started = Event(), Event(), Event()
        statements = []

        def sql(statement, params=None):
            statements.append(statement)
            result = Mock()

            def collect(**kwargs):
                if statement == "BEGIN":
                    begun.set()
                    if not release.wait(5):
                        raise TimeoutError("Test transaction was not released")
                return []

            result.collect.side_effect = collect
            return result

        owned.sql.side_effect = sql
        pool = self.pool(Mock(return_value=owned), size=1)
        store = storage.SnowflakeWorkspaceStore(pool.lease, contextmanaged_factory=True)

        def read():
            read_started.set()
            return store.list_leads("other-owner")

        with patch.object(store, "_reserve_agent_slot", return_value=True):
            with ThreadPoolExecutor(max_workers=2) as executor:
                write = executor.submit(store.enroll_agent, "owner", {"id": "agent", "expires_at": "later"})
                try:
                    self.assertTrue(begun.wait(5))
                    reader = executor.submit(read)
                    self.assertTrue(read_started.wait(5))
                    with self.assertRaises(FutureTimeout):
                        reader.result(timeout=0.05)
                    self.assertEqual(statements, ["BEGIN"])
                finally:
                    release.set()
                self.assertTrue(write.result(timeout=5))
                self.assertEqual(reader.result(timeout=5), [])
        self.assertEqual(statements[-2], "COMMIT")
        self.assertTrue(statements[-1].startswith("SELECT"))

    def test_default_store_reuses_connection_not_owner_results(self):
        owned = session()

        def sql(statement, params=None):
            self.assertIn("owner=?", statement)
            result = Mock()
            result.collect.return_value = [({"id": params[1]},)]
            return result

        owned.sql.side_effect = sql
        with patch.dict(os.environ, {"RETAILSCOPE_STORAGE": "snowflake"}), \
                patch("app.data_access.get_managed_snowpark_session", return_value=None), \
                patch("app.data_access.get_snowflake_config", return_value={"account": "test"}), \
                patch("app.data_access.get_snowpark_session", return_value=owned) as create:
            store = storage.get_store()
            for owner in ("owner-a", "owner-b", "owner-a"):
                self.assertEqual(store.list_leads(owner), [{"id": owner}])
            create.assert_called_once()
            self.assertEqual(owned.sql.call_count, 3)
            owned.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
