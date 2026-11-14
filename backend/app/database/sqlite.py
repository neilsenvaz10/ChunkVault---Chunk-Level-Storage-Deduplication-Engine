"""SQLite implementation of the ``Database`` port.

It composes the existing ``connect``/``transaction``/``initialize_database`` helpers (unchanged), so local behaviour is exactly
what it was before the port existed. The writer lock instance is created once and shared: its in-process thread lock is part of
its mutual exclusion, so a second instance would not exclude the first.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.config import Settings
from app.core.errors import DatabaseError
from app.core.locking import WriterLock
from app.database.connection import connect, transaction
from app.database.init import SCHEMA_VERSION, get_schema_version, initialize_database
from app.database.interfaces import Conn, ColumnSchema, DbHealth, ForeignKeySchema, TableSchema

TABLES = ("files", "chunks", "file_chunks", "operations")  # fixed, whitelisted names (never user input)


class SqliteDatabase:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.lock = WriterLock(settings.lock_path, settings.writer_lock_timeout_seconds)

    # -- lifecycle ---------------------------------------------------------------------------------------
    def prepare_for_startup(self) -> str:
        self._settings.ensure_directories()
        return str(initialize_database(self._settings.db_path))

    # -- connections and transactions ----------------------------------------------------------------------
    @contextmanager
    def connection(self) -> Iterator[Conn]:
        conn = connect(self._settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def transaction(self, conn: Conn):
        return transaction(conn)

    @contextmanager
    def snapshot(self, conn: Conn) -> Iterator[Conn]:
        """Read-only consistent snapshot: with WAL, one read transaction sees one point in time while writers continue."""
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        try:
            yield conn
        finally:
            try:
                conn.execute("ROLLBACK")  # nothing to commit in a read-only view
            finally:
                conn.execute("PRAGMA query_only = OFF")

    def writer_lock(self, timeout_s: float | None = None):
        return self.lock.acquire(timeout_s)

    # -- information -----------------------------------------------------------------------------------------
    def check(self, conn: Conn) -> DbHealth:
        try:
            conn.execute("SELECT 1").fetchone()
            return DbHealth(
                connected=True,
                foreign_keys_enforced=conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1,
                schema_version=str(get_schema_version(conn)),
            )
        except sqlite3.Error:
            return DbHealth(connected=False, foreign_keys_enforced=False)

    def ping(self) -> DbHealth:
        try:
            with self.connection() as conn:
                return self.check(conn)
        except (sqlite3.Error, DatabaseError):
            return DbHealth(connected=False, foreign_keys_enforced=False)

    def checkpoint_and_size(self) -> int:
        """Fold the WAL into the main file and return its size (the local benchmarks' storage-footprint measurement)."""
        with self.connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return self._settings.db_path.stat().st_size

    def schema_version(self) -> str | None:
        with self.connection() as conn:
            return str(get_schema_version(conn))

    def introspect(self) -> tuple[TableSchema, ...]:
        out = []
        with self.connection() as conn:
            for table in TABLES:
                fks = {r["from"]: ForeignKeySchema(table=r["table"], column=r["to"], on_delete=r["on_delete"])
                       for r in conn.execute(f"PRAGMA foreign_key_list({table})")}  # table is from the fixed tuple above
                cols = tuple(
                    ColumnSchema(name=r["name"], type=r["type"], not_null=bool(r["notnull"]), primary_key_position=r["pk"],
                                 default=r["dflt_value"], foreign_key=fks.get(r["name"]))
                    for r in conn.execute(f"PRAGMA table_info({table})"))
                out.append(TableSchema(name=table, columns=cols))
        return tuple(out)

    def footprint_bytes(self) -> int:
        """Main file plus the WAL/shared-memory sidecars (after a checkpoint the WAL is usually empty)."""
        path = self._settings.db_path
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += (path.parent / (path.name + suffix)).stat().st_size
            except OSError:
                pass
        return total

    def expected_schema_version(self) -> int:
        return SCHEMA_VERSION
