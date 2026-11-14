"""End-to-end cloud workflows: the real FastAPI app on a disposable local PostgreSQL + the fake Supabase Storage server.

Nothing here reaches a real Supabase project: the database is a throw-away local cluster (TEST_DATABASE_URL) and storage is a
fake HTTP server on 127.0.0.1 with fault injection. Needs PostgreSQL; skipped otherwise.
"""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import CloudSettings, Settings
from app.core.errors import ConfigurationError
from app.database.postgres import EXPECTED_SCHEMA_VERSION
from app.core.locking import WriterBusyError, WriterLockLostError
from app.deduplication.interfaces import QuotaExceededError
from app.main import create_app
from tests.conftest import make_data
from tests.fake_supabase import KEY, FakeStorage
from tests.pg_fixtures import pg_url, requires_postgres, schema_conn, schema_name, stamp_version  # noqa: F401

pytestmark = [pytest.mark.postgres, requires_postgres]

DEMO = "demo-token-" + "d" * 30
ADMIN = "admin-token-" + "a" * 30
D = {"Authorization": f"Bearer {DEMO}"}
A = {"Authorization": f"Bearer {ADMIN}"}
KIB = 1024


class Cloud:
    """One running cloud-mode application plus its fake storage and an owner connection for assertions."""

    def __init__(self, client, svc, fake, owner):
        self.client, self.svc, self.fake, self.owner = client, svc, fake, owner

    def upload(self, data: bytes, name="a.bin", headers=D):
        return self.client.post("/api/files", files={"file": (name, data)}, headers=headers)

    def q(self, sql, *args):
        return [tuple(r) for r in self.owner.execute(sql, args)]

    def one(self, sql, *args):
        return self.q(sql, *args)[0][0]

    def terminate_lock_session(self):
        (pid,) = [r[0] for r in self.owner.execute(
            "SELECT pid FROM pg_locks WHERE locktype='advisory' AND granted AND ((classid::bigint << 32) | objid::bigint) = %s",
            (self.svc.db.lock.key,))]
        self.owner.execute("SELECT pg_terminate_backend(%s)", (pid,))


@pytest.fixture
def launch(pg_url, schema_conn, schema_name, tmp_path, caplog):
    stamp_version(schema_conn)
    started = []

    def make(cloud_kw=None, settings_kw=None, sub="app") -> Cloud:
        fake = FakeStorage()
        ck = dict(database_url=pg_url, supabase_url=fake.url, supabase_service_key=KEY, demo_token=DEMO, admin_token=ADMIN,
                  bucket=fake.bucket, pg_schema=schema_name, storage_max_attempts=2, storage_timeout_seconds=3.0,
                  storage_connect_timeout_seconds=2.0, io_workers=3, batch_chunks=8, orphan_min_age_seconds=900.0,
                  audit_deep_chunks=500)
        ck.update(cloud_kw or {})
        sk = dict(data_dir=tmp_path / sub, db_path=tmp_path / sub / "unused.db", cors_origins=("https://app.example",),
                  backend="cloud", cloud=CloudSettings(**ck), allowed_hosts=("testserver", "localhost"),
                  writer_lock_timeout_seconds=1.5, staging_stale_seconds=3600, max_upload_bytes=4 * 1024 * KIB)
        sk.update(settings_kw or {})
        settings = Settings(**sk)
        app = create_app(settings)
        client = TestClient(app)
        client.__enter__()
        started.append((client, fake))
        return Cloud(client, app.state.services, fake, schema_conn)

    yield make
    for client, fake in started:
        client.__exit__(None, None, None)
        fake.close()


def keys_of(resp_json: dict) -> set[str]:
    return set(resp_json)


# ============================================================================================ wiring and startup

def test_cloud_app_starts_verifies_the_schema_and_serves_public_reads(launch):
    c = launch()
    r = c.client.get("/api/health")
    assert r.status_code == 200 and r.json()["database"]["schema_version"] == int(EXPECTED_SCHEMA_VERSION)
    assert c.client.get("/api/system/config").status_code == 200
    assert c.client.get("/api/files").json()["total"] == 0
    assert c.fake.requests == []  # starting and reading never touch the object store


def test_startup_refuses_a_schema_at_the_wrong_version(launch, schema_conn):
    schema_conn.execute("UPDATE alembic_version SET version_num = '0000'")
    with pytest.raises(Exception, match="schema is at version 0000"):
        launch()


def test_one_switch_selects_postgres_and_supabase_together(launch):
    from app.database.postgres import PostgresDatabase
    from app.storage.supabase import SupabaseChunkStore

    c = launch()
    assert isinstance(c.svc.db, PostgresDatabase) and isinstance(c.svc.store, SupabaseChunkStore)
    assert c.svc.settings.backend == "cloud" and not (c.svc.settings.chunks_dir.exists())  # no local chunk directory exists


