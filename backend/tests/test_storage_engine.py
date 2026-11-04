"""Service-level tests of the storage engine on isolated temporary stores."""

import hashlib
import io
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.core.config import KIB, Settings
from app.core.locking import WriterBusyError, WriterLock
from app.core.services import build_services
from app.database.connection import connect
from app.database.repository import FileNotFoundInMetadata
from app.deduplication.interfaces import (
    ChunkUnavailableError,
    EmptyFilenameError,
    UploadError,
    UploadTooLargeError,
)
from app.deduplication.upload import sanitize_filename
from app.restoration.interfaces import (
    ChunkCorruptionError,
    FileNotRestorableError,
    MissingChunkError,
)
from app.storage.interfaces import ChunkWriteError
from app.storage.stats import compute_stats
from tests.conftest import make_data

EMPTY_SHA = hashlib.sha256(b"").hexdigest()


class SimulatedCrash(BaseException):
    """Stands in for the process dying: the orchestrator performs no cleanup for it."""


def up(svc, name, data):
    return svc.uploader.upload(name, io.BytesIO(data))


def restore(svc, file_id, tmp_path, name="out.bin"):
    dest = tmp_path / name
    svc.restorer.restore_to_file(file_id, dest)
    return dest.read_bytes()


def rows(svc, sql, *args):
    conn = connect(svc.settings.db_path)
    try:
        return [tuple(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def chunk_files(svc):
    return sorted(svc.store.iter_hashes())


def staging(svc):
    return sorted(p.name for p in svc.store.iter_staged())


def kinds(report):
    return sorted(f.kind for f in report.findings)


def snapshot(svc):
    tables = {t: rows(svc, f"SELECT * FROM {t} ORDER BY 1, 2") for t in ("files", "chunks", "file_chunks", "operations")}
    return tables, chunk_files(svc), staging(svc)


def assert_metadata_consistent(svc):
    assert rows(svc, "SELECT * FROM v_ref_count_mismatch") == []


# ---- functional ---------------------------------------------------------------

def test_upload_and_restore_exact_bytes(svc, tmp_path):
    data = make_data(1, 300 * KIB)
    r = up(svc, "a.bin", data)
    assert r.original_size == len(data) and r.sha256 == hashlib.sha256(data).hexdigest()
    assert r.chunk_count == r.new_chunks + r.duplicate_chunks and r.new_chunks > 3
    assert restore(svc, r.file_id, tmp_path) == data
    assert len(chunk_files(svc)) == r.new_chunks
    assert rows(svc, "SELECT status FROM files") == [("complete",)]
    assert_metadata_consistent(svc)


def test_manifest_preserves_exact_order(svc):
    data = make_data(2, 200 * KIB)
    r = up(svc, "a.bin", data)
    conn = connect(svc.settings.db_path)
    manifest = svc.repo.get_file_chunks(conn, r.file_id)
    conn.close()
    assert [m.chunk_index for m in manifest] == list(range(r.chunk_count))
    rebuilt = b"".join(svc.store.read(m.chunk_hash) for m in manifest)
    assert rebuilt == data


def test_empty_file(svc, tmp_path):
    r = up(svc, "empty", b"")
    assert (r.original_size, r.chunk_count, r.sha256) == (0, 0, EMPTY_SHA)
    assert restore(svc, r.file_id, tmp_path) == b""
    assert chunk_files(svc) == []


def test_file_smaller_than_minimum_chunk(svc, tmp_path):
    data = b"tiny"
    r = up(svc, "t", data)
    assert r.chunk_count == 1
    assert restore(svc, r.file_id, tmp_path) == data


def test_exact_duplicate_creates_new_record_but_no_new_bytes(svc, tmp_path):
    data = make_data(3, 250 * KIB)
    a = up(svc, "a.bin", data)
    before = chunk_files(svc)
    b = up(svc, "copy.bin", data)
    assert a.file_id != b.file_id
    assert b.new_chunks == 0 and b.bytes_written == 0 and b.duplicate_chunks == b.chunk_count
    assert chunk_files(svc) == before
    assert len(rows(svc, "SELECT 1 FROM files")) == 2
    assert all(rc == 2 for (rc,) in rows(svc, "SELECT ref_count FROM chunks"))
    assert_metadata_consistent(svc)
    assert restore(svc, b.file_id, tmp_path) == data


def test_modified_file_reuses_most_chunks(svc, tmp_path):
    data = make_data(4, 400 * KIB)
    a = up(svc, "a.bin", data)
    edited = data[:300] + b"-- a few inserted bytes --" + data[300:]
    b = up(svc, "b.bin", edited)
    assert 0 < b.new_chunks <= 3
    assert b.duplicate_chunks >= b.chunk_count - 3
    assert len(chunk_files(svc)) == a.new_chunks + b.new_chunks
    assert restore(svc, b.file_id, tmp_path) == edited
    assert_metadata_consistent(svc)


def test_repeated_chunk_within_one_file_counts_every_occurrence(svc, tmp_path):
    data = bytes(300 * KIB)  # zeros: chunks of 128K, 128K, 44K -> first two identical
    r = up(svc, "zeros", data)
    assert r.chunk_count == 3 and r.new_chunks == 2 and r.duplicate_chunks == 1
    assert len(chunk_files(svc)) == 2
    assert sorted(rc for (rc,) in rows(svc, "SELECT ref_count FROM chunks")) == [1, 2]
    assert rows(svc, "SELECT COUNT(*) FROM file_chunks") == [(3,)]
    assert_metadata_consistent(svc)
    assert restore(svc, r.file_id, tmp_path) == data


def test_binary_all_byte_values(svc, tmp_path):
    data = bytes(range(256)) * 700
    r = up(svc, "bytes.bin", data)
    assert restore(svc, r.file_id, tmp_path) == data


# ---- deletion --------------------------------------------------------------------

def test_delete_keeps_shared_chunks_and_reclaims_unique_ones(svc, tmp_path):
    data = make_data(5, 300 * KIB)
    edited = data[:200] + b"X" * 40 + data[200:]
    a, b = up(svc, "a", data), up(svc, "b", edited)
    res = svc.deleter.delete(a.file_id)
    assert res.chunks_released == a.chunk_count
    assert res.chunks_reclaimed >= 1 and res.bytes_reclaimed > 0 and res.cleanup_failed == 0
    assert restore(svc, b.file_id, tmp_path) == edited  # survivor fully restorable
    assert len(chunk_files(svc)) == b.chunk_count  # b has all distinct chunks
    assert_metadata_consistent(svc)
    assert svc.auditor.verify_store().healthy
    svc.deleter.delete(b.file_id)
    assert chunk_files(svc) == [] and rows(svc, "SELECT COUNT(*) FROM chunks") == [(0,)]


def test_delete_file_with_repeated_chunk(svc):
    r = up(svc, "zeros", bytes(300 * KIB))
    res = svc.deleter.delete(r.file_id)
    assert res.chunks_released == 3 and res.chunks_reclaimed == 2
    assert chunk_files(svc) == []


def test_repeated_delete_is_a_clean_not_found(svc):
    r = up(svc, "a", b"hello")
    svc.deleter.delete(r.file_id)
    snap = snapshot(svc)
    with pytest.raises(FileNotFoundInMetadata):
        svc.deleter.delete(r.file_id)
    assert snapshot(svc) == snap


def test_interrupted_cleanup_leaves_only_orphans_found_by_audit_and_removed_by_reconcile(svc, monkeypatch):
    r = up(svc, "a", make_data(6, 100 * KIB))
    monkeypatch.setattr(svc.store, "delete", lambda h: (_ for _ in ()).throw(ChunkWriteError("disk error")))
    res = svc.deleter.delete(r.file_id)
    assert res.chunks_reclaimed == 0 and res.cleanup_failed > 0
    monkeypatch.undo()
    # metadata is already consistent; only bytes remain
    assert rows(svc, "SELECT COUNT(*) FROM chunks") == [(0,)]
    rep = svc.auditor.verify_store()
    assert set(kinds(rep)) == {"orphan_chunk_file"}
    out = svc.reconciler.reconcile()
    assert {a.action for a in out.actions} == {"delete_orphan_chunk"}
    assert out.remaining.healthy and chunk_files(svc) == []


def test_crash_between_delete_commit_and_unlink(svc, monkeypatch):
    r = up(svc, "a", make_data(7, 100 * KIB))
    monkeypatch.setattr(svc.store, "delete", lambda h: (_ for _ in ()).throw(SimulatedCrash()))
    with pytest.raises(SimulatedCrash):
        svc.deleter.delete(r.file_id)
    monkeypatch.undo()
    assert rows(svc, "SELECT COUNT(*) FROM files") == [(0,)]
    assert "orphan_chunk_file" in kinds(svc.auditor.verify_store())
    assert svc.reconciler.reconcile().remaining.healthy


# ---- failed uploads ------------------------------------------------------------------

def test_failure_in_metadata_commit_rolls_back_and_leaves_nothing_complete(svc, monkeypatch):
    calls = {"n": 0}
    orig = svc.repo.append_file_chunk

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise sqlite3.OperationalError("disk I/O error")
        return orig(*a, **k)

    monkeypatch.setattr(svc.repo, "append_file_chunk", flaky)
    with pytest.raises(UploadError):
        up(svc, "a", make_data(8, 200 * KIB))
    monkeypatch.undo()
    assert rows(svc, "SELECT status FROM files") == [("failed",)]
    assert rows(svc, "SELECT COUNT(*) FROM file_chunks") == [(0,)]  # T2 rolled back as a unit
    assert rows(svc, "SELECT COUNT(*) FROM chunks") == [(0,)]
    assert rows(svc, "SELECT status, operation_type FROM operations") == [("failed", "upload")]
    assert chunk_files(svc) == [] and staging(svc) == []  # unreferenced bytes removed again
    assert svc.auditor.verify_store().healthy


def test_failure_never_removes_pre_existing_chunks(svc, monkeypatch):
    data = make_data(9, 200 * KIB)
    a = up(svc, "a", data)
    before = chunk_files(svc)
    monkeypatch.setattr(svc.repo, "append_file_chunk",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("boom")))
    with pytest.raises(UploadError):
        up(svc, "b", data + b"extra-tail")
    monkeypatch.undo()
    assert set(before) <= set(chunk_files(svc))
    assert svc.auditor.verify_file(a.file_id).healthy


def test_crash_after_some_chunks_published_then_retry_reuses_them(svc, monkeypatch, tmp_path):
    data = make_data(10, 300 * KIB)
    orig, n = svc.store.publish, {"c": 0}

    def crashing(staged):
        if n["c"] >= 2:
            raise SimulatedCrash()
        n["c"] += 1
        return orig(staged)

    monkeypatch.setattr(svc.store, "publish", crashing)
    with pytest.raises(SimulatedCrash):
        up(svc, "a", data)
    monkeypatch.undo()

    # No completed file; leftovers exist and are detectable.
    assert rows(svc, "SELECT COUNT(*) FROM files WHERE status='complete'") == [(0,)]
    assert rows(svc, "SELECT status FROM files") == [("uploading",)]
    assert rows(svc, "SELECT status FROM operations") == [("running",)]
    assert len(chunk_files(svc)) == 2 and len(staging(svc)) == 2
    rep = svc.auditor.verify_store()
    assert {"orphan_chunk_file", "orphan_staging_file", "incomplete_file", "stuck_operation"} <= set(kinds(rep))

    # Retry restarts the upload and reuses the valid, previously published chunks.
    r = up(svc, "a", data)
    assert r.duplicate_chunks >= 2 and r.new_chunks == r.chunk_count - r.duplicate_chunks
    assert restore(svc, r.file_id, tmp_path) == data

    out = svc.reconciler.reconcile()  # clears the dead first attempt
    assert {a.action for a in out.actions} == {"delete_staging_file", "abort_operation", "fail_incomplete_file"}
    assert out.remaining.healthy
    assert restore(svc, r.file_id, tmp_path, "again.bin") == data  # reconcile never touches referenced chunks


def test_crash_before_metadata_commit_then_reconcile_after_restart(svc, monkeypatch, settings):
    data = make_data(11, 200 * KIB)
    orig = svc.repo.finish_operation

    def die_on_commit(conn, op_id, status, msg=None):
        if status.value == "succeeded":
            raise SimulatedCrash()
        return orig(conn, op_id, status, msg)

    monkeypatch.setattr(svc.repo, "finish_operation", die_on_commit)
    with pytest.raises(SimulatedCrash):
        up(svc, "a", data)
    monkeypatch.undo()
    assert rows(svc, "SELECT COUNT(*) FROM file_chunks") == [(0,)]
    assert rows(svc, "SELECT COUNT(*) FROM chunks") == [(0,)]  # T2 rolled back
    assert len(chunk_files(svc)) > 3  # all bytes published, none referenced

    restarted = build_services(settings)  # "restart": brand new objects, same disk
    assert "orphan_chunk_file" in kinds(restarted.auditor.verify_store())
    out = restarted.reconciler.reconcile()
    assert out.remaining.healthy and chunk_files(restarted) == [] and staging(restarted) == []
    actions = {a.action for a in out.actions}
    assert {"delete_orphan_chunk", "abort_operation", "fail_incomplete_file"} <= actions


def test_crash_while_receiving_upload_leaves_only_a_staging_file(svc):
    class Dying(io.RawIOBase):
        def __init__(self):
            self.sent = False

        def readable(self):
            return True

        def read(self, n=-1):
            if not self.sent:
                self.sent = True
                return b"x" * 1000
            raise SimulatedCrash()

    with pytest.raises(SimulatedCrash):
        svc.uploader.upload("a", Dying())
    assert rows(svc, "SELECT COUNT(*) FROM files") == [(0,)]
    assert len(staging(svc)) == 1 and chunk_files(svc) == []
    assert kinds(svc.auditor.verify_store()) == ["orphan_staging_file"]
    svc.reconciler.reconcile()
    assert staging(svc) == []


def test_upload_size_limit(tmp_path):
    s = Settings(data_dir=tmp_path / "d", db_path=tmp_path / "d" / "x.db",
                 cors_origins=("http://localhost:5173",), max_upload_bytes=10_000)
    s.ensure_directories()
    from app.database.init import initialize_database

    initialize_database(s.db_path)
    svc = build_services(s)
    with pytest.raises(UploadTooLargeError):
        up(svc, "big", b"x" * 10_001)
    up(svc, "ok", b"x" * 10_000)
    assert rows(svc, "SELECT COUNT(*) FROM files") == [(1,)]
    assert staging(svc) == []


@pytest.mark.parametrize("raw,expected", [
    ("report.pdf", "report.pdf"), ("../../etc/passwd", "passwd"), ("C:\\temp\\a.txt", "a.txt"), (" x ", "x"),
])
def test_filename_sanitised(raw, expected):
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "..", "a\x00b", "x/", "n" * 256])
def test_bad_filenames_rejected(raw):
    with pytest.raises(EmptyFilenameError):
        sanitize_filename(raw)


