"""
Phase 3: SQLite persistence layer.

Switched from PostgreSQL to SQLite: no server to install or run, no
password/port to configure, just a local file. Given the install
friction already hit with Docker/Redis, this avoids repeating that for
a piece of the project that doesn't need a full database server.

No setup required beyond having Python itself (sqlite3 is built in).
The database file is created automatically on first run.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


DB_PATH = os.environ.get("NDI_MONITOR_DB_PATH", "ndi_monitor.db")


@contextmanager
def get_connection():
    # A fresh connection per call keeps this safe to use from both the
    # background ingest thread and FastAPI's request threadpool without
    # sharing a single connection object across threads.
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Creates tables if they don't already exist. Safe to call on every startup."""
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")  # better concurrent read/write
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unknown'
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fault_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                severity TEXT NOT NULL,
                fault_type TEXT NOT NULL,
                message TEXT NOT NULL,
                detected_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fault_events_source_time
            ON fault_events (source_name, detected_at DESC);
        """)


def upsert_source(name: str, status: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO sources (name, first_seen, last_seen, status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                last_seen = excluded.last_seen,
                status = excluded.status;
        """, (name, now, now, status))


def insert_fault_events(source_name: str, faults: list) -> None:
    """faults: list of FaultEvent (from fault_classifier.py)."""
    if not faults:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO fault_events (source_name, severity, fault_type, message, detected_at)
            VALUES (?, ?, ?, ?, ?);
        """, [
            (source_name, f.severity.value, f.fault_type, f.message, now)
            for f in faults
        ])


def get_recent_faults(source_name: str, limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT severity, fault_type, message, detected_at
            FROM fault_events
            WHERE source_name = ?
            ORDER BY detected_at DESC
            LIMIT ?;
        """, (source_name, limit)).fetchall()
        return [dict(row) for row in rows]
