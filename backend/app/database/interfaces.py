"""The ``Database`` port: everything the engine needs from a metadata database besides the repository's queries.

Two implementations exist (or will): ``SqliteDatabase`` (development, tests, rollback; app/database/sqlite.py) and the PostgreSQL
one for the cloud backend. Callers receive an opaque connection object (``Conn``) from ``connection()`` and hand it back to the
repository and to ``transaction``/``snapshot``; they never learn which database is behind it.

Design reference: cloud-migration/phase-b-design.md, section 2.2.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

Conn = Any  # sqlite3.Connection or psycopg.Connection: opaque outside the database/repository modules


@dataclass(frozen=True)
class DbHealth:
    connected: bool
    foreign_keys_enforced: bool
    schema_version: str | None = None


@dataclass(frozen=True)
class ForeignKeySchema:
    table: str
    column: str
    on_delete: str


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    type: str
    not_null: bool
    primary_key_position: int  # 0 when not part of the primary key
    default: str | None
    foreign_key: ForeignKeySchema | None = None


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: tuple[ColumnSchema, ...]


class Database(Protocol):
    """One instance per process. Methods that open resources are context managers: resources are always released."""

    def prepare_for_startup(self) -> str:
        """Make the database usable and return its schema version.

        ``local``: create the data directories and apply the idempotent schema (as before).
        ``cloud``: only *verify* that the schema is at the expected version and raise ``DatabaseError`` otherwise.
        Migrations are manual (Alembic) and are never run from application startup.
        """

    def connection(self) -> AbstractContextManager[Conn]:
        """A connection for the duration of the block; pooled (cloud) or freshly opened (local); always closed/returned."""

    def transaction(self, conn: Conn) -> AbstractContextManager[Conn]:
        """Run a block atomically: commit on success, roll back on any exception."""

    def snapshot(self, conn: Conn) -> AbstractContextManager[Conn]:
        """A read-only, consistent view of the metadata for the duration of the block (used by the auditor)."""

    def writer_lock(self, timeout_s: float | None = None) -> AbstractContextManager[None]:
        """Exclusive right to modify storage; raises ``WriterBusyError`` when not obtained within the timeout.

        The same object is available as ``lock`` (with ``acquire``/``is_free``/``ensure_held``). It covers the WHOLE conflicting
        operation (upload, delete, reconcile, garbage collection) and is released in a ``finally`` block. ``transaction`` re-verifies
        an owned lock right before COMMIT, so an operation whose lock was lost fails instead of committing.
        """

    def ping(self) -> DbHealth:
        """Health on a connection of its own; never raises."""

    def check(self, conn: Conn) -> DbHealth:
        """Health on the caller's connection (the health endpoint's per-request connection); never raises a driver error."""

    def schema_version(self) -> str | None: ...

    def introspect(self) -> tuple[TableSchema, ...]:
        """Tables, columns, primary and foreign keys of the five ChunkVault tables, for the schema explorer."""

    def footprint_bytes(self) -> int:
        """Bytes the metadata database occupies (SQLite file and sidecars / ``pg_database_size``)."""