def test_default_limits_are_unchanged_locally_and_conservative_in_the_cloud():
    assert Settings.from_env({}).max_upload_bytes == 64 * 1024 * KIB
    env = {"CHUNKVAULT_BACKEND": "cloud", "DATABASE_URL": "postgresql://u:p@h.example:5432/d", "SUPABASE_URL": "https://x.supabase.co",
           "SUPABASE_SERVICE_KEY": KEY, "CHUNKVAULT_DEMO_TOKEN": DEMO, "CHUNKVAULT_ADMIN_TOKEN": ADMIN,
           "CHUNKVAULT_CORS_ORIGINS": "https://app.example"}
    assert Settings.from_env(env).max_upload_bytes == 10 * 1024 * KIB
    with pytest.raises(ConfigurationError):
        Settings.from_env({**env, "CHUNKVAULT_MAX_UPLOAD_BYTES": str(400 * 1024 * KIB)})  # above the 300 MiB quota


# ============================================================================================ authentication

@pytest.mark.parametrize("method,path,needs", [
    ("POST", "/api/files", "demo"), ("DELETE", "/api/files/" + "0" * 32, "demo"), ("GET", "/api/files/" + "0" * 32 + "/download", "demo"),
    ("POST", "/api/integrity/audit", "admin"), ("POST", "/api/integrity/reconcile", "admin"), ("POST", "/api/admin/gc", "admin"),
    ("POST", "/api/benchmarks", "admin"), ("POST", "/api/reliability/experiments", "admin"), ("POST", "/api/simulations", "demo"),
])
def test_mutations_and_maintenance_require_the_right_token(launch, method, path, needs):
    c = launch()
    base = len(c.fake.requests)
    none = c.client.request(method, path)
    wrong = c.client.request(method, path, headers={"Authorization": "Bearer nope-" + "x" * 30})
    malformed = c.client.request(method, path, headers={"Authorization": DEMO})  # no 'Bearer' scheme
    for r in (none, wrong, malformed):
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
        assert DEMO not in r.text and ADMIN not in r.text and KEY not in r.text
    assert len(c.fake.requests) == base  # a rejected request never reaches the object store
    demo = c.client.request(method, path, headers=D)
    if needs == "admin":
        assert demo.status_code == 403 and demo.json()["error"]["code"] == "forbidden"
        assert len(c.fake.requests) == base
    else:
        assert demo.status_code not in (401, 403)
    admin = c.client.request(method, path, headers=A)
    assert admin.status_code not in (401, 403)  # the admin token also satisfies demo routes


def test_reads_are_public_and_never_leak_secrets(launch, caplog):
    c = launch()
    c.upload(make_data(1, 40 * KIB))
    paths = ["/api/health", "/api/files", "/api/chunks", "/api/storage/stats", "/api/storage/quota", "/api/inspect/schema",
             "/api/inspect/tables/files/rows", "/api/system/config", "/api/benchmarks", "/api/reliability/scenarios",
             "/api/openapi.json"]
    for p in paths:
        r = c.client.get(p)
        assert r.status_code == 200, p
        for secret in (DEMO, ADMIN, KEY):
            assert secret not in r.text, p
    assert not any(s in caplog.text for s in (DEMO, ADMIN, KEY))


