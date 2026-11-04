"""Phase 2.5 hardening: bounded request bodies and recovery/deletion safety."""

import asyncio
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.limits import DEFAULT_BODY_LIMIT, MULTIPART_OVERHEAD, BodyLimitMiddleware
from app.core.config import KIB, Settings
from app.database.connection import connect
from app.main import create_app
from tests.conftest import make_data
from tests.test_storage_engine import SimulatedCrash, chunk_files, rows, up

LIMIT = 5000


@pytest.fixture
def small_client(tmp_path):
    s = Settings(data_dir=tmp_path / "d", db_path=tmp_path / "d" / "x.db",
                 cors_origins=("http://localhost:5173",), max_upload_bytes=LIMIT, staging_stale_seconds=0)
    with TestClient(create_app(s), raise_server_exceptions=False, base_url="http://localhost") as c:
        calls = {"n": 0}
        orig = c.app.state.services.uploader.upload

        def counted(*a, **k):
            calls["n"] += 1
            return orig(*a, **k)

        c.app.state.services.uploader.upload = counted
        c.calls, c.settings = calls, s
        yield c


def code(r):
    return r.json()["error"]["code"]


# ---- configured limit -------------------------------------------------------------

def test_default_limit_is_64_mib():
    assert Settings.from_env({}).max_upload_bytes == 64 * 1024 * 1024


# ---- declared / undeclared sizes ------------------------------------------------------

def test_declared_oversize_is_rejected_before_the_endpoint_runs(small_client):
    body = b"x" * (LIMIT + MULTIPART_OVERHEAD + 10)
    r = small_client.post("/api/files", content=body,
                          headers={"content-type": "multipart/form-data; boundary=zz"})
    assert r.status_code == 413 and code(r) == "upload_too_large"
    assert small_client.calls["n"] == 0
    assert not list(small_client.settings.staging_dir.iterdir())


def test_oversize_without_content_length_is_cut_off(small_client):
    def body():  # generator => chunked transfer encoding, no Content-Length
        for _ in range(64):
            yield b"x" * (64 * KIB)

    r = small_client.post("/api/files", content=body(),
                          headers={"content-type": "multipart/form-data; boundary=zz"})
    assert r.status_code == 413 and code(r) == "upload_too_large"
    assert small_client.calls["n"] == 0


def test_lying_client_is_cut_off(tmp_path):
    """Declared small, actually sends more: the received-byte count still stops it."""
    async def run():
        reached = []

        async def app(scope, receive, send):
            while True:
                m = await receive()
                reached.append(len(m["body"]))
                if not m.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        sent, produced = [], {"n": 0}

        async def receive():
            produced["n"] += 1
            return {"type": "http.request", "body": b"x" * 1000, "more_body": produced["n"] < 10_000}

        async def send(m):
            sent.append(m)

        scope = {"type": "http", "method": "POST", "path": "/api/files",
                 "headers": [(b"content-length", b"10")]}
        await BodyLimitMiddleware(app, max_upload_bytes=LIMIT)(scope, receive, send)
        return sent, produced["n"]

    sent, pulled = asyncio.run(run())
    assert sent[0]["status"] == 413
    assert pulled * 1000 <= LIMIT + MULTIPART_OVERHEAD + 1000  # stopped reading right after the limit
    assert pulled < 10_000


def test_malformed_content_length_rejected():
    async def run(value):
        sent = []

        async def app(scope, receive, send):  # pragma: no cover - must not be reached
            raise AssertionError("endpoint reached")

        async def send(m):
            sent.append(m)

        scope = {"type": "http", "method": "POST", "path": "/api/files", "headers": [(b"content-length", value)]}
        await BodyLimitMiddleware(app, max_upload_bytes=LIMIT)(scope, None, send)
        return sent

    for bad in (b"abc", b"-5", b"1e3", b""):
        sent = asyncio.run(run(bad))
        assert sent[0]["status"] == 400
        assert json.loads(sent[1]["body"])["error"]["code"] == "invalid_content_length"


