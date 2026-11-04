"""SQLite connection management.

Every connection is opened through ``connect`` so foreign-key enforcement
(which SQLite makes a per-connection setting, off by default) can never be
forgotten. Transactions are explicit: connections run in autocommit mode and
callers wrap multi-statement work in ``transaction``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.errors import DatabaseError

BUSY_TIMEOUT_MS = 5000


def connect(db_path: str | Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(
            str(db_path),
            isolation_level=None,  # autocommit; transactions are explicit
            timeout=BUSY_TIMEOUT_MS / 1000,
            check_same_thread=False,  # FastAPI may set up/tear down in different threads
        )
    except sqlite3.Error as exc:
        raise DatabaseError(f"cannot open database: {exc}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # FULL: a committed transaction survives power loss (default NORMAL in WAL mode
    # may lose the last commits). Part of the documented local durability model.
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        conn.close()
        raise DatabaseError("SQLite foreign-key enforcement could not be enabled")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block atomically. ``BEGIN IMMEDIATE`` takes the write lock up front
    so a concurrent writer fails fast at the start instead of at commit."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