# ---- restoration failures ---------------------------------------------------------------

def first_chunk(svc, file_id):
    conn = connect(svc.settings.db_path)
    try:
        return svc.repo.get_file_chunks(conn, file_id)[0].chunk_hash
    finally:
        conn.close()


def test_restore_unknown_or_incomplete_file(svc, tmp_path):
    with pytest.raises(FileNotRestorableError):
        svc.restorer.restore_to_file("0" * 32, tmp_path / "o")
    conn = connect(svc.settings.db_path)
    svc.repo.create_file(conn, "1" * 32, "partial", 5)  # status 'uploading'
    conn.close()
    with pytest.raises(FileNotRestorableError):
        svc.restorer.restore_to_file("1" * 32, tmp_path / "o")
    assert not (tmp_path / "o").exists()


def test_missing_chunk_fails_restore_and_is_reported(svc, tmp_path):
    r = up(svc, "a", make_data(12, 200 * KIB))
    h = first_chunk(svc, r.file_id)
    svc.store.path_for(h).unlink()
    dest = tmp_path / "out.bin"
    with pytest.raises(MissingChunkError):
        svc.restorer.restore_to_file(r.file_id, dest)
    assert not dest.exists()  # never a partial restoration
    assert ("failed", "restore") in rows(svc, "SELECT status, operation_type FROM operations")
    rep = svc.auditor.verify_store()
    f = [x for x in rep.findings if x.kind == "missing_chunk_file"]
    assert [x.subject for x in f] == [h] and r.file_id in f[0].detail
    assert "missing_chunk_file" in kinds(svc.auditor.verify_file(r.file_id))