def test_other_endpoints_have_a_small_body_limit(small_client):
    big = {"file_id": None, "pad": "x" * (DEFAULT_BODY_LIMIT + 10)}
    r = small_client.post("/api/integrity/audit", json=big)
    assert r.status_code == 413


def test_upload_at_exactly_the_limit_still_works_and_one_more_byte_does_not(small_client):
    ok = small_client.post("/api/files", files={"file": ("a", b"x" * LIMIT)})
    assert ok.status_code == 201
    over = small_client.post("/api/files", files={"file": ("b", b"x" * (LIMIT + 1))})
    assert over.status_code == 413 and code(over) == "upload_too_large"
    assert small_client.get("/api/files").json()["total"] == 1
    assert not list(small_client.settings.staging_dir.iterdir())


def test_cors_headers_present_on_rejections(small_client):
    r = small_client.post("/api/files", content=b"x" * (LIMIT + MULTIPART_OVERHEAD + 1),
                          headers={"origin": "http://localhost:5173",
                                   "content-type": "multipart/form-data; boundary=zz"})
    assert r.status_code == 413
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


# ---- malformed requests ------------------------------------------------------------------

@pytest.mark.parametrize("ctype,body", [
    ("multipart/form-data", b"whatever"),  # no boundary
    ("multipart/form-data; boundary=zz", b"--zz\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a\"\r\n\r\npartial"),  # truncated
    ("multipart/form-data; boundary=zz", b""),
    ("application/json", b"{}"),
    ("text/plain", b"hello"),
])
def test_malformed_uploads_are_client_errors_not_500(small_client, ctype, body):
    r = small_client.post("/api/files", content=body, headers={"content-type": ctype})
    assert 400 <= r.status_code < 500, r.text
    assert "error" in r.json()
    assert small_client.get("/api/files").json()["total"] == 0
    assert not list(small_client.settings.staging_dir.iterdir())


# ---- recovery and deletion safety ---------------------------------------------------------

def test_failed_duplicate_upload_leaves_existing_ref_counts_unchanged(svc, monkeypatch):
    data = make_data(40, 200 * KIB)
    up(svc, "a", data)
    before = rows(svc, "SELECT hash, ref_count FROM chunks ORDER BY hash")
    calls = {"n": 0}
    orig = svc.repo.append_file_chunk

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return orig(*a, **k)

    monkeypatch.setattr(svc.repo, "append_file_chunk", flaky)
    with pytest.raises(Exception):
        up(svc, "b", data)
    monkeypatch.undo()
    assert rows(svc, "SELECT hash, ref_count FROM chunks ORDER BY hash") == before
    assert rows(svc, "SELECT * FROM v_ref_count_mismatch") == []


def test_reconcile_never_deletes_referenced_chunks_even_with_tampered_counters(svc, tmp_path):
    """Regression: ref_count tampered to 0 while the manifest still references the chunk
    used to make reconcile abort on the FK constraint; it must now simply leave the chunk."""
    data = make_data(41, 150 * KIB)
    r = up(svc, "a", data)
    conn = connect(svc.settings.db_path)
    conn.execute("UPDATE chunks SET ref_count = 0")
    conn.close()
    files_before = chunk_files(svc)
    out = svc.reconciler.reconcile()  # must not raise
    assert chunk_files(svc) == files_before
    assert not any(a.action.startswith("delete_unreferenced") or a.action == "delete_orphan_chunk" for a in out.actions)
    assert any(f.kind == "ref_count_mismatch" for f in out.remaining.findings)
    dest = tmp_path / "o"
    svc.restorer.restore_to_file(r.file_id, dest)
    assert dest.read_bytes() == data


def test_delete_with_tampered_counter_does_not_remove_a_chunk_another_file_uses(svc, tmp_path):
    data = make_data(42, 120 * KIB)
    a, b = up(svc, "a", data), up(svc, "b", data)
    conn = connect(svc.settings.db_path)
    conn.execute("UPDATE chunks SET ref_count = 1")  # drifted: really 2
    conn.close()
    svc.deleter.delete(a.file_id)  # counters hit 0, but b's manifest still uses every chunk
    dest = tmp_path / "o"
    svc.restorer.restore_to_file(b.file_id, dest)
    assert dest.read_bytes() == data
    assert len(chunk_files(svc)) == b.chunk_count


