"""Fixtures for tests that need a real PostgreSQL (marked ``postgres``).

They read ``TEST_DATABASE_URL`` (an OWNER connection to a THROW-AWAY database; see scripts/test_postgres.py) and skip when it is
absent, so the default suite never depends on a database. Every test works in its own schema, created and dropped around it, so
nothing outside that schema is touched.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

BACKEND = Path(__file__).resolve().parents[1]
MIGRATIONS_UP = [BACKEND / "migrations" / "sql" / n for n in ("0001_initial.sql", "0002_chunk_deleting_state.sql")]
MIGRATION_SQL = MIGRATIONS_UP[0]  # kept for tests that exercise the first migration on its own


def apply_migrations(conn) -> None:
    for path in MIGRATIONS_UP:
        conn.execute(path.read_text(encoding="utf-8"))


def stamp_version(conn) -> None:
    """What `alembic upgrade head` leaves behind: the version table (owner-owned), at the version this build expects."""
    from app.database.postgres import EXPECTED_SCHEMA_VERSION

    conn.execute("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
    conn.execute("INSERT INTO alembic_version VALUES (%s)", (EXPECTED_SCHEMA_VERSION,))


def test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


requires_postgres = pytest.mark.skipif(
    not test_database_url(),
    reason="TEST_DATABASE_URL is not set (start a throw-away server with scripts/test_postgres.py)",
)


@pytest.fixture
def pg_url() -> str:
    url = test_database_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


@pytest.fixture
def schema_name() -> str:
    return "t_" + uuid.uuid4().hex[:12]


@pytest.fixture
def schema_conn(pg_url, schema_name):
    """Owner connection (autocommit) whose search_path is a fresh schema with the migration applied."""
    conn = psycopg.connect(pg_url, autocommit=True)
    conn.execute(f'CREATE SCHEMA "{schema_name}"')
    conn.execute(f'SET search_path TO "{schema_name}"')
    apply_migrations(conn)
    try:
        yield conn
    finally:
        conn.execute("RESET search_path")
        conn.execute(f'DROP SCHEMA "{schema_name}" CASCADE')
        conn.close()


H = lambda c: c * 64  # a valid 64-hex hash made of one repeated hex digit  # noqa: E731


def add_file(conn, file_id="f1", status="uploading", sha=None, size=10, name="a.bin"):
    conn.execute("INSERT INTO files(file_id, filename, original_size, sha256, status) VALUES (%s,%s,%s,%s,%s)",
                 (file_id, name, size, sha, status))


def add_chunk(conn, h=None, size=100, state="stored"):
    conn.execute("INSERT INTO chunks(hash, size_bytes, state) VALUES (%s,%s,%s)", (h or H("a"), size, state))


def map_chunk(conn, file_id, index, h=None, size=100):
    conn.execute("INSERT INTO file_chunks(file_id, chunk_index, chunk_hash, chunk_size) VALUES (%s,%s,%s,%s)",
                 (file_id, index, h or H("a"), size))


def ref(conn, h):
    return conn.execute("SELECT ref_count FROM chunks WHERE hash=%s", (h,)).fetchone()[0]


# ---------------------------------------------------------------------------------------------------------- PostgresDatabase

def cloud_settings(url: str, schema: str, tmp_path, **cloud_kw):
    from app.core.config import CloudSettings, Settings

    kw = dict(database_url=url, supabase_url="https://example.supabase.co", supabase_service_key="s" * 40,
              demo_token="d" * 40, admin_token="a" * 40, pg_schema=schema, pool_max_size=4)
    kw.update(cloud_kw)
    return Settings(data_dir=tmp_path, db_path=tmp_path / "unused.db", cors_origins=("https://app.example",),
                    backend="cloud", cloud=CloudSettings(**kw), writer_lock_timeout_seconds=2.0)


@pytest.fixture
def pgdb(pg_url, schema_conn, schema_name, tmp_path):
    """A PostgresDatabase on a fresh migrated schema (owner connection), as Alembic would leave it."""
    from app.database.postgres import PostgresDatabase

    stamp_version(schema_conn)
    db = PostgresDatabase(cloud_settings(pg_url, schema_name, tmp_path))
    try:
        yield db
    finally:
        db.close()
