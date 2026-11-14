"""One repository contract, run against BOTH implementations (SQLite always; PostgreSQL when TEST_DATABASE_URL is set).

Everything here goes through the ``Database`` port and the ``MetadataRepository`` interface only, so a pass on both backends
is the evidence that they are interchangeable behind it.
"""

import random
import re
import sqlite3

import pytest

from app.core.config import Settings
from app.database.factory import build_database, build_repository
from app.database.repository import ChunkSizeConflict, FileNotFoundInMetadata, RepositoryError
from app.database.states import ChunkState, FileStatus, OperationStatus, OperationType
from tests.pg_fixtures import H, pg_url, requires_postgres, schema_conn, schema_name  # noqa: F401

SHA = "f" * 64
TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=[pytest.mark.postgres, requires_postgres])])
def be(request, tmp_path):
    """(database, repository) of one backend, with the schema in place."""
    if request.param == "sqlite":
        s = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "c.db", cors_origins=("http://localhost:5173",))
        db = build_database(s)
        db.prepare_for_startup()
        yield db, build_repository(s)
        return
    from tests.pg_fixtures import cloud_settings  # noqa: PLC0415

    url = request.getfixturevalue("pg_url")
    owner = request.getfixturevalue("schema_conn")
    schema = request.getfixturevalue("schema_name")
    from tests.pg_fixtures import stamp_version  # noqa: PLC0415

    stamp_version(owner)
    s = cloud_settings(url, schema, tmp_path)
    db = build_database(s)
    db.prepare_for_startup()
    try:
        yield db, build_repository(s)
    finally:
        db.close()


@pytest.fixture
def ctx(be):
    db, repo = be
    with db.connection() as conn:
        yield db, repo, conn


def seed_chunk(db, repo, conn, h, size=100, state=ChunkState.STORED):
    with db.transaction(conn):
        return repo.register_chunk(conn, h, size, state)


def seed_file(db, repo, conn, fid="f1", name="a.bin", size=200, chunks=(), complete=True):
    """Create a file whose manifest is ``chunks`` = [(hash, size), ...] (chunk rows are registered when missing)."""
    with db.transaction(conn):
        repo.create_file(conn, fid, name, size)
        for i, (h, sz) in enumerate(chunks):
            repo.register_chunk(conn, h, sz, ChunkState.STORED)
            repo.append_file_chunk(conn, fid, i, h, sz)
        if complete:
            repo.set_file_status(conn, fid, FileStatus.COMPLETE, sha256=SHA)


# ----------------------------------------------------------------------------------------------------------- files

def test_file_crud_and_lifecycle(ctx):
    db, repo, conn = ctx
    with db.transaction(conn):
        f = repo.create_file(conn, "f1", "a.bin", 5)
    assert (f.file_id, f.filename, f.original_size, f.sha256, f.status) == ("f1", "a.bin", 5, None, FileStatus.UPLOADING)
    assert TS.match(f.created_at)
    assert repo.get_file(conn, "f1") == f and repo.get_file(conn, "nope") is None
    assert [x.file_id for x in repo.list_stuck_files(conn)] == ["f1"]
    with db.transaction(conn):
        repo.set_file_status(conn, "f1", FileStatus.COMPLETE, sha256=SHA)
    g = repo.get_file(conn, "f1")
    assert g.status is FileStatus.COMPLETE and g.sha256 == SHA and repo.list_stuck_files(conn) == []
    with pytest.raises(FileNotFoundInMetadata):
        with db.transaction(conn):
            repo.set_file_status(conn, "nope", FileStatus.FAILED)
    assert repo.count_files(conn) == 1


def test_complete_requires_sha256_and_ids_are_unique(ctx):
    db, repo, conn = ctx
    with db.transaction(conn):
        repo.create_file(conn, "f1", "a", 1)
    with pytest.raises(Exception):  # the schema's CHECK, whichever driver raises it
        with db.transaction(conn):
            repo.set_file_status(conn, "f1", FileStatus.COMPLETE)
    assert repo.get_file(conn, "f1").status is FileStatus.UPLOADING
    with pytest.raises(Exception):
        with db.transaction(conn):
            repo.create_file(conn, "f1", "dup", 1)