def test_cors_preflight_allows_the_authorization_header_only_for_the_configured_origin(launch):
    c = launch()
    ok = c.client.options("/api/files", headers={"Origin": "https://app.example", "Access-Control-Request-Method": "POST",
                                                 "Access-Control-Request-Headers": "authorization"})
    assert ok.status_code == 200 and "authorization" in ok.headers["access-control-allow-headers"].lower()
    bad = c.client.options("/api/files", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers
    forged = c.client.post("/api/files", files={"file": ("a", b"x")}, headers={**D, "Origin": "https://evil.example"})
    assert forged.status_code == 403  # a valid token does not bypass the origin check


def test_local_backend_is_unchanged_no_auth_and_no_cloud_routes(client):
    assert client.post("/api/files", files={"file": ("a.bin", make_data(2, 20 * KIB))}).status_code == 201
    assert client.post("/api/integrity/audit").status_code == 200
    assert client.post("/api/admin/gc").status_code == 404 and client.get("/api/storage/quota").status_code == 404


# ============================================================================================ upload / restore

def test_upload_and_restore_round_trip_with_the_same_response_shape_as_local(launch, client):
    c = launch()
    data = make_data(3, 300 * KIB)
    r = c.upload(data)
    local = client.post("/api/files", files={"file": ("a.bin", data)}).json()
    assert r.status_code == 201 and keys_of(r.json()) == keys_of(local)
    body = r.json()
    assert body["new_chunks"] == local["new_chunks"] and body["chunk_count"] == local["chunk_count"]  # same chunking either way
    assert body["bytes_written"] == len(data) and c.fake.count("upload") == body["new_chunks"]
    d = c.client.get(f"/api/files/{body['file_id']}/download", headers=D)
    assert d.status_code == 200 and d.content == data and d.headers["x-chunkvault-sha256"] == body["sha256"]
    detail = c.client.get(f"/api/files/{body['file_id']}").json()
    assert detail["status"] == "complete" and detail["chunk_count"] == body["chunk_count"]
    assert c.q("SELECT count(*) FROM v_ref_count_mismatch") == [(0,)]
    assert list(c.svc.settings.staging_dir.iterdir()) == []  # local scratch is cleaned up


def test_second_upload_of_the_same_content_writes_nothing_and_does_not_download(launch):
    c = launch()
    data = make_data(4, 200 * KIB)
    first = c.upload(data).json()
    uploads, downloads = c.fake.count("upload"), c.fake.count("download")
    second = c.upload(data, "copy.bin").json()
    assert second["new_chunks"] == 0 and second["bytes_written"] == 0 and second["duplicate_chunks"] == second["chunk_count"]
    assert c.fake.count("upload") == uploads and c.fake.count("download") == downloads  # presence checks use listings only
    assert {o["decision"] for o in second["chunk_outcomes"]} <= {"reused", "repeated_in_file"}
    assert c.one("SELECT max(ref_count) FROM chunks") >= 2 and c.fake.count("delete") == 0
    assert first["file_id"] != second["file_id"]


def test_edited_file_reuses_unchanged_chunks(launch):
    c = launch()
    base = make_data(5, 400 * KIB)
    c.upload(base)
    edited = base[:150 * KIB] + b"INSERTED BYTES" + base[150 * KIB:]
    r = c.upload(edited, "edited.bin").json()
    assert r["duplicate_chunks"] > 0 and r["new_chunks"] < r["chunk_count"]
    assert c.client.get(f"/api/files/{r['file_id']}/download", headers=D).content == edited


@pytest.mark.parametrize("mode,downloads_expected,lists_expected", [("none", False, False), ("exists", False, True), ("full", True, False)])
def test_verify_on_reuse_modes_control_how_duplicates_are_checked(launch, mode, downloads_expected, lists_expected):
    c = launch({"verify_on_reuse": mode})
    data = make_data(6, 120 * KIB)
    c.upload(data)
    c.fake.requests.clear()
    r = c.upload(data, "again.bin")
    assert r.status_code == 201 and r.json()["new_chunks"] == 0
    assert (c.fake.count("download") > 0) is downloads_expected and (c.fake.count("list") > 0) is lists_expected
    assert c.fake.count("upload") == 0


def test_parallel_storage_io_is_bounded_and_the_result_is_correct(launch):
    c = launch({"io_workers": 3, "batch_chunks": 16})
    c.fake.latency = 0.03
    data = make_data(7, 500 * KIB)
    r = c.upload(data)
    assert r.status_code == 201
    assert 2 <= c.fake.max_inflight["upload"] <= 3  # parallel, but never beyond io_workers
    assert c.client.get(f"/api/files/{r.json()['file_id']}/download", headers=D).content == data
    assert c.fake.max_inflight["download"] <= 3 * 2 + 1


def test_oversized_upload_is_rejected_before_anything_is_stored(launch):
    c = launch(settings_kw={"max_upload_bytes": 100 * KIB})
    r = c.upload(make_data(8, 300 * KIB))
    assert r.status_code == 413 and c.fake.count("upload") == 0 and c.one("SELECT count(*) FROM files") == 0


# ============================================================================================ metadata-last and failures

def test_a_lasting_storage_outage_commits_no_file_metadata_and_gives_back_its_own_reservations(launch):
    c = launch({"storage_max_attempts": 1})
    c.fake.always["upload"] = 503
    r = c.upload(make_data(9, 200 * KIB))
    assert r.status_code == 503 and r.json()["error"]["code"] == "storage_unavailable"
    assert c.q("SELECT status, count(*) FROM files GROUP BY status") == [("failed", 1)]
    assert c.one("SELECT count(*) FROM file_chunks") == 0 and c.q("SELECT count(*) FROM v_ref_count_mismatch") == [(0,)]
    assert c.fake.objects == {} and c.q("SELECT status FROM operations") == [("failed",)]
    # the quota reservations this upload made were returned: nothing is left counted, nothing is left in a half state
    assert c.one("SELECT count(*) FROM chunks") == 0 and c.client.get("/api/storage/quota").json()["used_bytes"] == 0
    assert c.fake.count("delete") > 0  # it deleted by exact key, and only the rows it had created itself


def test_recovery_after_an_outage_starts_clean_and_does_not_double_count(launch):
    c = launch({"storage_max_attempts": 1})
    data = make_data(10, 200 * KIB)
    c.fake.always["upload"] = 500
    assert c.upload(data).status_code == 503
    assert c.one("SELECT count(*) FROM chunks") == 0  # nothing pending is left behind to count against the quota
    c.fake.always.clear()
    r = c.upload(data)
    assert r.status_code == 201
    assert c.one("SELECT sum(size_bytes) FROM chunks") == len(data)  # counted once
    assert c.q("SELECT DISTINCT state FROM chunks") == [("stored",)]
    assert c.client.get(f"/api/files/{r.json()['file_id']}/download", headers=D).content == data


def test_ambiguous_uploads_are_resolved_by_verification(launch):
    c = launch({"storage_max_attempts": 3})
    c.fake.faults += [{"op": "upload", "drop_after_store": True}] * 4  # four chunk uploads are stored but their answers are lost
    data = make_data(11, 300 * KIB)
    r = c.upload(data)
    assert r.status_code == 201
    assert c.client.get(f"/api/files/{r.json()['file_id']}/download", headers=D).content == data
    assert c.q("SELECT count(*) FROM v_ref_count_mismatch") == [(0,)] and c.fake.count("delete") == 0


def test_an_unknown_outcome_fails_the_upload_without_committing_and_later_succeeds(launch):
    c = launch({"storage_max_attempts": 2})
    data = make_data(12, 60 * KIB)
    c.fake.always["upload"] = 0  # placeholder replaced below
    c.fake.always.clear()
    c.fake.faults += [{"op": "upload", "drop": True}] * 40  # every attempt of every chunk is lost
    r = c.upload(data)
    assert r.status_code == 503 and r.json()["error"]["details"]["outcome"] == "unknown"
    assert c.q("SELECT status FROM files") == [("failed",)] and c.one("SELECT count(*) FROM file_chunks") == 0
    c.fake.faults.clear()
    ok = c.upload(data)
    assert ok.status_code == 201 and c.client.get(f"/api/files/{ok.json()['file_id']}/download", headers=D).content == data


def test_failure_in_the_metadata_transaction_rolls_everything_back(launch, monkeypatch):
    c = launch()
    real = c.svc.repo.append_file_chunks

    def boom(*a, **k):
        real(*a, **k)  # the statement ran, so the triggers already counted references: the rollback must undo them
        raise RuntimeError("injected failure inside T2")

    monkeypatch.setattr(c.svc.repo, "append_file_chunks", boom)
    r = c.upload(make_data(13, 150 * KIB))
    assert r.status_code == 500
    assert c.q("SELECT status FROM files") == [("failed",)] and c.one("SELECT count(*) FROM file_chunks") == 0
    assert c.q("SELECT count(*) FROM v_ref_count_mismatch") == [(0,)]
    # the chunks this upload had registered and published are given back again (rows and objects), not left as counted orphans
    assert c.one("SELECT count(*) FROM chunks") == 0 and c.fake.objects == {} and c.client.get("/api/storage/quota").json()["used_bytes"] == 0


def test_losing_the_writer_lock_before_commit_refuses_the_commit(launch):
    c = launch()
    c.svc.uploader.checkpoint = lambda name: c.terminate_lock_session() if name == "in_t2" else None
    r = c.upload(make_data(14, 120 * KIB))
    assert r.status_code == 503 and r.json()["error"]["code"] == "writer_lock_lost"
    assert c.one("SELECT count(*) FROM file_chunks") == 0 and c.q("SELECT count(*) FROM files WHERE status='complete'") == [(0,)]
    assert c.q("SELECT count(*) FROM v_ref_count_mismatch") == [(0,)]
    c.svc.uploader.__dict__.pop("checkpoint")
    # the interrupted upload left a file in 'uploading'. A fresh one may still be live work, so reconcile leaves it alone ...
    assert c.client.post("/api/integrity/reconcile", headers=A).status_code == 200
    assert c.q("SELECT count(*) FROM files WHERE status='uploading'") == [(1,)]
    # ... and repairs it once it is old enough to be abandoned (backdated here instead of waiting 15 minutes)
    c.owner.execute("UPDATE files SET created_at = now() - interval '2 hours'")
    c.owner.execute("UPDATE operations SET started_at = now() - interval '2 hours'")
    assert c.client.post("/api/integrity/reconcile", headers=A).status_code == 200
    assert c.q("SELECT count(*) FROM files WHERE status='uploading'") == [(0,)]
    ok = c.upload(make_data(14, 120 * KIB))  # and the system simply works again
    assert ok.status_code == 201


def test_a_second_writer_is_turned_away_with_a_clear_error(launch):
    c = launch()
    with c.svc.lock.acquire():
        r = c.upload(make_data(15, 30 * KIB))
    assert r.status_code == 409 and r.json()["error"]["code"] == "writer_busy"
    assert c.one("SELECT count(*) FROM files") == 0


# ============================================================================================ restore robustness

def test_restore_retries_transient_errors_and_reports_missing_or_corrupt_chunks(launch):
    c = launch({"storage_max_attempts": 3})
    data = make_data(16, 150 * KIB)
    fid = c.upload(data).json()["file_id"]
    c.fake.faults += [{"op": "download", "status": 503}, {"op": "download", "status": 429}]
    assert c.client.get(f"/api/files/{fid}/download", headers=D).content == data

    victim = c.q("SELECT chunk_hash FROM file_chunks WHERE file_id=%s ORDER BY chunk_index LIMIT 1", fid)[0][0]
    good = c.fake.get(victim)
    c.fake.put(victim, bytes([good[0] ^ 1]) + good[1:])  # corrupted at rest
    r = c.client.get(f"/api/files/{fid}/download", headers=D)
    assert r.status_code >= 400 and r.json()["error"]["code"] in ("chunk_corrupt", "chunk_corruption", "chunk_failed_verification") or r.status_code == 500
    c.fake.objects.pop(f"{c.fake.bucket}/{victim[:2]}/{victim}")  # now missing
    r = c.client.get(f"/api/files/{fid}/download", headers=D)
    assert r.status_code >= 400 and data not in r.content
    assert list(c.svc.settings.staging_dir.iterdir()) == []  # no partial restore is left behind
    assert "failed" in {s for (s,) in c.q("SELECT status FROM operations WHERE operation_type='restore'")}


# ============================================================================================ deletion safety and GC

def share_two_files(c):
    base = make_data(20, 300 * KIB)
    a = c.upload(base, "a.bin").json()
    b = c.upload(base[:200 * KIB] + make_data(21, 100 * KIB), "b.bin").json()  # shares its first part with a
    return base, a, b


def test_deleting_a_file_never_deletes_objects_and_never_touches_shared_chunks(launch):
    c = launch()
    base, a, b = share_two_files(c)
    objects_before = dict(c.fake.objects)
    quota_before = c.client.get("/api/storage/quota").json()
    r = c.client.delete(f"/api/files/{a['file_id']}", headers=D)
    assert r.status_code == 200
    body = r.json()
    assert body["chunks_reclaimed"] == 0 and body["bytes_reclaimed"] == 0 and body["cleanup_failed"] == 0
    assert body["chunks_released"] == a["chunk_count"] and body["chunks_pending_gc"] > 0
    assert c.fake.count("delete") == 0 and c.fake.objects == objects_before  # every object is still there
    b_data = c.client.get(f"/api/files/{b['file_id']}/download", headers=D).content
    assert b_data[:200 * KIB] == base[:200 * KIB]  # the shared chunks still restore file b
    assert c.client.get("/api/storage/quota").json()["used_bytes"] == quota_before["used_bytes"]  # nothing freed yet
    assert c.client.get(f"/api/files/{a['file_id']}").status_code == 404
    assert c.q("SELECT count(*) FROM v_ref_count_mismatch") == [(0,)]
    again = c.client.delete(f"/api/files/{a['file_id']}", headers=D)
    assert again.status_code == 404  # repeated delete changes nothing


def test_gc_is_dry_run_by_default_admin_only_and_respects_the_grace_window(launch):
    c = launch()  # default grace window: 900 s
    _, a, b = share_two_files(c)
    c.client.delete(f"/api/files/{a['file_id']}", headers=D)
    c.client.delete(f"/api/files/{b['file_id']}", headers=D)
    assert c.client.post("/api/admin/gc").status_code == 401 and c.client.post("/api/admin/gc", headers=D).status_code == 403
    dry = c.client.post("/api/admin/gc", headers=A).json()
    assert dry["dry_run"] is True and dry["candidates"] == 0 and dry["rows_removed"] == 0  # everything is too fresh
    forced = c.client.post("/api/admin/gc", headers=A, json={"dry_run": False}).json()
    assert forced["rows_removed"] == 0 and c.fake.count("delete") == 0  # grace protects a fresh chunk even when not a dry run


def test_gc_reclaims_only_unreferenced_chunks_and_is_idempotent(launch):
    c = launch({"orphan_min_age_seconds": 0.0})
    base, a, b = share_two_files(c)
    c.client.delete(f"/api/files/{a['file_id']}", headers=D)
    unref = c.one("SELECT count(*) FROM chunks WHERE ref_count = 0")
    unref_bytes = c.one("SELECT coalesce(sum(size_bytes),0) FROM chunks WHERE ref_count = 0")
    assert unref > 0
    objects_before = len(c.fake.objects)

    dry = c.client.post("/api/admin/gc", headers=A).json()
    assert dry["dry_run"] and dry["candidates"] == unref and dry["candidate_bytes"] == unref_bytes
    assert len(c.fake.objects) == objects_before and c.fake.count("delete") == 0  # a dry run changes nothing

    real = c.client.post("/api/admin/gc", headers=A, json={"dry_run": False, "limit": 1000}).json()
    assert real["rows_removed"] == unref == real["objects_removed"] and real["bytes_reclaimed"] == unref_bytes and real["failed"] == []
    assert len(c.fake.objects) == objects_before - unref
    assert c.one("SELECT count(*) FROM chunks WHERE ref_count = 0") == 0
    b_data = c.client.get(f"/api/files/{b['file_id']}/download", headers=D).content  # file b is untouched
    assert b_data[:200 * KIB] == base[:200 * KIB]
    again = c.client.post("/api/admin/gc", headers=A, json={"dry_run": False}).json()
    assert again["candidates"] == 0 and again["rows_removed"] == 0
    assert c.q("SELECT status FROM operations WHERE operation_type='reconciliation' ORDER BY operation_id") == [("succeeded",)] * 2  # each real run is recorded


def test_gc_never_removes_a_referenced_chunk_even_if_asked_to(launch):
    c = launch({"orphan_min_age_seconds": 0.0})
    base, a, _ = share_two_files(c)
    victim = c.q("SELECT chunk_hash FROM file_chunks WHERE file_id=%s LIMIT 1", a["file_id"])[0][0]
    # a (buggy) caller that names a referenced chunk directly: the guard inside the DELETE refuses it
    with c.svc.db.connection() as conn, c.svc.db.transaction(conn):
        assert c.svc.repo.delete_unreferenced_chunk_rows(conn, [victim]) == []
    c.client.post("/api/admin/gc", headers=A, json={"dry_run": False, "limit": 1000})
    assert c.fake.get(victim) is not None and c.one("SELECT count(*) FROM chunks WHERE hash=%s", victim) == 1
    assert c.client.get(f"/api/files/{a['file_id']}/download", headers=D).content == base


def test_gc_is_bounded_per_call(launch):
    c = launch({"orphan_min_age_seconds": 0.0})
    a = c.upload(make_data(22, 300 * KIB)).json()
    c.client.delete(f"/api/files/{a['file_id']}", headers=D)
    total = c.one("SELECT count(*) FROM chunks")
    first = c.client.post("/api/admin/gc", headers=A, json={"dry_run": False, "limit": 3}).json()
    assert first["rows_removed"] == 3 and first["more_available"] is True and c.one("SELECT count(*) FROM chunks") == total - 3
    assert c.client.post("/api/admin/gc", headers=A, json={"limit": 0}).status_code == 422
    assert c.client.post("/api/admin/gc", headers=A, json={"limit": 100000}).status_code == 422


def test_gc_object_delete_failure_keeps_the_chunk_counted_and_retries_next_time(launch):
    c = launch({"orphan_min_age_seconds": 0.0, "storage_max_attempts": 1})
    a = c.upload(make_data(23, 80 * KIB)).json()
    c.client.delete(f"/api/files/{a['file_id']}", headers=D)
    used = c.client.get("/api/storage/quota").json()["used_bytes"]
    c.fake.always["delete"] = 500
    r = c.client.post("/api/admin/gc", headers=A, json={"dry_run": False}).json()
    assert r["failed"] and r["objects_removed"] == 0
    assert c.client.get("/api/storage/quota").json()["used_bytes"] == used  # re-registered: still counted, still discoverable
    c.fake.always.clear()
    ok = c.client.post("/api/admin/gc", headers=A, json={"dry_run": False}).json()
    assert ok["failed"] == [] and ok["rows_removed"] > 0 and c.fake.objects == {}


def test_gc_stops_when_the_writer_lock_is_lost(launch, monkeypatch):
    import app.maintenance.gc as gcmod

    c = launch({"orphan_min_age_seconds": 0.0})
    a = c.upload(make_data(24, 300 * KIB)).json()
    c.client.delete(f"/api/files/{a['file_id']}", headers=D)
    total = c.one("SELECT count(*) FROM chunks")
    monkeypatch.setattr(gcmod, "BATCH", 2)
    real_delete = c.svc.store.delete
    calls = []

    def delete_then_lose_lock(h):
        out = real_delete(h)
        if not calls:
            calls.append(h)
            c.terminate_lock_session()  # the lock session dies in the middle of the run
        return out

    monkeypatch.setattr(c.svc.store, "delete", delete_then_lose_lock)
    r = c.client.post("/api/admin/gc", headers=A, json={"dry_run": False, "limit": 1000})
    assert r.status_code == 503 and r.json()["error"]["code"] == "writer_lock_lost"
    # the lock died after the first object delete: the release step refused to run, so NO row was removed. Every claimed row is
    # still there in the durable 'deleting' state (counted by the quota, refused by uploads), waiting for the next run.
    assert c.one("SELECT count(*) FROM chunks") == total and c.one("SELECT count(*) FROM chunks WHERE state='deleting'") == total
    # the run could not even record its own failure (that bookkeeping also needs the lock): it stays 'running' until reconciled
    assert c.q("SELECT status FROM operations WHERE operation_type='reconciliation'") == [("running",)]
    monkeypatch.undo()
    assert c.client.post("/api/admin/gc", headers=A, json={"dry_run": False, "limit": 1000}).json()["failed"] == []  # restart-safe
    assert c.one("SELECT count(*) FROM chunks") == 0
    assert c.client.post("/api/integrity/reconcile", headers=A).status_code == 200  # reconcile aborts the stuck bookkeeping row
    assert c.q("SELECT status FROM operations WHERE operation_type='reconciliation' ORDER BY operation_id") == [("aborted",), ("succeeded",)]


# ============================================================================================ quota

def used(c):
    return c.client.get("/api/storage/quota").json()


def test_quota_boundaries_are_exact_and_duplicates_never_count_twice(launch):
    size = 120 * KIB
    data = make_data(30, size)
    c = launch({"stored_bytes_cap": size + 1}, {"max_upload_bytes": size + 1})
    assert c.upload(data).status_code == 201 and used(c)["used_bytes"] == size
    assert c.upload(data, "dup.bin").status_code == 201 and used(c)["used_bytes"] == size  # a duplicate adds nothing, even near the cap
    assert c.upload(b"x").status_code == 201 and used(c)["used_bytes"] == size + 1 == used(c)["quota_bytes"]  # exactly at the cap
    over = c.upload(b"y")  # one more unique byte
    assert over.status_code == 507 and over.json()["error"]["code"] == "storage_quota_exceeded"
    d = over.json()["error"]["details"]
    assert (d["quota_bytes"], d["used_bytes"], d["required_new_bytes"], d["available_bytes"]) == (size + 1, size + 1, 1, 0)
    assert used(c)["used_bytes"] == size + 1 and c.one("SELECT count(*) FROM chunks") == c.one("SELECT count(*) FROM chunks WHERE state='stored'")
    assert c.q("SELECT status, count(*) FROM files GROUP BY status") == [("complete", 3)]  # refused up front: no file row at all


def test_a_rejected_upload_publishes_nothing_and_registers_nothing(launch):
    c = launch({"stored_bytes_cap": 100 * KIB}, {"max_upload_bytes": 100 * KIB})
    assert c.upload(make_data(31, 60 * KIB)).status_code == 201
    uploads, rows = c.fake.count("upload"), c.one("SELECT count(*) FROM chunks")
    r = c.upload(make_data(32, 60 * KIB))  # 120 KiB of unique bytes cannot fit: refused before any chunk moves
    assert r.status_code == 507 and r.json()["error"]["code"] == "storage_quota_exceeded"
    assert c.fake.count("upload") == uploads and c.one("SELECT count(*) FROM chunks") == rows


def test_quota_frees_up_after_delete_and_explicit_gc(launch):
    size = 100 * KIB
    c = launch({"stored_bytes_cap": size, "orphan_min_age_seconds": 0.0}, {"max_upload_bytes": size})
    first = c.upload(make_data(32, size)).json()
    assert c.upload(make_data(33, size)).status_code == 507
    c.client.delete(f"/api/files/{first['file_id']}", headers=D)
    assert c.upload(make_data(33, size)).status_code == 507  # deleting alone frees nothing: objects wait for GC
    assert used(c)["reclaimable_bytes"] == size
    c.client.post("/api/admin/gc", headers=A, json={"dry_run": False, "limit": 1000})
    assert used(c)["used_bytes"] == 0 and c.upload(make_data(33, size)).status_code == 201


def test_concurrent_uploads_cannot_exceed_the_quota(launch):
    size = 100 * KIB
    c = launch({"stored_bytes_cap": int(size * 1.5)}, {"max_upload_bytes": size, "writer_lock_timeout_seconds": 30.0})
    results = []

    def go(seed):
        import io
        try:
            c.svc.uploader.upload(f"f{seed}.bin", io.BytesIO(make_data(seed, size)))
            results.append("ok")
        except QuotaExceededError:
            results.append("quota")

    threads = [threading.Thread(target=go, args=(s,)) for s in (40, 41, 42)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert sorted(results) == ["ok", "quota", "quota"]  # serialised by the writer lock: exactly one fits
    assert c.one("SELECT coalesce(sum(size_bytes),0) FROM chunks") <= int(size * 1.5)
    assert c.one("SELECT count(*) FROM files WHERE status='complete'") == 1


def test_failed_uploads_leave_no_counted_reservations(launch):
    c = launch({"storage_max_attempts": 1})
    c.fake.always["upload"] = 500
    c.upload(make_data(34, 100 * KIB))
    q = used(c)
    assert q["used_bytes"] == 0 and q["reclaimable_bytes"] == 0  # failures cannot hide or pin quota bytes


def test_if_storage_cannot_delete_during_cleanup_the_rows_stay_durable_counted_and_collectable(launch):
    c = launch({"storage_max_attempts": 1, "orphan_min_age_seconds": 0.0})
    c.fake.always["upload"] = 500
    c.fake.always["delete"] = 500  # Storage is down for BOTH writing and deleting
    c.upload(make_data(35, 100 * KIB))
    left = c.one("SELECT count(*) FROM chunks")
    assert left > 0 and c.q("SELECT DISTINCT state FROM chunks") == [("deleting",)]  # never silently forgotten
    assert used(c)["used_bytes"] > 0  # still counted until reclaimed
    c.fake.always.clear()
    r = c.client.post("/api/admin/gc", headers=A, json={"dry_run": False, "limit": 1000}).json()
    assert r["rows_removed"] == left and used(c)["used_bytes"] == 0  # the explicit GC finishes the job


# ============================================================================================ stats, audit, inspect

def test_stats_inspect_and_listings_never_scan_the_object_store(launch):
    c = launch()
    c.upload(make_data(50, 200 * KIB))
    c.upload(make_data(51, 100 * KIB), "b.bin")
    c.fake.requests.clear()
    stats = c.client.get("/api/storage/stats").json()
    for p in ("/api/files", "/api/chunks", "/api/inspect/schema", "/api/inspect/tables/chunks/rows", "/api/storage/quota", "/api/health"):
        assert c.client.get(p).status_code == 200
    assert c.fake.requests == []  # none of these touches storage at all
    assert stats["total_files"] == 2 and stats["chunk_files"] == c.one("SELECT count(*) FROM chunks WHERE state='stored'")
    assert stats["chunk_store_bytes"] == c.one("SELECT sum(size_bytes) FROM chunks")
    schema = c.client.get("/api/inspect/schema").json()
    assert [t["name"] for t in schema["tables"]] == ["files", "chunks", "file_chunks", "operations"]
    assert schema["ref_count_invariant"]["mismatches"] == 0


def test_whole_store_audit_is_bounded_never_holds_a_transaction_open_and_says_what_it_verified(launch):
    c = launch({"audit_deep_chunks": 5})
    c.upload(make_data(52, 300 * KIB))
    chunks = c.one("SELECT count(*) FROM chunks")
    open_tx = []
    c.fake.on_request = lambda op: open_tx.append(c.one(
        "SELECT count(*) FROM pg_stat_activity WHERE application_name='chunkvault' AND state LIKE 'idle in transaction%%'"))
    c.fake.requests.clear()
    r = c.client.post("/api/integrity/audit", headers=A, json={})
    assert r.status_code == 200 and r.json()["healthy"] is True
    assert c.fake.count("list") == 256 and c.fake.count("download") == 5 and c.fake.count("upload") == 0  # one inventory + the sample
    assert open_tx and max(open_tx) == 0  # no PostgreSQL transaction was open during any storage request
    notes = " ".join(r.json()["notes"])
    assert f"{chunks} chunk row(s)" in notes and "5 of" in notes and "random sample" in notes
    light = c.client.post("/api/integrity/audit", headers=A, json={"verify_chunk_bytes": False}).json()
    assert light["healthy"] and "metadata-only" in " ".join(light["notes"])


def test_audit_finds_missing_corrupt_and_orphan_objects(launch):
    c = launch({"audit_deep_chunks": 500})
    fid = c.upload(make_data(53, 200 * KIB)).json()["file_id"]
    hashes = [h for (h,) in c.q("SELECT hash FROM chunks ORDER BY hash")]
    missing, flipped, resized = hashes[0], hashes[1], hashes[2]
    c.fake.objects.pop(f"{c.fake.bucket}/{missing[:2]}/{missing}")
    good = c.fake.get(flipped)
    c.fake.put(flipped, bytes([good[0] ^ 1]) + good[1:])  # same size, wrong content: only a hash check can see it
    c.fake.put(resized, c.fake.get(resized)[:-1])  # wrong size: the inventory sees it
    stray = "ab" + "c" * 62
    c.fake.put(stray, b"unreferenced object")
    r = c.client.post("/api/integrity/audit", headers=A, json={}).json()
    by_kind = {}
    for f in r["findings"]:
        by_kind.setdefault(f["kind"], set()).add(f["subject"])
    assert missing in by_kind["missing_chunk_file"] and {flipped, resized} <= by_kind["corrupt_chunk"]
    assert by_kind["orphan_chunk_file"] == {stray} and r["healthy"] is False
    assert fid in " ".join(f["detail"] for f in r["findings"] if f["subject"] == missing)  # says which file is affected
    per_file = c.client.post("/api/integrity/audit", headers=A, json={"file_id": fid}).json()
    assert {"missing_chunk_file", "corrupt_chunk"} <= {f["kind"] for f in per_file["findings"]}
    assert c.fake.count("delete") == 0  # the audit changes nothing


def test_clean_file_audit_downloads_and_verifies_every_chunk(launch):
    c = launch()
    fid = c.upload(make_data(54, 150 * KIB)).json()["file_id"]
    c.fake.requests.clear()
    r = c.client.post("/api/integrity/audit", headers=A, json={"file_id": fid}).json()
    assert r["healthy"] and "SHA-256 verified" in " ".join(r["notes"]) and c.fake.count("download") >= 1 and c.fake.count("list") == 0


def test_cloud_reconcile_repairs_metadata_but_never_deletes_objects(launch):
    c = launch({"orphan_min_age_seconds": 0.0})
    a = c.upload(make_data(55, 100 * KIB)).json()
    c.client.delete(f"/api/files/{a['file_id']}", headers=D)
    c.owner.execute("INSERT INTO files(file_id, filename, original_size) VALUES ('stuck', 's', 1)")
    c.fake.put("cd" + "e" * 62, b"stray")
    r = c.client.post("/api/integrity/reconcile", headers=A).json()
    actions = {x["action"] for x in r["actions"]}
    assert "fail_incomplete_file" in actions and not any("chunk" in x for x in actions)
    assert c.fake.count("delete") == 0 and c.one("SELECT count(*) FROM chunks WHERE ref_count=0") > 0  # reclamation is GC's job only


# ============================================================================================ local-only features

def test_benchmarks_and_fault_lab_stay_local_and_never_touch_cloud_state(launch):
    c = launch({}, {"fault_lab_enabled": True})
    c.upload(make_data(60, 60 * KIB))
    before = (c.q("SELECT count(*) FROM files"), c.q("SELECT count(*) FROM chunks"), c.q("SELECT count(*) FROM operations"))
    objects_before, requests_before = dict(c.fake.objects), len(c.fake.requests)

    r = c.client.post("/api/benchmarks", headers=A, json={"kind": "pipeline", "workload": "small", "size_bytes": 16 * KIB, "iterations": 1})
    assert r.status_code == 202
    job = r.json()["job_id"]
    for _ in range(300):
        st = c.client.get(f"/api/benchmarks/{job}").json()
        if st["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.2)
    assert st["status"] == "done"

    scenario = c.client.get("/api/reliability/scenarios").json()["scenarios"][0]["id"]
    e = c.client.post("/api/reliability/experiments", headers=A, json={"scenario_id": scenario})
    assert e.status_code == 200

    assert (c.q("SELECT count(*) FROM files"), c.q("SELECT count(*) FROM chunks"), c.q("SELECT count(*) FROM operations")) == before
    assert c.fake.objects == objects_before and len(c.fake.requests) == requests_before  # no storage request at all
    assert c.svc.settings.benchmarks_dir.resolve() not in [p.resolve() for p in c.svc.settings.staging_dir.iterdir()]


def test_the_fault_lab_always_builds_a_local_store(launch):
    from app.reliability.faultlab import Lab
    from app.core.config import CDCConfig

    c = launch()
    lab = Lab(CDCConfig())
    try:
        assert lab.settings.backend == "local" and lab.settings.cloud is None
        from app.database.sqlite import SqliteDatabase
        from app.storage.filesystem import FilesystemChunkStore
        assert isinstance(lab.svc.db, SqliteDatabase) and isinstance(lab.svc.store, FilesystemChunkStore)
        assert lab.root not in c.svc.settings.data_dir.parents and lab.settings.db_path != c.svc.settings.db_path
    finally:
        lab.cleanup()
