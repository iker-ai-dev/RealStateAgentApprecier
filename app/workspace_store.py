"""Durable owner-scoped workspace storage, without sample-data fallbacks.

Owners are opaque session IDs supplied by the server, never client-selected identity.
Snowflake deployments must provision ``SNOWFLAKE_SCHEMA_SQL`` in the session's
database/schema, then run ``SNOWFLAKE_QUOTA_SQL`` once in a serialized admin
migration before enabling paid work. Construction does not connect, create tables,
or alter Snowflake.
Standard Snowflake table keys are informational; use a single writer per owner
when concurrently inserting the same lead ID.
"""
from __future__ import annotations

from contextlib import AbstractContextManager, closing, contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable
from uuid import uuid4


MAX_JSON_BYTES = 1_048_576
MAX_JSON_DEPTH = 20
MAX_LEADS_PER_BATCH = 100
MAX_READ_RECORDS = 1000
MAX_TEXT_BYTES = 65_536
MAX_CONVERSATIONS = 100
MAX_CONVERSATION_PREVIEW = 100
JOB_PATCH_FIELDS = ("status", "leads", "error", "analysis")
MESSAGE_METADATA_FIELDS = ("sources", "model", "usage")

SNOWFLAKE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS RETAILSCOPE_WORKSPACE_RECORDS (
    record_type VARCHAR(16) NOT NULL,
    owner VARCHAR(256) NOT NULL,
    id VARCHAR(256) NOT NULL,
    payload VARIANT NOT NULL,
    updated_at TIMESTAMP_TZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (record_type, owner, id)
)
"""

# Admin migration only: run once, without concurrent seeders. Reservations never insert.
SNOWFLAKE_QUOTA_SQL = """
INSERT INTO RETAILSCOPE_WORKSPACE_RECORDS (record_type, owner, id, payload, updated_at)
SELECT 'quota', 'system', 'daily',
    OBJECT_CONSTRUCT('date', TO_CHAR(CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE,
                                    'YYYY-MM-DD'), 'count', 0),
    CURRENT_TIMESTAMP()