def test_list_files_is_newest_first_with_paging_and_unbounded_limit(ctx):
    db, repo, conn = ctx
    for i in range(5):
        seed_file(db, repo, conn, f"f{i}", complete=False)
    ids = [f.file_id for f in repo.list_files(conn, 100, 0)]
    assert sorted(ids) == [f"f{i}" for i in range(5)]
    assert [f.file_id for f in repo.list_files(conn, 2, 1)] == ids[1:3]
    assert [f.file_id for f in repo.list_files(conn, -1, 0)] == ids  # negative limit = no limit (the auditor relies on it)


# ---------------------------------------------------------------------------------------------------------- chunks

def test_register_chunk_is_idempotent_and_detects_size_conflicts(ctx):
    db, repo, conn = ctx
    rec, created = seed_chunk(db, repo, conn, H("a"), 100, ChunkState.PENDING)
    assert created and rec.state is ChunkState.PENDING and rec.ref_count == 0 and TS.match(rec.created_at)
    rec2, created2 = seed_chunk(db, repo, conn, H("a"), 100, ChunkState.STORED)
    assert not created2 and rec2.state is ChunkState.PENDING  # existing rows are not rewritten
    with pytest.raises(ChunkSizeConflict):
        seed_chunk(db, repo, conn, H("a"), 101)
    with db.transaction(conn):
        repo.mark_chunk_state(conn, H("a"), ChunkState.STORED)
    assert repo.get_chunk(conn, H("a")).state is ChunkState.STORED and repo.get_chunk(conn, H("b")) is None


def test_chunk_listing_counts_and_state_filter(ctx):
    db, repo, conn = ctx
    for h, st in ((H("c"), ChunkState.STORED), (H("a"), ChunkState.PENDING), (H("b"), ChunkState.STORED)):
        seed_chunk(db, repo, conn, h, 10, st)
    assert [c.hash for c in repo.list_chunks(conn, 10, 0)] == [H("a"), H("b"), H("c")]  # by hash, binary order
    assert [c.hash for c in repo.list_chunks(conn, 1, 1)] == [H("b")]
    assert [c.hash for c in repo.list_chunks(conn, -1, 0)] == [H("a"), H("b"), H("c")]
    assert [c.hash for c in repo.list_chunks(conn, 10, 0, ChunkState.PENDING)] == [H("a")]
    assert (repo.count_chunks(conn), repo.count_chunks(conn, ChunkState.STORED)) == (3, 2)


def test_unreferenced_chunks_are_listed_and_only_those_are_deleted(ctx):
    db, repo, conn = ctx
    seed_file(db, repo, conn, "f1", chunks=[(H("a"), 100)])
    seed_chunk(db, repo, conn, H("b"), 50)
    assert [c.hash for c in repo.unreferenced_chunks(conn)] == [H("b")]
    with db.transaction(conn):
        deleted = repo.delete_unreferenced_chunk_rows(conn, [H("a"), H("b"), H("c")])
    assert deleted == [H("b")]  # referenced and unknown hashes are left alone
    assert repo.get_chunk(conn, H("a")) is not None and repo.get_chunk(conn, H("b")) is None
    assert repo.delete_unreferenced_chunk_rows(conn, []) == []


def test_referenced_chunk_rows_can_never_be_removed(ctx):
    db, repo, conn = ctx
    seed_file(db, repo, conn, "f1", chunks=[(H("a"), 100)])
    with pytest.raises(Exception):  # RESTRICT foreign key
        with db.transaction(conn):
            repo.delete_chunk_row(conn, H("a"))
    assert repo.get_chunk(conn, H("a")).ref_count == 1


# --------------------------------------------------------------------------------------------- manifest / references

