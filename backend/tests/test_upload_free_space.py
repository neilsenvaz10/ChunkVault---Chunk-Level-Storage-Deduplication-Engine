"""Upload free-space preflight and disk-full handling (final acceptance correction).

The preflight is advisory: it refuses an upload that clearly cannot fit, never one that needs little or no new space.
Disk-full errors that still occur (another process fills the disk) are reported as ``insufficient_storage`` after the
normal cleanup, and the metadata stays consistent.
"""

import errno
import io
import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.core.config import KIB, Settings
from app.core.diskfull import is_disk_full
from app.core.errors import ConfigurationError
from app.database.connection import connect
from app.deduplication.interfaces import InsufficientStorageError, UploadError
from app.main import create_app
from app.storage.interfaces import ChunkWriteError
from tests.conftest import make_data


def _up(svc, name, data):
    return svc.uploader.upload(name, io.BytesIO(data))


def _db_state(svc):
    conn = connect(svc.settings.db_path)
    try:
        return {
            "files": [tuple(r) for r in conn.execute("SELECT status, sha256 FROM files ORDER BY file_id")],
            "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "manifest": conn.execute("SELECT COUNT(*) FROM file_chunks").fetchone()[0],
            "ops": [tuple(r) for r in conn.execute("SELECT operation_type, status FROM operations ORDER BY operation_id")],
            "mismatch": conn.execute("SELECT COUNT(*) FROM v_ref_count_mismatch").fetchone()[0],
        }
    finally:
        conn.close()


def _nothing_left_behind(svc):
    assert list(svc.store.iter_hashes()) == []
    assert list(svc.store.iter_staged()) == []


def _assert_consistent(svc):
    report = svc.auditor.verify_store()
    assert report.healthy, report.findings
    assert _db_state(svc)["mismatch"] == 0


# ---------------------------------------------------------------- preflight

def test_insufficient_space_is_refused_before_anything_is_published(svc, monkeypatch):
    monkeypatch.setattr(svc.store, "free_bytes", lambda: 1000)
    with pytest.raises(InsufficientStorageError) as err:
        _up(svc, "big.bin", make_data(1, 200 * KIB))
    assert err.value.code == "insufficient_storage" and err.value.status_code == 507
    assert err.value.details["advisory"] is True and err.value.details["free_bytes"] == 1000
    assert err.value.details["required_bytes"] > 200 * KIB
    _nothing_left_behind(svc)  # staging removed, no chunk published
    state = _db_state(svc)
    assert state["files"] == [] and state["chunks"] == 0 and state["manifest"] == 0 and state["ops"] == []
    _assert_consistent(svc)


def test_a_duplicate_upload_is_not_refused_when_it_needs_no_new_space(svc, monkeypatch):
    data = make_data(2, 200 * KIB)
    first = _up(svc, "orig.bin", data)
    # free space covers the safety margin but not size + margin, so only the "really new bytes" count can save it
    margin = svc.uploader._margin(len(data))
    monkeypatch.setattr(svc.store, "free_bytes", lambda: margin + 1000)
    second = _up(svc, "copy.bin", data)
    assert second.new_chunks == 0 and second.bytes_written == 0 and second.chunk_count == first.chunk_count
    # ...while genuinely new content of the same size is refused under the same conditions
    with pytest.raises(InsufficientStorageError):
        _up(svc, "other.bin", make_data(3, 200 * KIB))
    state = _db_state(svc)
    assert [f[0] for f in state["files"]] == ["complete", "complete"]
    assert list(svc.store.iter_staged()) == []
    _assert_consistent(svc)


def test_mostly_duplicate_upload_is_judged_on_its_new_bytes_only(svc, monkeypatch):
    base = make_data(4, 300 * KIB)
    _up(svc, "v1.bin", base)
    edited = base[:700] + b"<<a small insertion>>" + base[700:]
    margin = svc.uploader._margin(len(edited))
    # only the chunk(s) around the insertion are new; room for the margin plus 200 KiB is plenty for that
    monkeypatch.setattr(svc.store, "free_bytes", lambda: margin + 200 * KIB)
    result = _up(svc, "v2.bin", edited)
    assert 0 < result.new_chunks < result.chunk_count
    _assert_consistent(svc)