def test_reconcile_with_mixed_state_removes_only_unreferenced_data(svc, tmp_path):
    keep = make_data(43, 150 * KIB)
    r = up(svc, "keep", keep)
    gone = up(svc, "gone", make_data(44, 100 * KIB))
    svc.deleter.delete(gone.file_id)
    orphan = svc.hasher.digest(b"orphan")
    (svc.store.chunks_dir / orphan[:2]).mkdir(exist_ok=True)
    (svc.store.chunks_dir / orphan[:2] / orphan).write_bytes(b"orphan")
    (svc.store.staging_dir / "junk.tmp").write_bytes(b"x")
    out = svc.reconciler.reconcile()
    assert {a.action for a in out.actions} == {"delete_orphan_chunk", "delete_staging_file"}
    assert out.remaining.healthy
    dest = tmp_path / "o"
    svc.restorer.restore_to_file(r.file_id, dest)
    assert dest.read_bytes() == keep


def test_interrupted_deletion_cleanup_can_be_retried_repeatedly(svc, monkeypatch):
    r = up(svc, "a", make_data(45, 100 * KIB))
    monkeypatch.setattr(svc.store, "delete", lambda h: (_ for _ in ()).throw(SimulatedCrash()))
    with pytest.raises(SimulatedCrash):
        svc.deleter.delete(r.file_id)
    monkeypatch.undo()
    first = svc.reconciler.reconcile()
    assert first.actions and first.remaining.healthy
    second = svc.reconciler.reconcile()
    assert second.actions == [] and second.remaining.healthy


def test_audit_does_not_change_database_file_contents(svc):
    up(svc, "a", make_data(46, 100 * KIB))
    svc.store.path_for(sorted(svc.store.iter_hashes())[0]).write_bytes(b"corrupt")
    conn = connect(svc.settings.db_path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    db_bytes = svc.settings.db_path.read_bytes()
    chunk_state = {p.name: p.read_bytes() for p in svc.store.chunks_dir.rglob("*") if p.is_file()}
    svc.auditor.verify_store()
    svc.auditor.verify_store(verify_chunk_bytes=False)
    assert svc.settings.db_path.read_bytes() == db_bytes
    assert {p.name: p.read_bytes() for p in svc.store.chunks_dir.rglob("*") if p.is_file()} == chunk_state


def test_concurrent_server_processes_cannot_bypass_the_writer_lock(svc, settings):
    """Two real OS processes upload concurrently into the same store."""
    backend = Path(__file__).resolve().parents[1]
    code_ = (
        "import io,sys,random\n"
        "from app.core.config import Settings\nfrom app.core.services import build_services\n"
        "s=Settings.from_env({'CHUNKVAULT_DATA_DIR':sys.argv[1],'CHUNKVAULT_DB_PATH':sys.argv[2],"
        "'CHUNKVAULT_WRITER_LOCK_TIMEOUT_SECONDS':'60'})\n"
        "svc=build_services(s)\n"
        "data=random.Random(7).randbytes(200*1024)\n"
        "for i in range(4):\n svc.uploader.upload(f'{sys.argv[3]}-{i}', io.BytesIO(data))\nprint('done')\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", code_, str(settings.data_dir), str(settings.db_path), tag],
                              cwd=backend, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for tag in ("p1", "p2")]
    outs = [p.communicate(timeout=120) for p in procs]
    assert [p.returncode for p in procs] == [0, 0], outs
    assert rows(svc, "SELECT COUNT(*) FROM files WHERE status='complete'") == [(8,)]
    assert rows(svc, "SELECT * FROM v_ref_count_mismatch") == []
    assert all(rc == 8 for (rc,) in rows(svc, "SELECT ref_count FROM chunks"))  # 8 identical files
    assert len(chunk_files(svc)) == rows(svc, "SELECT COUNT(*) FROM chunks")[0][0]
    assert svc.auditor.verify_store().healthy