WHERE NOT EXISTS (
    SELECT 1 FROM RETAILSCOPE_WORKSPACE_RECORDS
    WHERE record_type='quota' AND owner='system' AND id='daily'
)
"""


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _daily_limit(limit: int) -> int:
    if type(limit) is not int or not 0 <= limit <= 9_223_372_036_854_775_807:
        raise ValueError("Daily usage limit must be a nonnegative signed 64-bit integer.")
    return limit


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 256:
        raise ValueError(f"{name} must be a nonempty string of at most 256 UTF-8 bytes.")
    return value


def _text(value: str, name: str, limit: int = MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
        raise ValueError(f"{name} must be a string of at most {limit} UTF-8 bytes.")
    return value


def _encode(value: Any) -> str:
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError("Workspace JSON exceeds the nesting limit.")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("Workspace JSON keys must be strings.")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend((child, depth + 1) for child in item)
    try:
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Workspace payload must be finite JSON data.") from exc
    if len(payload.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("Workspace JSON exceeds the byte limit.")
    return payload


def _decode(payload: Any) -> dict:
    if isinstance(payload, str):
        if len(payload.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValueError("Stored workspace JSON exceeds the byte limit.")
        try:
            payload = json.loads(payload)
        except (ValueError, RecursionError) as exc:
            raise ValueError("Invalid stored workspace JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Stored workspace payload must be a JSON object.")
    # Also validate VARIANT objects and return detached data, not driver-owned values.
    return json.loads(_encode(payload))


def _leads(leads: list[dict]) -> list[tuple[str, str]]:
    if not isinstance(leads, list) or len(leads) > MAX_LEADS_PER_BATCH:
        raise ValueError(f"Expected at most {MAX_LEADS_PER_BATCH} leads per batch.")
    records = []
    for lead in leads:
        if not isinstance(lead, dict):
            raise ValueError("Each lead must be a JSON object.")
        lead = dict(lead)
        identifier = lead.get("id")
        if identifier is None:
            url = _text(lead.get("url"), "Lead URL")
            if not url.strip():
                raise ValueError("Each lead needs an ID or a nonempty URL.")
            identifier = hashlib.sha256(url.encode("utf-8")).hexdigest()
            lead["id"] = identifier
        records.append((_identifier(identifier, "Lead ID"), _encode(lead)))
    return records


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _timestamp(updated_at: Any) -> str:
    if isinstance(updated_at, datetime):
        updated_at = updated_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    return _text(updated_at, "Stored timestamp", 128)


def _job(payload: Any, updated_at: Any) -> dict:
    job = _decode(payload)
    job["updated_at"] = _timestamp(updated_at)
    return job


def _conversation(row: Any) -> dict:
    return {"id": _identifier(row[0], "Conversation ID"), "updated_at": _timestamp(row[1]),
            "preview": _text(row[2], "Conversation preview")[:MAX_CONVERSATION_PREVIEW]}


def _recovery_limit(limit: int) -> int:
    if type(limit) is not int or not 1 <= limit <= MAX_READ_RECORDS:
        raise ValueError(f"Recovery limit must be between 1 and {MAX_READ_RECORDS}.")
    return limit


class _StoreInterface:
    def list_leads(self, owner: str) -> list[dict]:
        return self._read("lead", _identifier(owner, "Owner"))

    def upsert_leads(self, owner: str, leads: list[dict]) -> None:
        owner = _identifier(owner, "Owner")
        self._write("lead", owner, _leads(leads))

    def create_job(self, owner: str, query: str) -> dict:
        owner = _identifier(owner, "Owner")
        stamp = _now()
        job = {"id": uuid4().hex, "status": "queued",
               "query": _text(query, "Query", 16_384), "created_at": stamp, "updated_at": stamp}
        self._write("job", owner, [(job["id"], _encode(job))])
        return job

    def update_job(self, owner: str, id: str, status: str,
                   leads: list[dict] | None = None, error: str | None = None,
                   analysis: dict | None = None, expected_status: str | None = None) -> bool:
        """Patch an existing owned job; unknown/foreign IDs are an inert no-op.

        Omitted leads/error/analysis preserve previous fields. Analysis reports
        (answer, sources, usage, model) are separate from collected lead evidence.
        Results belong to the job;
        publishing them to the lead workspace requires an explicit upsert_leads.
        If expected_status is set, update only when the stored status matches.
        Return False for missing/foreign jobs or a status mismatch, True on update.
        """
        owner = _identifier(owner, "Owner")
        id = _identifier(id, "Job ID")
        if expected_status is not None:
            expected_status = _identifier(expected_status, "Expected job status")
        changes = {"status": _identifier(status, "Job status")}
        if leads is not None:
            changes["leads"] = [_decode(payload) for _, payload in _leads(leads)]
        if error is not None:
            changes["error"] = _text(error, "Job error")
        if analysis is not None:
            if not isinstance(analysis, dict):
                raise ValueError("Job analysis must be a JSON object.")
            changes["analysis"] = _decode(analysis)
        _encode(changes)
        return self._patch_job(owner, id, changes, expected_status)

    def get_job(self, owner: str, id: str) -> dict | None:
        records = self._read("job", _identifier(owner, "Owner"), _identifier(id, "Job ID"))
        return records[0] if records else None

    def recover_interrupted_jobs(self, limit: int = MAX_READ_RECORDS) -> int:
        """Fail one bounded batch during startup, before starting any workers.

        Internal/admin use only, just like pending_jobs. Repeat for a larger backlog
        until zero is returned. There are no provider retries; errors propagate.
        Do not run against another instance's live workers without coordination.
        """
        records = self.pending_jobs(limit)
        for record in records:
            self.update_job(record["owner"], record["job"]["id"], "failed",
                            error="Interrupted by server restart; submit a new job to retry.")
        return len(records)

    def append_message(self, owner: str, conversation_id: str, role: str, content: str, *,
                       metadata: dict | None = None) -> None:
        owner = _identifier(owner, "Owner")
        conversation_id = _identifier(conversation_id, "Conversation ID")
        if role not in ("system", "user", "assistant", "tool"):
            raise ValueError("Unsupported message role.")
        payload = {"conversation_id": conversation_id, "role": role,
                   "content": _text(content, "Message content")}
        if metadata is not None:
            if not isinstance(metadata, dict) or any(key not in MESSAGE_METADATA_FIELDS for key in metadata):
                raise ValueError("Message metadata may contain only sources, model, and usage.")
            payload.update(metadata)
        self._write("message", owner, [(uuid4().hex, _encode(payload))])

    def messages(self, owner: str, conversation_id: str) -> list[dict]:
        records = self._read("message", _identifier(owner, "Owner"),
                             conversation_id=_identifier(conversation_id, "Conversation ID"))
        return [{"role": item["role"], "content": item["content"],
                 **{key: item[key] for key in MESSAGE_METADATA_FIELDS if key in item}}
                for item in records]


class WorkspaceStore(_StoreInterface):
    """SQLite store. Each operation owns a connection, safe for background workers."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path if path is not None else os.getenv(
            "RETAILSCOPE_DB_PATH", ".retailscope/workspace.db"
        )).expanduser()
        if str(self.path) == ":memory:":
            raise ValueError("WorkspaceStore requires a durable SQLite file.")
        self.path = self.path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS leads (
                    owner TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY (owner, id)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    owner TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY (owner, id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner TEXT NOT NULL, id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    payload TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (owner, id)
                );
                CREATE INDEX IF NOT EXISTS messages_owner_conversation
                    ON messages(owner, conversation_id, sequence);
            """)
            # Seed only when creating the quota table; a missing existing row stays closed.
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_usage'"
            ).fetchone()
            if exists is None:
                connection.execute("""
                    CREATE TABLE daily_usage (
                        id TEXT PRIMARY KEY CHECK(id='daily'), date TEXT NOT NULL,
                        count INTEGER NOT NULL CHECK(typeof(count)='integer' AND count>=0)
                    )
                """)
                connection.execute("INSERT INTO daily_usage(id, date, count) VALUES (?, ?, ?)",
                                   ("daily", _utc_day(), 0))

    def _connect(self):
        return sqlite3.connect(str(self.path), timeout=30)

    def reserve_daily_usage(self, limit: int) -> bool:
        """Reserve one paid run globally for this UTC day; never refund failures.

        Missing, duplicate, invalid or future-dated counters deny the reservation.
        Database errors propagate, so callers must not start paid work on exceptions.
        """
        limit = _daily_limit(limit)
        if limit == 0:
            return False
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            today = _utc_day()
            rows = connection.execute("SELECT date, count FROM daily_usage WHERE id=? LIMIT 2",
                                      ("daily",)).fetchall()
            if len(rows) != 1:
                return False
            stored_day, count = rows[0]
            try:
                valid_day = isinstance(stored_day, str) and date.fromisoformat(stored_day).isoformat() == stored_day
            except ValueError:
                valid_day = False
            if not valid_day or stored_day > today or type(count) is not int or count < 0:
                return False
            if stored_day == today and count >= limit:
                return False
            next_count = count + 1 if stored_day == today else 1
            result = connection.execute("UPDATE daily_usage SET date=?, count=? WHERE id=?",
                                        (today, next_count, "daily"))
            return result.rowcount == 1

    def _write(self, kind: str, owner: str, records: list[tuple[str, str]]) -> None:
        with closing(self._connect()) as connection, connection:
            stamp = _now()
            if kind == "message":
                connection.executemany(
                    "INSERT INTO messages(owner, id, conversation_id, payload, updated_at) VALUES (?, ?, ?, ?, ?)",
                    [(owner, id, _decode(payload)["conversation_id"], payload, stamp)
                     for id, payload in records],
                )
            else:
                sql = {
                    "lead": "INSERT INTO leads(owner, id, payload, updated_at) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT(owner, id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                    "job": "INSERT INTO jobs(owner, id, payload, updated_at) VALUES (?, ?, ?, ?)",
                }[kind]
                connection.executemany(sql, [
                    (owner, id, payload, _decode(payload)["created_at"] if kind == "job" else stamp)
                    for id, payload in records
                ])

    def _read(self, kind: str, owner: str, id: str | None = None,
              conversation_id: str | None = None) -> list[dict]:
        sql, params = {
            "lead": ("SELECT payload FROM leads WHERE owner=? ORDER BY updated_at, id LIMIT ?",
                     (owner, MAX_READ_RECORDS + 1)),
            "job": ("SELECT payload, updated_at FROM jobs WHERE owner=? AND id=?", (owner, id)),
            "message": ("SELECT payload FROM messages WHERE owner=? AND conversation_id=? "
                        "ORDER BY sequence LIMIT ?", (owner, conversation_id, MAX_READ_RECORDS + 1)),
        }[kind]
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, params).fetchall()
        if len(rows) > MAX_READ_RECORDS:
            raise ValueError("Workspace read exceeds the record limit.")
        return [_job(row[0], row[1]) if kind == "job" else _decode(row[0]) for row in rows]

    def list_conversations(self, owner: str) -> list[dict]:
        """Latest 100 owned conversations, previewed by their first user message."""
        owner = _identifier(owner, "Owner")
        with closing(self._connect()) as connection:
            rows = connection.execute("""
                WITH recent AS (
                    SELECT conversation_id AS id, MAX(updated_at) AS updated_at
                    FROM messages WHERE owner=?
                    GROUP BY conversation_id
                    ORDER BY updated_at DESC, conversation_id LIMIT ?
                )
                SELECT recent.id, recent.updated_at, COALESCE((
                    SELECT substr(json_extract(payload, '$.content'), 1, ?)
                    FROM messages WHERE owner=? AND conversation_id=recent.id
                        AND json_extract(payload, '$.role')=?
                    ORDER BY sequence LIMIT 1
                ), '') AS preview
                FROM recent ORDER BY updated_at DESC, id
            """, (owner, MAX_CONVERSATIONS, MAX_CONVERSATION_PREVIEW, owner, "user")).fetchall()
        return [_conversation(row) for row in rows]

    def pending_jobs(self, limit: int = MAX_READ_RECORDS) -> list[dict]:
        """Internal startup recovery across ALL owners; never expose as a user route.

        Return at most limit records shaped as {owner, job}, oldest updates first.
        This is a read only; callers must fail or resume interrupted work explicitly.
        """
        limit = _recovery_limit(limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT owner, payload, updated_at FROM jobs "
                "WHERE json_extract(payload, '$.status') IN (?, ?) "
                "ORDER BY updated_at, owner, id LIMIT ?", ("queued", "running", limit),
            ).fetchall()
        return [{"owner": row[0], "job": _job(row[1], row[2])} for row in rows]

    def _patch_job(self, owner: str, id: str, changes: dict,
                   expected_status: str | None = None) -> bool:
        with closing(self._connect()) as connection, connection:
            # Serialize read-modify-write across processes, not just Python threads.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM jobs WHERE owner=? AND id=?", (owner, id)).fetchone()
            if row is not None:
                payload = _decode(row[0])
                if expected_status is not None and payload.get("status") != expected_status:
                    return False
                payload.update(changes)
                result = connection.execute("UPDATE jobs SET payload=?, updated_at=? WHERE owner=? AND id=?",
                                            (_encode(payload), _now(), owner, id))
                return result.rowcount == 1
            return False


class SnowflakeWorkspaceStore(_StoreInterface):
    """A preprovisioned Snowflake VARIANT table accessed through a session factory.

    Injected sessions are borrowed by default. Set close_owned_sessions=True for
    a factory creating sessions owned by this adapter, or set contextmanaged_factory
    for a factory returning a lifecycle context manager. Raw Snowpark sessions may
    themselves implement __exit__; borrowed sessions must NOT be entered or closed.
    Bound qmark parameters are used for all values, including JSON and ownership.
    """

    def __init__(self, session_factory: Callable[[], Any], *, close_owned_sessions: bool = False,
                 contextmanaged_factory: bool = False):
        if not callable(session_factory):
            raise TypeError("A Snowpark session factory is required.")
        self.session_factory = session_factory
        self.close_owned_sessions = close_owned_sessions
        self.contextmanaged_factory = contextmanaged_factory

    @contextmanager
    def _session(self):
        resource = self.session_factory()
        if self.contextmanaged_factory:
            if not isinstance(resource, AbstractContextManager):
                raise TypeError("Context-managed session factory must return a context manager.")
            with resource as session:
                yield session
        elif self.close_owned_sessions:
            with closing(resource):
                yield resource
        else:
            yield resource

    def reserve_daily_usage(self, limit: int) -> bool:
        """Reserve one paid run via a single update of the admin-seeded singleton.

        No SELECT-then-write or insertion race. Missing/duplicate/invalid counters
        deny reservations; provider errors propagate without retries or refunds.
        Requires autocommit (not a caller-owned uncommitted transaction).
        """
        limit = _daily_limit(limit)
        if limit == 0:
            return False
        today = _utc_day()
        with self._session() as session:
            result = session.sql("""
                UPDATE RETAILSCOPE_WORKSPACE_RECORDS
                SET payload=OBJECT_CONSTRUCT('date', ?, 'count',
                        IFF(payload:date::STRING=?,
                            TRY_TO_NUMBER(payload:count::STRING, 38, 0)+1, 1)),
                    updated_at=CURRENT_TIMESTAMP()
                WHERE record_type='quota' AND owner='system' AND id='daily'
                    AND (SELECT COUNT(*) FROM RETAILSCOPE_WORKSPACE_RECORDS
                         WHERE record_type='quota' AND owner='system' AND id='daily')=1
                    AND IS_OBJECT(payload) AND IS_INTEGER(payload:count)
                    AND TRY_TO_NUMBER(payload:count::STRING, 38, 0)>=0
                    AND TRY_TO_NUMBER(payload:count::STRING, 38, 0)<=9223372036854775807
                    AND IS_VARCHAR(payload:date)
                    AND TO_CHAR(TRY_TO_DATE(payload:date::STRING, 'YYYY-MM-DD'),
                                'YYYY-MM-DD')=payload:date::STRING
                    AND (payload:date::STRING<? OR
                         (payload:date::STRING=? AND TRY_TO_NUMBER(payload:count::STRING, 38, 0)<?))
            """, params=[today, today, today, today, limit]).collect(
                statement_params={"STATEMENT_TIMEOUT_IN_SECONDS": "30"}, log_on_exception=False,
            )
        return len(result) == 1 and type(result[0][0]) is int and result[0][0] == 1

    def _write(self, kind: str, owner: str, records: list[tuple[str, str]]) -> None:
        if not records:
            return
        with self._session() as session:
            for id, payload in records:
                session.sql("""
                MERGE INTO RETAILSCOPE_WORKSPACE_RECORDS AS target
                USING (SELECT ? AS record_type, ? AS owner, ? AS id,
                              PARSE_JSON(?) AS payload) AS source
                ON target.record_type=source.record_type
                   AND target.owner=source.owner AND target.id=source.id
                WHEN MATCHED THEN UPDATE SET payload=source.payload, updated_at=CURRENT_TIMESTAMP()
                WHEN NOT MATCHED THEN INSERT (record_type, owner, id, payload, updated_at)
                    VALUES (source.record_type, source.owner, source.id, source.payload, CURRENT_TIMESTAMP())
                """, params=[kind, owner, id, payload]).collect(
                    statement_params={"STATEMENT_TIMEOUT_IN_SECONDS": "30"}, log_on_exception=False,
                )

    def _read(self, kind: str, owner: str, id: str | None = None,
              conversation_id: str | None = None) -> list[dict]:
        sql = ("SELECT payload, updated_at FROM RETAILSCOPE_WORKSPACE_RECORDS WHERE record_type=? AND owner=?"
               if kind == "job" else
               "SELECT payload FROM RETAILSCOPE_WORKSPACE_RECORDS WHERE record_type=? AND owner=?")
        params = [kind, owner]
        if id is not None:
            sql += " AND id=?"
            params.append(id)
        if conversation_id is not None:
            sql += " AND payload:conversation_id::STRING=?"
            params.append(conversation_id)
        sql += " ORDER BY updated_at, id LIMIT ?"
        params.append(MAX_READ_RECORDS + 1)
        with self._session() as session:
            rows = session.sql(sql, params=params).collect(
                statement_params={"STATEMENT_TIMEOUT_IN_SECONDS": "30"}, log_on_exception=False,
            )
        if len(rows) > MAX_READ_RECORDS:
            raise ValueError("Workspace read exceeds the record limit.")
        return [_job(row[0], row[1]) if kind == "job" else _decode(row[0]) for row in rows]

    def pending_jobs(self, limit: int = MAX_READ_RECORDS) -> list[dict]:
        """Internal-only {owner, job} recovery records; bounded across all owners."""
        limit = _recovery_limit(limit)
        with self._session() as session:
            rows = session.sql(
                "SELECT owner, payload, updated_at FROM RETAILSCOPE_WORKSPACE_RECORDS "
                "WHERE record_type=? AND payload:status::STRING IN (?, ?) "
                "ORDER BY updated_at, owner, id LIMIT ?",
                params=["job", "queued", "running", limit],
            ).collect(
                statement_params={"STATEMENT_TIMEOUT_IN_SECONDS": "30"}, log_on_exception=False,
            )
        return [{"owner": row[0], "job": _job(row[1], row[2])} for row in rows]

    def list_conversations(self, owner: str) -> list[dict]:
        """Latest 100 owned conversations, previewed by their first user message."""
        owner = _identifier(owner, "Owner")
        with self._session() as session:
            rows = session.sql("""
                WITH scoped AS (
                    SELECT payload:conversation_id::STRING AS conversation_id,
                        updated_at, id AS message_id, payload:role::STRING AS role,
                        payload:content::STRING AS content
                    FROM RETAILSCOPE_WORKSPACE_RECORDS WHERE record_type=? AND owner=?
                ), recent AS (
                    SELECT conversation_id AS id, MAX(updated_at) AS updated_at
                    FROM scoped GROUP BY conversation_id
                    ORDER BY updated_at DESC, conversation_id LIMIT ?
                ), first_user AS (
                    SELECT conversation_id, SUBSTR(content, 1, ?) AS preview
                    FROM scoped WHERE role=?
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY conversation_id ORDER BY updated_at, message_id
                    )=1
                )
                SELECT recent.id, recent.updated_at, COALESCE(first_user.preview, '') AS preview
                FROM recent LEFT JOIN first_user ON recent.id=first_user.conversation_id
                ORDER BY recent.updated_at DESC, recent.id
            """, params=["message", owner, MAX_CONVERSATIONS, MAX_CONVERSATION_PREVIEW, "user"]).collect(
                statement_params={"STATEMENT_TIMEOUT_IN_SECONDS": "30"}, log_on_exception=False,
            )
        return [_conversation(row) for row in rows]

    def _patch_job(self, owner: str, id: str, changes: dict,
                   expected_status: str | None = None) -> bool:
        # Patch fields inside SQL so simultaneous updates do not overwrite other fields.
        expression = "payload"
        params = []
        for key in JOB_PATCH_FIELDS:
            if key in changes:
                expression = f"OBJECT_INSERT({expression}, '{key}', PARSE_JSON(?), TRUE)"
                params.append(_encode(changes[key]))
        sql = "UPDATE RETAILSCOPE_WORKSPACE_RECORDS SET payload=" + expression
        sql += ", updated_at=CURRENT_TIMESTAMP() WHERE record_type=? AND owner=? AND id=?"
        # Include the existing payload in the bound, per-record JSON size budget.
        sql += " AND OCTET_LENGTH(TO_JSON(" + expression + ")) <= ?"
        params.extend(["job", owner, id])
        params.extend(_encode(changes[key]) for key in JOB_PATCH_FIELDS if key in changes)
        params.append(MAX_JSON_BYTES)
        if expected_status is not None:
            sql += " AND payload:status::STRING=?"
            params.append(expected_status)
        with self._session() as session:
            result = session.sql(sql, params=params).collect(
                statement_params={"STATEMENT_TIMEOUT_IN_SECONDS": "30"}, log_on_exception=False,
            )
        if result and result[0][0] > 0:
            return True
        if result:
            job = self.get_job(owner, id)
            if job is not None and (expected_status is None or job.get("status") == expected_status):
                raise ValueError("Updated workspace JSON exceeds the byte limit.")
        return False


def get_store() -> WorkspaceStore | SnowflakeWorkspaceStore:
    """Select only the requested provider; never silently fall back on failure."""
    provider = os.getenv("RETAILSCOPE_STORAGE", "sqlite").strip().lower()
    if provider == "sqlite":
        return WorkspaceStore()
    if provider == "snowflake":
        @contextmanager
        def session_factory():
            from app.data_access import get_snowflake_config, get_snowpark_session
            from snowflake.snowpark import Session

            session = Session.get_active_session()
            if session is None:
                with closing(get_snowpark_session(get_snowflake_config())) as session:
                    yield session
            else:
                yield session

        return SnowflakeWorkspaceStore(session_factory, contextmanaged_factory=True)
    raise ValueError("RETAILSCOPE_STORAGE must be 'sqlite' or 'snowflake'.")