def test_comfortable_free_space_costs_no_extra_work(svc, monkeypatch):
    monkeypatch.setattr(svc.store, "free_bytes", lambda: 10**15)
    monkeypatch.setattr(svc.uploader, "_new_bytes_needed", lambda *a: pytest.fail("dry-run chunking must not run"))
    assert _up(svc, "ok.bin", make_data(5, 100 * KIB)).new_chunks > 0


def test_the_check_is_skipped_when_free_space_cannot_be_read(svc, monkeypatch):
    monkeypatch.setattr(svc.store, "free_bytes", lambda: None)
    assert _up(svc, "ok.bin", make_data(6, 50 * KIB)).new_chunks > 0


def test_empty_upload_never_needs_space(svc, monkeypatch):
    monkeypatch.setattr(svc.store, "free_bytes", lambda: 0)
    assert _up(svc, "empty.bin", b"").chunk_count == 0


def test_free_bytes_is_read_from_the_chunk_store_volume(svc, monkeypatch):
    assert isinstance(svc.store.free_bytes(), int) and svc.store.free_bytes() > 0
    seen = []

    class Usage:
        free = 1234

    monkeypatch.setattr("app.storage.filesystem.shutil.disk_usage", lambda p: (seen.append(str(p)), Usage())[1])
    assert svc.store.free_bytes() == 1234
    assert any(str(svc.store.chunks_dir) == p for p in seen)  # the chunk store, not an unrelated volume
    monkeypatch.setattr("app.storage.filesystem.shutil.disk_usage", lambda p: (_ for _ in ()).throw(OSError("no")))
    assert svc.store.free_bytes() is None


def test_margin_accounts_for_staging_and_metadata(svc):
    cfg = svc.settings.cdc
    m = svc.uploader._margin(10 * 1024 * KIB)
    assert m >= svc.settings.free_space_reserve_bytes + cfg.max_chunk_size
    assert svc.uploader._margin(20 * 1024 * KIB) > m  # more chunks, more metadata


# ---------------------------------------------------------------- disk-full errors mid-upload

def _enospc():
    return OSError(errno.ENOSPC, "No space left on device")


def test_disk_full_while_staging_the_upload(svc, monkeypatch):
    def boom(_fd):
        raise _enospc()

    monkeypatch.setattr("app.deduplication.upload.os.fsync", boom)
    with pytest.raises(InsufficientStorageError):
        _up(svc, "a.bin", make_data(7, 100 * KIB))
    monkeypatch.undo()
    _nothing_left_behind(svc)
    assert _db_state(svc)["files"] == []
    _assert_consistent(svc)


def test_disk_full_while_staging_a_chunk_unpublishes_earlier_chunks(svc, monkeypatch):
    real_stage = svc.store.stage
    calls = {"n": 0}

    def stage(h, data):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ChunkWriteError("cannot stage chunk: No space left on device") from _enospc()
        return real_stage(h, data)

    monkeypatch.setattr(svc.store, "stage", stage)
    with pytest.raises(InsufficientStorageError):
        _up(svc, "a.bin", make_data(8, 400 * KIB))
    assert calls["n"] == 3
    _nothing_left_behind(svc)  # the two chunks this upload had published are gone again
    state = _db_state(svc)
    assert state["files"] == [("failed", None)] and state["chunks"] == 0 and state["manifest"] == 0
    assert state["ops"] == [("upload", "failed")]
    _assert_consistent(svc)


def test_disk_full_while_publishing_a_chunk(svc, monkeypatch):
    real_publish = svc.store.publish
    calls = {"n": 0}

    def publish(staged):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ChunkWriteError("cannot publish chunk: No space left on device") from _enospc()
        return real_publish(staged)

    monkeypatch.setattr(svc.store, "publish", publish)
    with pytest.raises(InsufficientStorageError):
        _up(svc, "a.bin", make_data(9, 400 * KIB))
    _nothing_left_behind(svc)
    assert _db_state(svc)["files"] == [("failed", None)]
    _assert_consistent(svc)