def test_manifest_is_ordered_and_duplicate_references_are_counted(ctx):
    db, repo, conn = ctx
    seed_file(db, repo, conn, "f1", size=300, chunks=[(H("a"), 100), (H("b"), 100), (H("a"), 100)])  # H("a") twice
    seed_file(db, repo, conn, "f2", size=100, chunks=[(H("a"), 100)])
    m = repo.get_file_chunks(conn, "f1")
    assert [(x.chunk_index, x.chunk_hash, x.chunk_size) for x in m] == [(0, H("a"), 100), (1, H("b"), 100), (2, H("a"), 100)]
    assert (repo.get_chunk(conn, H("a")).ref_count, repo.get_chunk(conn, H("b")).ref_count) == (3, 1)
    assert repo.chunks_referenced_by_file(conn, H("a")) == ["f1", "f2"]  # distinct files
    assert repo.count_manifest_references(conn, H("a")) == 3
    refs = repo.list_manifest_references(conn, H("a"), 10)
    assert [(r.file_id, r.chunk_index, r.filename, r.file_status) for r in refs] == [
        ("f1", 0, "a.bin", "complete"), ("f1", 2, "a.bin", "complete"), ("f2", 0, "a.bin", "complete")]
    assert len(repo.list_manifest_references(conn, H("a"), 2)) == 2
    assert repo.file_chunk_counts(conn, ["f1", "f2", "none"]) == {"f1": 3, "f2": 1}
    assert repo.file_chunk_counts(conn, []) == {}


def test_manifest_size_must_match_the_chunk_and_positions_are_unique(ctx):
    db, repo, conn = ctx
    seed_chunk(db, repo, conn, H("a"), 100)
    with db.transaction(conn):
        repo.create_file(conn, "f1", "a", 100)
        repo.append_file_chunk(conn, "f1", 0, H("a"), 100)
    with pytest.raises(Exception):
        with db.transaction(conn):
            repo.append_file_chunk(conn, "f1", 1, H("a"), 99)
    with pytest.raises(Exception):
        with db.transaction(conn):
            repo.append_file_chunk(conn, "f1", 0, H("a"), 100)
    assert repo.get_chunk(conn, H("a")).ref_count == 1


def test_delete_file_releases_references_and_returns_only_zero_ref_hashes(ctx):
    db, repo, conn = ctx
    seed_file(db, repo, conn, "f1", size=300, chunks=[(H("a"), 100), (H("b"), 100), (H("a"), 100)])
    seed_file(db, repo, conn, "f2", size=100, chunks=[(H("a"), 100)])
    with db.transaction(conn):
        assert repo.delete_file(conn, "f1") == [H("b")]  # H("a") is still used by f2
    assert repo.get_chunk(conn, H("a")).ref_count == 1 and repo.get_chunk(conn, H("b")).ref_count == 0
    with db.transaction(conn):
        assert repo.delete_file(conn, "f2") == [H("a")]
    assert repo.get_file_chunks(conn, "f2") == [] and repo.get_file(conn, "f2") is None
    with pytest.raises(FileNotFoundInMetadata):
        with db.transaction(conn):
            repo.delete_file(conn, "f2")


# ------------------------------------------------------------------------------------------------------- transactions

def test_a_failed_transaction_rolls_back_rows_and_reference_counts(ctx):
    db, repo, conn = ctx
    seed_file(db, repo, conn, "f1", chunks=[(H("a"), 100)])
    with pytest.raises(RuntimeError, match="boom"):
        with db.transaction(conn):
            repo.create_file(conn, "f2", "b", 100)
            repo.append_file_chunk(conn, "f2", 0, H("a"), 100)
            repo.delete_file(conn, "f1")
            assert repo.get_chunk(conn, H("a")).ref_count == 1  # visible inside the transaction
            raise RuntimeError("boom")
    assert repo.get_file(conn, "f2") is None and repo.get_file(conn, "f1") is not None
    assert repo.get_chunk(conn, H("a")).ref_count == 1 and repo.ref_count_mismatches(conn) == []
    with db.transaction(conn):  # the connection is usable again after a rollback
        repo.create_file(conn, "f3", "c", 1)
    assert repo.get_file(conn, "f3") is not None


def test_uncommitted_changes_are_invisible_to_other_connections_until_commit(ctx):
    db, repo, conn = ctx
    with db.connection() as other:
        with db.transaction(conn):
            repo.create_file(conn, "f1", "a", 1)
            assert repo.get_file(other, "f1") is None
        assert repo.get_file(other, "f1") is not None