def test_corrupt_chunk_fails_restore_is_reported_and_not_repaired(svc, tmp_path):
    data = make_data(13, 200 * KIB)
    r = up(svc, "a", data)
    h = first_chunk(svc, r.file_id)
    p = svc.store.path_for(h)
    raw = bytearray(p.read_bytes())
    raw[len(raw) // 2] ^= 0x01  # same size, one flipped bit
    p.write_bytes(bytes(raw))
    dest = tmp_path / "out.bin"
    with pytest.raises(ChunkCorruptionError):
        svc.restorer.restore_to_file(r.file_id, dest)
    assert not dest.exists()
    rep = svc.auditor.verify_store()
    assert [x.subject for x in rep.findings if x.kind == "corrupt_chunk"] == [h]
    # An ordinary re-upload must not silently overwrite the corrupt chunk.
    with pytest.raises(ChunkUnavailableError):
        up(svc, "again", data)
    assert p.read_bytes() == bytes(raw)
    # Reconcile reports but does not repair or delete a referenced corrupt chunk.
    out = svc.reconciler.reconcile()
    assert p.exists() and any(x.kind == "corrupt_chunk" for x in out.remaining.findings)


def test_truncated_chunk_detected(svc):
    r = up(svc, "a", make_data(14, 100 * KIB))
    h = first_chunk(svc, r.file_id)
    p = svc.store.path_for(h)
    p.write_bytes(p.read_bytes()[:-5])
    assert [x.subject for x in svc.auditor.verify_store().findings if x.kind == "corrupt_chunk"] == [h]


def test_whole_file_hash_mismatch_detected(svc, tmp_path):
    from app.restoration.interfaces import FileHashMismatchError

    r = up(svc, "a", make_data(15, 50 * KIB))
    conn = connect(svc.settings.db_path)
    conn.execute("UPDATE files SET sha256=? WHERE file_id=?", ("f" * 64, r.file_id))
    conn.close()
    with pytest.raises(FileHashMismatchError):
        svc.restorer.restore_to_file(r.file_id, tmp_path / "o")
    assert "file_hash_mismatch" in kinds(svc.auditor.verify_file(r.file_id))


# ---- audit ------------------------------------------------------------------------------------

def test_audit_of_healthy_store_is_clean(svc):
    up(svc, "a", make_data(16, 200 * KIB))
    up(svc, "b", make_data(17, 100 * KIB))
    rep = svc.auditor.verify_store()
    assert rep.healthy and rep.files_checked == 2 and rep.chunks_checked > 5
    assert svc.auditor.verify_store(verify_chunk_bytes=False).healthy


def test_incorrect_reference_count_reported_but_not_modified(svc):
    r = up(svc, "a", make_data(18, 100 * KIB))
    h = first_chunk(svc, r.file_id)
    conn = connect(svc.settings.db_path)
    conn.execute("UPDATE chunks SET ref_count = 7 WHERE hash=?", (h,))
    conn.close()
    rep = svc.auditor.verify_store()
    assert [f.subject for f in rep.findings if f.kind == "ref_count_mismatch"] == [h]
    assert "recorded ref_count 7" in rep.findings[0].detail
    svc.reconciler.reconcile()
    assert rows(svc, "SELECT ref_count FROM chunks WHERE hash=?", h) == [(7,)]  # needs a human


def test_manifest_gap_detected(svc):
    r = up(svc, "a", make_data(19, 100 * KIB))
    conn = connect(svc.settings.db_path)
    conn.execute("DELETE FROM file_chunks WHERE file_id=? AND chunk_index=1", (r.file_id,))
    conn.close()
    assert "manifest_inconsistent" in kinds(svc.auditor.verify_store())


def test_audit_is_strictly_read_only(svc):
    r = up(svc, "a", make_data(20, 150 * KIB))
    svc.store.path_for(first_chunk(svc, r.file_id)).write_bytes(b"corrupt")
    (svc.store.staging_dir / "junk.tmp").write_bytes(b"x")
    (svc.store.chunks_dir / "aa").mkdir(exist_ok=True)
    orphan = svc.hasher.digest(b"orphan")
    (svc.store.chunks_dir / orphan[:2]).mkdir(exist_ok=True)
    (svc.store.chunks_dir / orphan[:2] / orphan).write_bytes(b"orphan")
    before = snapshot(svc)
    rep = svc.auditor.verify_store()
    assert {"corrupt_chunk", "orphan_staging_file", "orphan_chunk_file"} <= set(kinds(rep))
    assert snapshot(svc) == before  # no row, chunk file or staging file changed
    # the audit connection itself refuses writes
    conn = svc.auditor._conn()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM files")
    conn.close()


def test_audit_unknown_file(svc):
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        svc.auditor.verify_file("0" * 32)


def test_reconcile_is_idempotent_and_leaves_valid_data_alone(svc, tmp_path):
    data = make_data(21, 200 * KIB)
    r = up(svc, "a", data)
    snap = snapshot(svc)
    out = svc.reconciler.reconcile()
    assert out.actions == [] and snapshot(svc) == snap
    assert restore(svc, r.file_id, tmp_path) == data


# ---- statistics ----------------------------------------------------------------------------------

def test_stats_empty_store_has_no_ratios(svc):
    s = compute_stats(svc.settings, svc.store)
    assert (s.total_files, s.logical_bytes, s.unique_chunk_bytes) == (0, 0, 0)
    assert s.storage_savings_percent is None and s.deduplication_ratio is None
    assert s.database_bytes > 0 and s.chunk_files == 0


def test_stats_formulas_on_exact_duplicate(svc):
    data = make_data(22, 200 * KIB)
    a = up(svc, "a", data)
    up(svc, "b", data)
    s = compute_stats(svc.settings, svc.store)
    assert s.total_files == 2 and s.logical_bytes == 2 * len(data)
    assert s.unique_chunk_bytes == len(data) and s.unique_chunks == a.chunk_count
    assert s.total_chunk_references == 2 * a.chunk_count and s.reused_chunk_references == a.chunk_count
    assert s.storage_savings_percent == pytest.approx(50.0)
    assert s.deduplication_ratio == pytest.approx(2.0)
    assert s.chunk_files == a.chunk_count and s.chunk_store_bytes == len(data)
    assert s.staging_files == 0 and s.unreferenced_chunks == 0


def test_stats_ignore_failed_uploads(svc, monkeypatch):
    monkeypatch.setattr(svc.repo, "append_file_chunk",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("x")))
    with pytest.raises(UploadError):
        up(svc, "a", make_data(23, 50 * KIB))
    s = compute_stats(svc.settings, svc.store)
    assert s.total_files == 0 and s.logical_bytes == 0


