"""PostgreSQL implementation of the ``Database`` port (psycopg 3 + a small connection pool).

Connections
    Opened in autocommit mode with explicit ``BEGIN``/``COMMIT``/``ROLLBACK`` (see ``transaction``), so nothing is ever
    committed implicitly. A connection returned to the pool is rolled back first if a transaction is somehow still open.
    Construction never connects; the pool opens on first use.

Session settings (statement_timeout, lock_timeout, application_name, search_path)
    Applied with explicit SQL (``set_config``) AFTER every new connection is established, outside any transaction, and VERIFIED
    against ``pg_settings`` before the connection is used -- for pooled connections (pool ``configure`` callback) and for the
    dedicated lock connection alike. They are NOT sent as libpq startup ``options``: Supabase's pooler accepts that parameter but
    silently ignores it (found in C5A: statement_timeout stayed at the 2-minute role default). If the settings cannot be applied or
    do not read back, the connection is closed and a sanitized ``DatabaseError`` is raised; the app never runs on server defaults.
    The pool's ``check`` callback re-verifies them on every checkout (one cheap query that also proves the connection is alive), so a
    connection whose settings were lost is discarded and replaced. Remote hosts get ``sslmode=require`` unless the URL says otherwise.

The writer lock (one lock for upload, delete, reconcile and, later, garbage collection)
    A *session-level advisory lock* held on a DEDICATED connection that is opened outside the pool for the whole protected
    operation and used for nothing else. The lock is released in a ``finally`` block and the session is then ALWAYS closed
    (closing releases every lock the server still attributes to it), so a session that might hold the lock is never returned
    to the pool or reused. Within one process a plain thread lock admits one competitor at a time, so waiting writers do not
    pile up sessions. If the session dies in the middle of the operation the server drops the lock;
    ``ensure_held()`` notices that and ``transaction()`` calls it right before COMMIT, so the operation fails (and rolls back)
    instead of committing without the lock. The lock key is derived from the schema, so test schemas do not interfere.

Requires a session-mode connection (Supabase session pooler or direct). Transaction-mode poolers (port 6543) multiplex
sessions and would silently break session advisory locks; ``CloudSettings.warnings()`` flags that URL.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
from psycopg import errors as pgerr
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus
from psycopg_pool import ConnectionPool, PoolTimeout

from app.core.config import Settings
from app.core.errors import DatabaseError, DatabaseUnavailableError
from app.core.locking import WriterBusyError, WriterLockLostError
from app.database.interfaces import ColumnSchema, Conn, DbHealth, ForeignKeySchema, TableSchema

logger = logging.getLogger("chunkvault.database")

EXPECTED_SCHEMA_VERSION = "0002"
TABLES = ("files", "chunks", "file_chunks", "operations")
_ON_DELETE = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}
_POLL_SECONDS = 0.05
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
APPLICATION_NAME = "chunkvault"


def tls_kwargs(database_url: str) -> dict:
    """``sslmode=require`` for remote servers when the URL does not choose a mode itself; nothing for local test instances."""
    info = conninfo_to_dict(database_url)
    if "sslmode" in info:
        return {}  # the operator's explicit choice (e.g. verify-full) is respected
    hosts = [h.strip().lower() for h in (info.get("host") or "").split(",") if h.strip()]
    if not hosts or all(h.startswith("/") or h in _LOCAL_HOSTS for h in hosts):
        return {}
    return {"sslmode": "require"}


class Row(tuple):
    """A result row addressable by position or by column name (like ``sqlite3.Row``), so callers need not care."""

    def __new__(cls, values, index):
        row = super().__new__(cls, values)
        row._index = index
        return row

    def __getitem__(self, key):
        return super().__getitem__(self._index[key] if isinstance(key, str) else key)

    def keys(self):
        return list(self._index)


def _row_factory(cursor):
    names = [c.name for c in (cursor.description or ())]
    index = {n: i for i, n in enumerate(names)}
    return lambda values: Row(values, index)


def lock_key(schema: str | None) -> int:
    """A stable positive 63-bit advisory-lock key for one ChunkVault schema."""
    digest = hashlib.sha256(f"chunkvault.writer:{schema or 'default'}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


@dataclass
class _Held:
    conn: psycopg.Connection
    lost: bool = False


class PgWriterLock:
    """Same interface as ``core.locking.WriterLock`` (``acquire``, ``is_free``, ``ensure_held``), backed by an advisory lock."""

    def __init__(self, db: "PostgresDatabase", timeout_seconds: float, key: int) -> None:
        self._db, self._timeout, self.key = db, timeout_seconds, key
        self._local = threading.Lock()
        # The lock this execution context currently owns (a thread, or a task): lets ``transaction`` know whether the
        # code that opened it is a lock owner, without changing any caller.
        self._held: contextvars.ContextVar[_Held | None] = contextvars.ContextVar(f"pg_lock_{key}", default=None)

    @contextmanager
    def acquire(self, timeout_seconds: float | None = None) -> Iterator[None]:
        timeout = self._timeout if timeout_seconds is None else timeout_seconds
        if self._held.get() is not None:
            raise WriterBusyError("the storage lock is already held by this operation")  # never self-deadlock
        deadline = time.monotonic() + max(timeout, 0)
        # At most one session per process competes for the lock; other threads of this process wait here, so waiting
        # writers never pile up database sessions (and cannot starve the pool).
        if not self._local.acquire(timeout=max(timeout, 0)):
            raise WriterBusyError("another storage operation is in progress")
        conn = None
        got = False
        try:
            conn = self._db._connect_dedicated()  # NOT from the pool: it is closed, never reused, when the lock is released
            while True:
                try:
                    got = bool(conn.execute("SELECT pg_try_advisory_lock(%s)", (self.key,)).fetchone()[0])
                except psycopg.Error as exc:
                    raise DatabaseUnavailableError("cannot take the storage lock: the database session failed") from exc
                if got:
                    break
                if time.monotonic() >= deadline:
                    raise WriterBusyError("another storage operation is in progress")
                time.sleep(_POLL_SECONDS)
            token = self._held.set(_Held(conn))
            try:
                yield
            finally:
                self._held.reset(token)
        finally:
            try:
                self._release(conn, got)
            finally:
                self._local.release()

    def _release(self, conn: psycopg.Connection | None, got: bool) -> None:
        """Unlock explicitly, then ALWAYS close the session: closing releases any lock the server still attributes to it, so a
        session that might hold the lock can never be reused by anything else."""
        if conn is None:
            return
        try:
            if got and not conn.closed:
                if not conn.execute("SELECT pg_advisory_unlock(%s)", (self.key,)).fetchone()[0]:
                    logger.warning("the storage lock was no longer held at release (its session had been interrupted)")
        except Exception:  # noqa: BLE001 - a broken session: the server already dropped (or will drop) the lock
            pass
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def ensure_held(self) -> None:
        """Raise ``WriterLockLostError`` if the calling context owns the lock but the server no longer holds it for our session.

        A no-op for a context that does not own the lock (read paths, restores).
        """
        held = self._held.get()
        if held is None:
            return
        if held.lost:
            raise WriterLockLostError("the storage lock was lost; the operation was stopped before committing")
        try:
            row = held.conn.execute(
                "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted "
                "AND objsubid = 1 AND ((classid::bigint << 32) | objid::bigint) = %s", (self.key,)).fetchone()
        except psycopg.Error as exc:
            held.lost = True
            raise WriterLockLostError("the storage lock's database session failed; the operation was stopped") from exc
        if row is None:
            held.lost = True
            raise WriterLockLostError("the storage lock is no longer held; the operation was stopped before committing")

    def is_free(self) -> bool:
        """True if no session holds the lock (momentary check)."""
        try:
            with self.acquire(timeout_seconds=0):
                return True
        except WriterBusyError:
            return False


class PostgresDatabase:
    POOL_WAIT_SECONDS = 15.0  # how long a caller waits for a usable pooled connection (tests shorten it)

    def __init__(self, settings: Settings) -> None:
        if settings.backend != "cloud" or settings.cloud is None:
            raise ValueError("PostgresDatabase needs the cloud settings")
        self._settings = settings
        self._c = settings.cloud
        self._pool: ConnectionPool | None = None
        self._pool_guard = threading.Lock()
        self._init_failure: str | None = None  # exception class name of the last failed session initialisation (no details)
        self.lock = PgWriterLock(self, settings.writer_lock_timeout_seconds, lock_key(self._c.pg_schema))

    # -- sessions ----------------------------------------------------------------------------------------
    def session_settings(self) -> dict[str, str]:
        """The effective settings every application connection must have (name -> value as pg_settings reports it)."""
        c = self._c
        values = {"statement_timeout": str(c.db_statement_timeout_ms), "lock_timeout": str(c.db_lock_timeout_ms),
                  "application_name": APPLICATION_NAME}
        if c.pg_schema:
            values["search_path"] = c.pg_schema
        return values

    @staticmethod
    def _read_effective(conn: psycopg.Connection, names) -> dict[str, str]:
        return {r[0]: r[1] for r in conn.execute("SELECT name, setting FROM pg_settings WHERE name = ANY(%s)", (list(names),))}

    @staticmethod
    def _same(name: str, got: str | None, want: str) -> bool:
        if got is None:
            return False
        if name == "search_path":
            return got.replace('"', "").split(",")[0].strip() == want
        return got == want

    def _mismatches(self, conn: psycopg.Connection) -> list[str]:
        want = self.session_settings()
        got = self._read_effective(conn, want)
        return [n for n, v in want.items() if not self._same(n, got.get(n), v)]

    def _init_connection(self, conn: psycopg.Connection) -> None:
        """Apply and verify the session settings on a NEW connection; on any failure close it and raise a sanitized error."""
        try:
            if conn.info.transaction_status != TransactionStatus.IDLE:
                raise RuntimeError("initialisation must run outside a transaction")
            want = self.session_settings()
            names = list(want)
            conn.execute("SELECT " + ", ".join("set_config(%s, %s, false)" for _ in names),
                         [x for n in names for x in (n, want[n])])
            bad = self._mismatches(conn)
            if bad:
                raise RuntimeError("settings did not read back: " + ", ".join(bad))
        except Exception as exc:  # noqa: BLE001 - whatever the cause, this connection must not be used
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._init_failure = type(exc).__name__
            logger.error("a database connection was discarded: its session settings could not be applied or verified (%s)",
                         type(exc).__name__)  # no connection details, no driver message
            raise DatabaseError("the database connection could not be configured: its session settings (timeouts, schema) "
                                "were not applied, so it was discarded") from None
        self._init_failure = None

    def _check_connection(self, conn: psycopg.Connection) -> None:
        """Pool ``check``: proves the connection is alive AND still has its settings; raising makes the pool discard it."""
        bad = self._mismatches(conn)
        if bad:
            raise psycopg.OperationalError("session settings lost: " + ", ".join(bad))

    # -- pool --------------------------------------------------------------------------------------------
    def _conn_kwargs(self) -> dict:
        c = self._c
        # NO "options": the session settings are applied with SQL after connecting (see the module docstring).
        return {"autocommit": True, "row_factory": _row_factory, "connect_timeout": c.db_connect_timeout_seconds,
                "application_name": APPLICATION_NAME, "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10,
                "keepalives_count": 3, **tls_kwargs(c.database_url)}

    def _ensure_pool(self) -> ConnectionPool:
        with self._pool_guard:
            if self._pool is None:
                pool = ConnectionPool(self._c.database_url, kwargs=self._conn_kwargs(), min_size=self._c.pool_min_size,
                                      max_size=self._c.pool_max_size, timeout=self.POOL_WAIT_SECONDS, max_idle=300.0, max_lifetime=1800.0,
                                      configure=self._init_connection, check=self._check_connection, name="chunkvault", open=False)
                pool.open(wait=False)
                self._pool = pool
            return self._pool

    def _checkout(self) -> psycopg.Connection:
        try:
            return self._ensure_pool().getconn()
        except PoolTimeout as exc:
            if self._init_failure:
                raise DatabaseError("the database connection could not be configured: its session settings (timeouts, schema) "
                                    "were not applied, so it was discarded") from None
            raise DatabaseUnavailableError("the database is unavailable (no connection could be obtained)") from exc
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("the database is unavailable") from exc

    def reachable(self, timeout_s: float = 3.0) -> bool:
        """Can a connection be opened and a trivial query answered within ``timeout_s``? One-shot, outside the pool; never raises."""
        try:
            with psycopg.connect(self._c.database_url, connect_timeout=max(1, int(timeout_s)), autocommit=True, **tls_kwargs(self._c.database_url)) as c:
                c.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001 - any failure means "not reachable"; the reason is deliberately not carried (it can name hosts)
            return False

    def _connect_dedicated(self) -> psycopg.Connection:
        """A session of its own (outside the pool) for the writer lock, configured exactly like a pooled one."""
        try:
            conn = psycopg.connect(self._c.database_url, **self._conn_kwargs())
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("the database is unavailable (cannot open the lock session)") from exc
        self._init_connection(conn)  # closes the connection and raises a sanitized DatabaseError on failure
        return conn

    def _checkin(self, conn: psycopg.Connection) -> None:
        try:
            if not conn.closed and conn.info.transaction_status != TransactionStatus.IDLE:
                conn.rollback()  # explicit: a connection that still has a transaction open is never committed implicitly
        except psycopg.Error:
            try:
                conn.close()
            except psycopg.Error:
                pass
        self._ensure_pool().putconn(conn)

    def close(self) -> None:
        with self._pool_guard:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()

    # -- lifecycle ---------------------------------------------------------------------------------------
    def prepare_for_startup(self) -> str:
        """Cloud: VERIFY the schema version only. Migrations are manual (Alembic) and never run from here."""
        for warning in self._c.warnings():
            logger.warning(warning)
        version = self.schema_version()
        if version is None:
            raise DatabaseError("the database schema is missing: run `alembic upgrade head` with the owner connection")
        if version != EXPECTED_SCHEMA_VERSION:
            raise DatabaseError(f"the database schema is at version {version} but this build needs {EXPECTED_SCHEMA_VERSION}: "
                                "run `alembic upgrade head` (or deploy the matching build)")
        return version

    # -- connections and transactions ----------------------------------------------------------------------
    @contextmanager
    def connection(self) -> Iterator[Conn]:
        conn = self._checkout()
        try:
            yield conn
        finally:
            self._checkin(conn)

    @contextmanager
    def transaction(self, conn: Conn) -> Iterator[Conn]:
        """Explicit BEGIN / COMMIT / ROLLBACK. If the caller owns the writer lock, it is re-verified immediately before COMMIT."""
        conn.execute("BEGIN")
        try:
            yield conn
            self.lock.ensure_held()
        except BaseException:
            self._rollback(conn)
            raise
        conn.execute("COMMIT")

    @staticmethod
    def _rollback(conn: Conn) -> None:
        try:
            conn.execute("ROLLBACK")
        except psycopg.Error:
            pass  # the session is gone; the server rolled the transaction back

    @contextmanager
    def snapshot(self, conn: Conn) -> Iterator[Conn]:
        """Read-only, consistent view for the block: one REPEATABLE READ READ ONLY transaction, always rolled back."""
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            yield conn
        finally:
            self._rollback(conn)

    def writer_lock(self, timeout_s: float | None = None):
        return self.lock.acquire(timeout_s)

    # -- information -----------------------------------------------------------------------------------------
    def check(self, conn: Conn) -> DbHealth:
        try:
            conn.execute("SELECT 1").fetchone()
            return DbHealth(connected=True, foreign_keys_enforced=True, schema_version=self._read_version(conn))
        except psycopg.Error:
            return DbHealth(connected=False, foreign_keys_enforced=False)

    def ping(self) -> DbHealth:
        try:
            with self.connection() as conn:
                return self.check(conn)
        except DatabaseError:
            return DbHealth(connected=False, foreign_keys_enforced=False)

    @staticmethod
    def _read_version(conn: Conn) -> str | None:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except pgerr.UndefinedTable:
            return None
        return row[0] if row else None

    def schema_version(self) -> str | None:
        with self.connection() as conn:
            try:
                return self._read_version(conn)
            except pgerr.InsufficientPrivilege as exc:
                raise DatabaseError("the application role cannot read the schema version "
                                    "(re-run scripts/setup_app_role.py after the migration)") from exc

    def expected_schema_version(self) -> str:
        return EXPECTED_SCHEMA_VERSION

    def introspect(self) -> tuple[TableSchema, ...]:
        tables = list(TABLES)
        with self.connection() as conn:
            cols = conn.execute(
                "SELECT table_name, column_name, upper(data_type), is_nullable = 'NO', column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ANY(%s) ORDER BY table_name, ordinal_position",
                (tables,)).fetchall()
            pks = {(r[0], r[1]): r[2] for r in conn.execute(
                "SELECT tc.table_name, kcu.column_name, kcu.ordinal_position FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu ON kcu.constraint_schema = tc.constraint_schema "
                "AND kcu.constraint_name = tc.constraint_name AND kcu.table_name = tc.table_name "
                "WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = current_schema() AND tc.table_name = ANY(%s)",
                (tables,))}
            fks = {(r[0], r[1]): ForeignKeySchema(table=r[2], column=r[3], on_delete=_ON_DELETE[r[4]]) for r in conn.execute(
                "SELECT cl.relname, att.attname, ref.relname, refatt.attname, con.confdeltype FROM pg_constraint con "
                "JOIN pg_class cl ON cl.oid = con.conrelid JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "JOIN pg_class ref ON ref.oid = con.confrelid "
                "JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1] "
                "JOIN pg_attribute refatt ON refatt.attrelid = con.confrelid AND refatt.attnum = con.confkey[1] "
                "WHERE con.contype = 'f' AND n.nspname = current_schema() AND cl.relname = ANY(%s)", (tables,))}
        out: dict[str, list[ColumnSchema]] = {t: [] for t in TABLES}
        for table, name, typ, not_null, default in cols:
            out[table].append(ColumnSchema(name=name, type=typ, not_null=bool(not_null),
                                           primary_key_position=pks.get((table, name), 0), default=default,
                                           foreign_key=fks.get((table, name))))
        return tuple(TableSchema(name=t, columns=tuple(out[t])) for t in TABLES)

    def footprint_bytes(self) -> int:
        """Bytes the ChunkVault tables (with their indexes and TOAST) occupy in the configured schema."""
        with self.connection() as conn:
            return conn.execute(
                "SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0)::bigint FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = current_schema() AND c.relkind = 'r'"
            ).fetchone()[0]