def test_snapshot_is_read_only_and_consistent(ctx):
    db, repo, conn = ctx
    seed_file(db, repo, conn, "f1", chunks=[(H("a"), 100)])
    with db.connection() as writer:
        with db.snapshot(conn):
            before = repo.storage_totals(conn)
            seed_file(db, repo, writer, "f2", chunks=[(H("b"), 7)])
            assert repo.storage_totals(conn) == before  # a commit after the snapshot began is not visible inside it
            with pytest.raises(Exception):  # refuses writes (driver-specific error type; PostgreSQL then aborts the snapshot)
                repo.create_file(conn, "x", "x", 1)
        assert repo.storage_totals(conn).files == 2
    with db.transaction(conn):  # writable again afterwards
        repo.create_file(conn, "ok", "ok", 1)


# ------------------------------------------------------------------------------------------- reference-count audit

def test_ref_count_mismatch_view_reports_tampering_only(ctx):
    db, repo, conn = ctx
    seed_file(db, repo, conn, "f1", chunks=[(H("a"), 100), (H("a"), 100)])
    assert repo.ref_count_mismatches(conn) == []
    with db.transaction(conn):
        conn.execute("UPDATE chunks SET ref_count = 9")  # simulated corruption (portable statement)
    assert repo.ref_count_mismatches(conn) == [(H("a"), 9, 2)]


def test_reference_counts_stay_consistent_through_a_mixed_workload(ctx):
    db, repo, conn = ctx
    rnd = random.Random(7)
    hashes = [H(c) for c in "abcdef"]
    for h in hashes:
        seed_chunk(db, repo, conn, h, 10)
    live = []
    for n in range(40):
        if live and rnd.random() < 0.4:
            fid = live.pop(rnd.randrange(len(live)))
            with db.transaction(conn):
                repo.delete_file(conn, fid)
        else:
            fid = f"w{n}"
            picks = [rnd.choice(hashes) for _ in range(rnd.randint(1, 6))]
            seed_file(db, repo, conn, fid, size=10 * len(picks), chunks=[(h, 10) for h in picks])
            live.append(fid)
        assert repo.ref_count_mismatches(conn) == []
    expected = sum(len(repo.get_file_chunks(conn, f)) for f in live)
    assert sum(c.ref_count for c in repo.list_chunks(conn, -1, 0)) == expected


# --------------------------------------------------------------------------------------------------------- operations

def test_operations_lifecycle(ctx):
    db, repo, conn = ctx
    seed_file(db, repo, conn, "f1", complete=False)
    with db.transaction(conn):
        a = repo.start_operation(conn, OperationType.UPLOAD, "f1")
        b = repo.start_operation(conn, OperationType.INTEGRITY_CHECK, None)
    assert b > a
    running = repo.list_running_operations(conn)
    assert [(o.operation_id, o.operation_type, o.file_id, o.completed_at) for o in running] == [
        (a, OperationType.UPLOAD, "f1", None), (b, OperationType.INTEGRITY_CHECK, None, None)]
    assert TS.match(running[0].started_at)
    with db.transaction(conn):
        repo.finish_operation(conn, a, OperationStatus.SUCCEEDED)
        repo.finish_operation(conn, b, OperationStatus.FAILED)  # a failure always carries a message
    assert repo.list_running_operations(conn) == []
    with pytest.raises(RepositoryError):
        repo.finish_operation(conn, a, OperationStatus.RUNNING)
    with db.transaction(conn):  # history survives deleting the file; the link is cleared
        repo.delete_file(conn, "f1")
    _, rows = repo.list_rows(conn, "operations", 10, 0)
    assert [r[1] for r in rows] == [None, None] and [r[0] for r in rows] == [b, a]


# ------------------------------------------------------------------------------------------------- read models