def test_sqlite_disk_full_in_the_final_transaction_rolls_everything_back(svc, monkeypatch):
    real = svc.uploader._repo.append_file_chunk
    calls = {"n": 0}

    def append(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise sqlite3.OperationalError("database or disk is full")
        return real(*a, **k)

    monkeypatch.setattr(svc.uploader._repo, "append_file_chunk", append)
    with pytest.raises(InsufficientStorageError):
        _up(svc, "a.bin", make_data(10, 400 * KIB))
    monkeypatch.undo()
    state = _db_state(svc)
    assert state["files"] == [("failed", None)]  # never 'complete'
    assert state["chunks"] == 0 and state["manifest"] == 0  # T2 rolled back as a unit
    _nothing_left_behind(svc)  # chunk files this upload created were un-published
    _assert_consistent(svc)


def test_a_disk_full_failure_does_not_damage_existing_data_and_the_next_upload_works(svc, monkeypatch):
    keep = make_data(11, 200 * KIB)
    kept = _up(svc, "keep.bin", keep)
    real_stage = svc.store.stage
    def full(_h, _d):
        raise _enospc()

    monkeypatch.setattr(svc.store, "stage", full)
    with pytest.raises(InsufficientStorageError):
        _up(svc, "new.bin", make_data(12, 200 * KIB))
    monkeypatch.setattr(svc.store, "stage", real_stage)
    assert _up(svc, "retry.bin", make_data(12, 200 * KIB)).new_chunks > 0  # retry after space is available
    out = svc.settings.data_dir / "restored.bin"
    svc.restorer.restore_to_file(kept.file_id, out)
    assert out.read_bytes() == keep
    _assert_consistent(svc)


# ---------------------------------------------------------------- classification, config, API

@pytest.mark.parametrize("exc,expected", [
    (OSError(errno.ENOSPC, "full"), True),
    (OSError(errno.EACCES, "denied"), False),
    (sqlite3.OperationalError("database or disk is full"), True),
    (sqlite3.OperationalError("database is locked"), False),
    (ValueError("nope"), False),
    (None, False),
])
def test_is_disk_full(exc, expected):
    assert is_disk_full(exc) is expected


def test_is_disk_full_follows_the_cause_chain_and_windows_codes():
    try:
        try:
            raise _enospc()
        except OSError as inner:
            raise ChunkWriteError("cannot publish chunk") from inner
    except ChunkWriteError as outer:
        assert is_disk_full(outer)
    win = OSError()
    win.winerror = 112
    assert is_disk_full(win)
    if hasattr(errno, "EDQUOT"):
        assert is_disk_full(OSError(errno.EDQUOT, "quota"))


def test_reserve_setting_is_validated_and_read_from_the_environment(tmp_path, monkeypatch):
    base = dict(data_dir=tmp_path, db_path=tmp_path / "x.db", cors_origins=("http://localhost:5173",))
    with pytest.raises(ConfigurationError):
        Settings(**base, free_space_reserve_bytes=-1)
    assert Settings(**base).free_space_reserve_bytes == 16 * 1024 * KIB
    monkeypatch.setenv("CHUNKVAULT_FREE_SPACE_RESERVE_BYTES", "12345")
    monkeypatch.setenv("CHUNKVAULT_DATA_DIR", str(tmp_path))
    assert Settings.from_env().free_space_reserve_bytes == 12345


def test_api_returns_507_with_a_clean_error_and_stores_nothing(settings):
    app = create_app(replace(settings, free_space_reserve_bytes=10**15))  # no volume has this much free
    with TestClient(app, raise_server_exceptions=False, base_url="http://localhost") as client:
        r = client.post("/api/files", files={"file": ("a.bin", make_data(13, 100 * KIB))})
        assert r.status_code == 507
        body = r.json()["error"]
        assert body["code"] == "insufficient_storage"
        assert str(settings.data_dir) not in r.text and "\\" not in body["message"]
        assert client.get("/api/files").json()["total"] == 0
        assert client.get("/api/storage/stats").json()["chunk_files"] == 0
        assert client.post("/api/integrity/audit", json={}).json()["healthy"] is True


def test_insufficient_storage_is_an_upload_error_so_existing_handlers_still_apply():
    assert issubclass(InsufficientStorageError, UploadError)
