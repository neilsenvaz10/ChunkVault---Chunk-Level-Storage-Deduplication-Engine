"""Liveness vs readiness, and the connection-pool safety of restores (C5E-1).

C5D measured that 6 concurrent slow restores starved a 4-connection pool: two restores failed with 503 and /api/health timed out,
which on Render would have restarted a healthy service. Restores now hold NO database connection while chunks are fetched, and
liveness never touches the database. Real PostgreSQL (disposable) + the fake Storage server.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.database.postgres import PostgresDatabase
from app.main import create_app
from tests.conftest import make_data
from tests.pg_fixtures import pg_url, requires_postgres, schema_conn, schema_name  # noqa: F401
from tests.test_cloud_workflows import D, KIB, launch  # noqa: F401  (launch is a fixture)

# --------------------------------------------------------------------------------------------- liveness / readiness (local backend)


@pytest.fixture
def local_client(tmp_path):
    s = Settings(data_dir=tmp_path / "d", db_path=tmp_path / "d" / "t.db", cors_origins=("https://app.example",), allowed_hosts=("testserver",))
    with TestClient(create_app(s)) as c:
        yield c


def test_liveness_does_not_open_a_database_connection_but_readiness_does(local_client):
    db = local_client.app.state.services.db
    real, opened = db.connection, []

    def counting():
        opened.append(1)
        return real()

    db.connection = counting
    try:
        assert local_client.get("/api/health/live").status_code == 200
        assert opened == []
        r = local_client.get("/api/health")
        assert r.status_code == 200 and r.json()["status"] == "ok" and len(opened) == 1
    finally:
        db.__dict__.pop("connection", None)


def test_liveness_stays_green_when_the_database_is_down_while_readiness_reports_degraded(local_client):
    import sqlite3

    from app.api.deps import get_db

    class Broken:
        def execute(self, *a):
            raise sqlite3.OperationalError("boom")

    local_client.app.dependency_overrides[get_db] = lambda: Broken()
    live = local_client.get("/api/health/live")
    ready = local_client.get("/api/health")
    assert live.status_code == 200 and live.json()["status"] == "ok"
    assert ready.status_code == 503 and ready.json()["status"] == "degraded"
    assert local_client.get("/api/health/ready").status_code == 503  # the alias behaves identically


def test_ready_is_an_alias_of_health_and_stays_out_of_the_published_contract(local_client):
    a, b = local_client.get("/api/health").json(), local_client.get("/api/health/ready").json()
    assert a == b
    paths = local_client.get("/api/openapi.json").json()["paths"]
    assert "/api/health/live" in paths and "/api/health" in paths and "/api/health/ready" not in paths


# --------------------------------------------------------------------------------------------- cloud: pool safety (PostgreSQL)
pg = [pytest.mark.postgres, requires_postgres]


def checked_out(db):
    st = db._pool.get_stats()
    return st["pool_size"] - st["pool_available"]


@pytest.fixture
def fast_pool_failure(monkeypatch):
    """A starved pool must fail within seconds in a test, not after the production 15 s."""
    monkeypatch.setattr(PostgresDatabase, "POOL_WAIT_SECONDS", 3.0)


@pytest.mark.postgres
@requires_postgres
def test_a_restore_holds_no_database_connection_while_it_fetches_chunks(launch, fast_pool_failure):
    c = launch({"pool_max_size": 2, "io_workers": 3})
    data = make_data(100, 300 * KIB)
    fid = c.upload(data).json()["file_id"]
    seen = []
    real_read = c.svc.store.read

    def spying_read(h):
        seen.append(checked_out(c.svc.db))  # connections checked out of the pool RIGHT NOW, mid-download
        return real_read(h)

    c.svc.store.read = spying_read
    r = c.client.get(f"/api/files/{fid}/download", headers=D)
    assert r.status_code == 200 and r.content == data
    assert len(seen) >= 10 and max(seen) == 0, f"connections held during chunk fetches: {sorted(set(seen))}"
    assert checked_out(c.svc.db) == 0
    assert c.q("SELECT status FROM operations WHERE operation_type='restore'") == [("succeeded",)]  # bookkeeping still recorded


@pytest.mark.postgres
@requires_postgres
def test_many_slow_concurrent_restores_never_starve_the_pool_or_readiness(launch, fast_pool_failure):
    """The C5D scenario in miniature: more slow restores than pooled connections."""
    c = launch({"pool_max_size": 2, "io_workers": 3, "max_concurrent_restores": 8})  # the gate is tested separately; this is about the pool
    data = make_data(101, 400 * KIB)
    fid = c.upload(data).json()["file_id"]
    c.fake.latency = 0.3  # each restore now takes seconds
    results, probes = [], []

    def restore():
        r = c.client.get(f"/api/files/{fid}/download", headers=D)
        results.append((r.status_code, r.content == data))

    threads = [threading.Thread(target=restore) for _ in range(6)]  # 6 restores, 2 pooled connections
    [t.start() for t in threads]
    time.sleep(0.8)
    while any(t.is_alive() for t in threads):
        t0 = time.perf_counter()
        probes.append((c.client.get("/api/health").status_code, c.client.get("/api/health/live").status_code, time.perf_counter() - t0))
        time.sleep(0.4)
    [t.join(60) for t in threads]
    assert results == [(200, True)] * 6, results  # every restore succeeded, byte-identical (before the fix: 503s)
    assert probes and all(r == 200 and l == 200 for r, l, _ in probes), probes  # readiness never starved
    assert max(t for _, _, t in probes) < 2.0
    assert c.q("SELECT status, count(*) FROM operations WHERE operation_type='restore' GROUP BY 1") == [("succeeded", 6)]


@pytest.mark.postgres
@requires_postgres
def test_an_upload_is_not_blocked_by_slow_restores_holding_every_pooled_connection(launch, fast_pool_failure):
    c = launch({"pool_max_size": 2, "io_workers": 3, "max_concurrent_restores": 8})
    fid = c.upload(make_data(102, 300 * KIB)).json()["file_id"]
    c.fake.latency = 0.3
    done = []
    threads = [threading.Thread(target=lambda: done.append(c.client.get(f"/api/files/{fid}/download", headers=D).status_code)) for _ in range(4)]
    [t.start() for t in threads]
    time.sleep(0.8)
    r = c.upload(make_data(103, 40 * KIB), "while-restoring.bin")
    [t.join(60) for t in threads]
    assert r.status_code == 201 and done == [200] * 4


@pytest.mark.postgres
@requires_postgres
def test_liveness_stays_green_when_every_pooled_connection_is_taken(launch, fast_pool_failure):
    c = launch({"pool_max_size": 2, "startup_retry_seconds": 0.1})
    with c.svc.db.connection(), c.svc.db.connection():  # the pool is now completely exhausted
        t0 = time.perf_counter()
        assert c.client.get("/api/health/live").status_code == 200
        assert time.perf_counter() - t0 < 1.0  # immediate: it never waited for a connection
        ready = c.client.get("/api/health")
        assert ready.status_code == 503 and ready.json()["status"] == "degraded"  # readiness honestly reports it, in its own shape
    time.sleep(0.3)  # connections are back; the next request re-checks the database (after the short retry interval) ...
    assert c.client.get("/api/health").status_code == 200  # ... and readiness recovers by itself


@pytest.mark.postgres
@requires_postgres
def test_a_file_deleted_in_the_middle_of_a_restore_still_restores_exactly_what_was_snapshotted(launch):
    """Consistency: file row and manifest come from one snapshot, so a concurrent delete cannot produce a mixed or partial file."""
    c = launch({"io_workers": 2})
    data = make_data(104, 200 * KIB)
    fid = c.upload(data).json()["file_id"]
    real_read, state = c.svc.store.read, {"deleted": False}

    def read_and_delete_once(h):
        if not state["deleted"]:
            state["deleted"] = True
            assert c.client.delete(f"/api/files/{fid}", headers=D).status_code == 200  # the file disappears mid-restore
        return real_read(h)

    c.svc.store.read = read_and_delete_once
    r = c.client.get(f"/api/files/{fid}/download", headers=D)
    assert r.status_code == 200 and r.content == data  # complete and verified against the snapshotted SHA-256
    assert c.client.get(f"/api/files/{fid}/download", headers=D).status_code in (404, 409, 422)  # afterwards it is gone
    assert c.q("SELECT status FROM operations WHERE operation_type='restore' ORDER BY operation_id")[0] == ("succeeded",)


@pytest.mark.postgres
@requires_postgres
def test_a_failed_restore_still_records_its_outcome_on_a_fresh_connection_and_cleans_up(launch, fast_pool_failure):
    c = launch({"pool_max_size": 2, "storage_max_attempts": 1})
    data = make_data(105, 120 * KIB)
    fid = c.upload(data).json()["file_id"]
    victim = c.q("SELECT chunk_hash FROM file_chunks WHERE file_id=%s ORDER BY chunk_index LIMIT 1", fid)[0][0]
    c.fake.objects.pop(f"{c.fake.bucket}/{victim[:2]}/{victim}")
    r = c.client.get(f"/api/files/{fid}/download", headers=D)
    assert r.status_code >= 400 and data not in r.content
    assert c.q("SELECT status FROM operations WHERE operation_type='restore'") == [("failed",)]
    assert checked_out(c.svc.db) == 0 and list(c.svc.settings.staging_dir.iterdir()) == []


@pytest.mark.postgres
@requires_postgres
def test_integrity_is_unchanged_corrupt_chunks_are_still_refused(launch):
    c = launch({"io_workers": 2})
    data = make_data(106, 100 * KIB)
    fid = c.upload(data).json()["file_id"]
    victim = c.q("SELECT chunk_hash FROM file_chunks WHERE file_id=%s ORDER BY chunk_index LIMIT 1", fid)[0][0]
    good = c.fake.get(victim)
    c.fake.put(victim, bytes([good[0] ^ 1]) + good[1:])
    r = c.client.get(f"/api/files/{fid}/download", headers=D)
    assert r.status_code >= 400 and data not in r.content  # a corrupt chunk is never returned, connection or no connection