def test_storage_totals_count_complete_files_only(ctx):
    db, repo, conn = ctx
    seed_file(db, repo, conn, "f1", size=300, chunks=[(H("a"), 100), (H("b"), 100), (H("a"), 100)])
    seed_file(db, repo, conn, "f2", size=100, chunks=[(H("a"), 100)])
    seed_file(db, repo, conn, "f3", size=50, chunks=[(H("c"), 50)], complete=False)  # still uploading: excluded
    seed_chunk(db, repo, conn, H("d"), 25)  # unreferenced
    t = repo.storage_totals(conn)
    assert (t.files, t.logical_bytes, t.references) == (2, 400, 4)
    assert (t.unique_chunks, t.unique_chunk_bytes) == (2, 200)
    assert (t.unreferenced_chunks, t.unreferenced_chunk_bytes) == (1, 25)
    assert all(type(v) is int for v in vars(t).values())  # never Decimal


def test_inspection_rows_use_the_same_shapes_and_order(ctx):
    db, repo, conn = ctx
    seed_file(db, repo, conn, "f1", size=200, chunks=[(H("a"), 100), (H("b"), 100)])
    assert [repo.count_rows(conn, t) for t in ("files", "chunks", "file_chunks", "operations")] == [1, 2, 2, 0]
    cols, rows = repo.list_rows(conn, "files", 10, 0)
    assert cols == ["file_id", "filename", "original_size", "sha256", "status", "created_at"]
    assert rows[0][:5] == ("f1", "a.bin", 200, SHA, "complete") and TS.match(rows[0][5])
    cols, rows = repo.list_rows(conn, "file_chunks", 10, 1)
    assert cols == ["file_id", "chunk_index", "chunk_hash", "chunk_size"] and rows == [("f1", 1, H("b"), 100)]
    cols, rows = repo.list_rows(conn, "chunks", 10, 0)
    assert cols == ["hash", "size_bytes", "ref_count", "state", "created_at"] and [r[0] for r in rows] == [H("a"), H("b")]
    for bad in ("benchmark_results", "files; DROP TABLE files", "sqlite_master", ""):
        with pytest.raises(ValueError):
            repo.count_rows(conn, bad)
        with pytest.raises(ValueError):
            repo.list_rows(conn, bad, 1, 0)


# --------------------------------------------------------------------------------------- schema / health / lifecycle

def test_schema_introspection_describes_tables_keys_and_foreign_keys(be):
    db, _ = be
    tables = {t.name: {c.name: c for c in t.columns} for t in db.introspect()}
    assert list(tables) == ["files", "chunks", "file_chunks", "operations"]
    assert set(tables["files"]) == {"file_id", "filename", "original_size", "sha256", "status", "created_at"}
    assert set(tables["chunks"]) == {"hash", "size_bytes", "ref_count", "state", "created_at"}
    assert set(tables["operations"]) >= {"operation_id", "file_id", "operation_type", "status", "started_at", "completed_at"}
    assert tables["files"]["file_id"].primary_key_position == 1 and tables["files"]["filename"].not_null
    assert tables["files"]["sha256"].not_null is False
    fc = tables["file_chunks"]
    assert (fc["file_id"].primary_key_position, fc["chunk_index"].primary_key_position) == (1, 2)
    fk = fc["file_id"].foreign_key
    assert (fk.table, fk.column, fk.on_delete) == ("files", "file_id", "CASCADE")
    assert (fc["chunk_hash"].foreign_key.table, fc["chunk_hash"].foreign_key.on_delete) == ("chunks", "RESTRICT")
    assert tables["operations"]["file_id"].foreign_key.on_delete == "SET NULL"
    assert tables["chunks"]["hash"].foreign_key is None


def test_health_version_and_footprint(be):
    db, _ = be
    h = db.ping()
    assert h.connected and h.foreign_keys_enforced and h.schema_version is not None
    assert db.footprint_bytes() > 0
    with db.connection() as conn:
        assert db.check(conn).connected


def test_sqlite_check_maps_driver_errors_to_unhealthy(tmp_path):
    s = Settings(data_dir=tmp_path, db_path=tmp_path / "c.db", cors_origins=("http://localhost:5173",))

    class Broken:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("boom")

    assert build_database(s).check(Broken()).connected is False


# --------------------------------------------------------------------------------------------- batched operations (cloud pipeline)

