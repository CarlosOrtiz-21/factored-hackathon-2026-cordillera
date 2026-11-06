"""SQLite access to the mock bank (data/bank.db, built by pipelines/etl/export.py).

The ETL owns the reference tables (customers, cards, card_transactions). The runtime
tables below (sessions, card_actions) are created here, idempotently, because they
hold state produced by the running service, not by the pipeline.
"""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

RUNTIME_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    token_sha256 TEXT NOT NULL UNIQUE,   -- only the hash is stored, like a password
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS card_actions (
    action_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    product_id TEXT NOT NULL REFERENCES cards(product_id),
    customer_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    approver_id TEXT NOT NULL,
    status_before TEXT NOT NULL,
    status_after TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staff_sessions (
    token_sha256 TEXT PRIMARY KEY,
    staff_id TEXT NOT NULL,
    role TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS threads (       -- which customer owns each conversation
    thread_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_spend (     -- estimated Anthropic spend per UTC day (budget cap)
    day TEXT PRIMARY KEY,
    usd REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS approval_requests (
    thread_id TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    payload TEXT NOT NULL,                 -- masked view for the auditor (no raw PII)
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL,                  -- pending | approved | rejected | expired
    decided_by TEXT,
    decided_at TEXT,
    PRIMARY KEY (thread_id, requested_at)
);
"""


class BankDB:
    """One connection shared across threads, serialized by a lock (SQLite + FastAPI)."""

    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(RUNTIME_SCHEMA)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """All-or-nothing writes: commit on success, roll back on any exception."""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def close(self) -> None:
        self._conn.close()
