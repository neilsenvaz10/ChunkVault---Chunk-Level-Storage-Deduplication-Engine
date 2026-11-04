"""Shared fixtures. Every test gets its own temporary data directory and
database; nothing under the real ``data/`` directory is touched."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.services import build_services
from app.database.connection import connect
from app.database.init import initialize_database
from app.main import create_app

H1 = "a" * 64
H2 = "b" * 64
H3 = "c" * 64


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "test.db",
        cors_origins=("http://localhost:5173",),
        staging_stale_seconds=0,  # leftovers are reported immediately in tests
    )


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "schema.db"
    initialize_database(path)
    conn = connect(path)
    yield conn
    conn.close()


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def add_file(conn, file_id="f1", status="uploading", sha=None, size=10):
    conn.execute(
        "INSERT INTO files(file_id, filename, original_size, sha256, status) VALUES (?,?,?,?,?)",
        (file_id, f"{file_id}.bin", size, sha, status),
    )


def add_chunk(conn, h=H1, size=100, state="stored"):
    conn.execute("INSERT INTO chunks(hash, size_bytes, state) VALUES (?,?,?)", (h, size, state))


def map_chunk(conn, file_id, index, h=H1, size=100):
    conn.execute(
        "INSERT INTO file_chunks(file_id, chunk_index, chunk_hash, chunk_size) VALUES (?,?,?,?)",
        (file_id, index, h, size),
    )


def ref_count(conn, h):
    return conn.execute("SELECT ref_count FROM chunks WHERE hash=?", (h,)).fetchone()[0]


@pytest.fixture
def svc(settings):
    """Concrete services on an isolated temporary database and chunk store."""
    settings.ensure_directories()
    initialize_database(settings.db_path)
    return build_services(settings)


def make_data(seed: int, size: int) -> bytes:
    import random

    return random.Random(seed).randbytes(size)
