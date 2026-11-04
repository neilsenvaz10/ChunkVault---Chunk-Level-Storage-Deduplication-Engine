"""Database initialisation. Creates tables only; never inserts data."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.errors import DatabaseError
from app.database.connection import connect

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def get_schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def initialize_database(db_path: str | Path) -> int:
    """Create the schema if absent. Idempotent. Returns the schema version."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        current = get_schema_version(conn)
        if current > SCHEMA_VERSION:
            raise DatabaseError(
                f"database schema version {current} is newer than supported {SCHEMA_VERSION}"
            )
        try:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise DatabaseError(f"schema initialisation failed: {exc}") from exc
        return get_schema_version(conn)
    finally:
        conn.close()