def test_batched_chunk_registration_and_lookup(ctx):
    db, repo, conn = ctx
    assert repo.get_chunks(conn, []) == {} and repo.register_chunks(conn, []) == []
    with db.transaction(conn):
        created = repo.register_chunks(conn, [(H("a"), 10), (H("b"), 20), (H("c"), 30)])
    assert created == [H("a"), H("b"), H("c")]
    rows = repo.get_chunks(conn, [H("a"), H("c"), H("d"), H("a")])  # duplicates and unknown hashes are fine
    assert set(rows) == {H("a"), H("c")} and rows[H("c")].size_bytes == 30 and rows[H("a")].state is ChunkState.PENDING
    with db.transaction(conn):
        again = repo.register_chunks(conn, [(H("b"), 20), (H("e"), 5)])
    assert again == [H("e")]  # existing rows are left alone
    with pytest.raises(ChunkSizeConflict):
        with db.transaction(conn):
            repo.register_chunks(conn, [(H("f"), 1), (H("a"), 11)])
    assert repo.get_chunk(conn, H("f")) is None  # the failed batch left nothing behind
    with db.transaction(conn):
        repo.mark_chunks_state(conn, [H("a"), H("b")], ChunkState.STORED)
        repo.mark_chunks_state(conn, [], ChunkState.STORED)
    assert [c.state for c in repo.list_chunks(conn, 10, 0)][:3] == [ChunkState.STORED, ChunkState.STORED, ChunkState.PENDING]
    assert repo.chunk_bytes_total(conn) == 10 + 20 + 30 + 5


def test_bulk_manifest_insert_counts_every_reference_in_one_statement(ctx):
    db, repo, conn = ctx
    with db.transaction(conn):
        repo.register_chunks(conn, [(H("a"), 100), (H("b"), 50)], ChunkState.STORED)
        repo.create_file(conn, "f1", "a", 250)
        repo.append_file_chunks(conn, "f1", [(0, H("a"), 100), (1, H("b"), 50), (2, H("a"), 100)])
        repo.append_file_chunks(conn, "f1", [])
    assert [(m.chunk_index, m.chunk_hash) for m in repo.get_file_chunks(conn, "f1")] == [(0, H("a")), (1, H("b")), (2, H("a"))]
    assert (repo.get_chunk(conn, H("a")).ref_count, repo.get_chunk(conn, H("b")).ref_count) == (2, 1)
    with pytest.raises(Exception):  # one bad row rejects the whole statement, counters included
        with db.transaction(conn):
            repo.create_file(conn, "f2", "b", 150)
            repo.append_file_chunks(conn, "f2", [(0, H("a"), 100), (1, H("b"), 999)])
    assert repo.get_file(conn, "f2") is None and repo.get_chunk(conn, H("a")).ref_count == 2
    assert repo.ref_count_mismatches(conn) == []


def test_gc_candidates_are_unreferenced_old_enough_and_bounded(ctx):
    db, repo, conn = ctx
    seed_file(db, repo, conn, "f1", chunks=[(H("a"), 100)])
    with db.transaction(conn):
        repo.register_chunks(conn, [(H("b"), 10), (H("c"), 20), (H("d"), 30)])
    assert [c.hash for c in repo.gc_candidates(conn, 0, 10)] == [H("b"), H("c"), H("d")]  # not H("a"): it is referenced
    assert repo.gc_candidates(conn, 3600, 10) == []  # too young
    assert len(repo.gc_candidates(conn, 0, 2)) == 2
    with db.transaction(conn):
        repo.delete_file(conn, "f1")
    assert H("a") in [c.hash for c in repo.gc_candidates(conn, 0, 10)]  # becomes a candidate once unreferenced


def test_storage_totals_report_stored_rows(ctx):
    db, repo, conn = ctx
    seed_file(db, repo, conn, "f1", chunks=[(H("a"), 100)])
    seed_chunk(db, repo, conn, H("b"), 7, ChunkState.PENDING)
    t = repo.storage_totals(conn)
    assert (t.stored_chunks, t.stored_chunk_bytes) == (1, 100)