# ---- single-writer guard ----------------------------------------------------------------------------

def test_writer_lock_blocks_in_process(tmp_path):
    lock = WriterLock(tmp_path / "w.lock", timeout_seconds=0.2)
    held, release = threading.Event(), threading.Event()

    def hold():
        with lock.acquire():
            held.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        assert not lock.is_free()
        with pytest.raises(WriterBusyError):
            with lock.acquire():
                pass
    finally:
        release.set()
        t.join()
    assert lock.is_free()


def test_upload_and_delete_refuse_while_another_writer_holds_the_lock(svc):
    svc.lock._timeout = 0.2
    held, release = threading.Event(), threading.Event()

    def hold():
        with svc.lock.acquire():
            held.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        with pytest.raises(WriterBusyError):
            up(svc, "a", b"data")
        with pytest.raises(WriterBusyError):
            svc.deleter.delete("0" * 32)
        with pytest.raises(WriterBusyError):
            svc.reconciler.reconcile()
    finally:
        release.set()
        t.join()
    assert rows(svc, "SELECT COUNT(*) FROM files") == [(0,)]


def test_writer_lock_excludes_other_processes(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    code = ("import sys,time\nfrom app.core.locking import WriterLock\n"
            "with WriterLock(sys.argv[1], 5).acquire():\n print('locked', flush=True); time.sleep(2.0)\n")
    path = tmp_path / "w.lock"
    child = subprocess.Popen([sys.executable, "-c", code, str(path)], cwd=backend,
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(WriterBusyError):
            with WriterLock(path, 0.3).acquire():
                pass
    finally:
        child.wait(timeout=10)
    with WriterLock(path, 2).acquire():
        pass
