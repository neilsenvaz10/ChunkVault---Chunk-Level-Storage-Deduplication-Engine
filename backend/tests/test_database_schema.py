import sqlite3

import pytest

from app.core.errors import DatabaseError
from app.database.connection import connect, transaction
from app.database.init import SCHEMA_VERSION, get_schema_version, initialize_database
from app.database.states import ChunkState, FileStatus, OperationStatus, OperationType
from tests.conftest import H1, H2, add_chunk, add_file, map_chunk, ref_count

SHA = "d" * 64


# ---- initialisation -------------------------------------------------------

def test_init_creates_tables_without_data(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"files", "chunks", "file_chunks", "operations"} <= tables
    for t in ("files", "chunks", "file_chunks", "operations"):
        assert db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0


def test_init_is_idempotent_and_keeps_data(tmp_path):
    path = tmp_path / "x.db"
    assert initialize_database(path) == SCHEMA_VERSION
    conn = connect(path)
    add_file(conn)
    conn.close()
    assert initialize_database(path) == SCHEMA_VERSION
    conn = connect(path)
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
    conn.close()


def test_init_rejects_newer_schema(tmp_path):
    path = tmp_path / "x.db"
    initialize_database(path)
    conn = connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(DatabaseError):
        initialize_database(path)


def test_init_creates_missing_parent_directory(tmp_path):
    path = tmp_path / "a" / "b" / "x.db"
    initialize_database(path)
    assert path.exists()


def test_expected_indexes_exist(db):
    idx = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_file_chunks_hash", "idx_files_sha256", "idx_operations_file_id"} <= idx


# ---- foreign-key enforcement ---------------------------------------------

def test_foreign_keys_enabled_on_every_connection(tmp_path):
    path = tmp_path / "x.db"
    initialize_database(path)
    for _ in range(3):
        conn = connect(path)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.close()


def test_file_chunks_requires_existing_file(db):
    add_chunk(db)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        map_chunk(db, "missing", 0)


def test_file_chunks_requires_existing_chunk(db):
    add_file(db)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        map_chunk(db, "f1", 0, H1)


def test_referenced_chunk_cannot_be_deleted(db):
    add_file(db)
    add_chunk(db)
    map_chunk(db, "f1", 0)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("DELETE FROM chunks WHERE hash=?", (H1,))


def test_operation_requires_existing_file(db):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("INSERT INTO operations(file_id, operation_type) VALUES ('nope','upload')")


def test_deleting_file_keeps_operation_history(db):
    add_file(db)
    db.execute("INSERT INTO operations(file_id, operation_type) VALUES ('f1','upload')")
    db.execute("DELETE FROM files WHERE file_id='f1'")
    assert db.execute("SELECT file_id FROM operations").fetchone()[0] is None


# ---- primary keys / uniqueness -------------------------------------------

def test_duplicate_file_id_rejected(db):
    add_file(db)
    with pytest.raises(sqlite3.IntegrityError):
        add_file(db)


def test_duplicate_chunk_hash_rejected(db):
    add_chunk(db)
    with pytest.raises(sqlite3.IntegrityError):
        add_chunk(db)


def test_composite_primary_key_file_chunks(db):
    add_file(db, "f1")
    add_file(db, "f2")
    add_chunk(db)
    map_chunk(db, "f1", 0)
    map_chunk(db, "f2", 0)  # same index, different file: fine
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        map_chunk(db, "f1", 0)  # same (file_id, chunk_index)


# ---- invalid records ------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "x" * 64, "A" * 64, "a" * 63, "a" * 65])
def test_bad_chunk_hash_rejected(db, bad):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_chunk(db, bad)


def test_bad_chunk_values_rejected(db):
    for size, ref in ((0, 0), (-1, 0)):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute("INSERT INTO chunks(hash,size_bytes,ref_count) VALUES (?,?,?)", (H1, size, ref))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        db.execute("INSERT INTO chunks(hash,size_bytes,ref_count) VALUES (?,?,?)", (H1, 5, -1))


def test_bad_file_values_rejected(db):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_file(db, size=-1)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_file(db, sha="not-a-hash")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        db.execute("INSERT INTO files(file_id, filename, original_size) VALUES ('f','',1)")


def test_complete_file_requires_sha256(db):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_file(db, status="complete", sha=None)
    add_file(db, status="complete", sha=SHA)


def test_null_required_columns_rejected(db):
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        db.execute("INSERT INTO files(file_id, filename) VALUES ('f','a')")


def test_file_chunk_values_rejected(db):
    add_file(db)
    add_chunk(db)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        map_chunk(db, "f1", -1)
    with pytest.raises(sqlite3.IntegrityError):  # rejected by the size-match trigger / CHECK
        map_chunk(db, "f1", 0, size=0)


def test_file_chunk_size_must_match_chunk(db):
    add_file(db)
    add_chunk(db, size=100)
    with pytest.raises(sqlite3.IntegrityError, match="chunk_size must equal"):
        map_chunk(db, "f1", 0, size=99)


def test_operation_state_rules(db):
    db.execute("INSERT INTO operations(operation_type) VALUES ('benchmark')")  # running, no file: ok
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):  # running but has completed_at
        db.execute("INSERT INTO operations(operation_type, completed_at) VALUES ('upload','2026-01-01')")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):  # finished without completed_at
        db.execute("INSERT INTO operations(operation_type, status) VALUES ('upload','succeeded')")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):  # failed without message
        db.execute(
            "INSERT INTO operations(operation_type, status, completed_at) VALUES ('upload','failed','t')"
        )
    db.execute(
        "INSERT INTO operations(operation_type,status,completed_at,error_message) "
        "VALUES ('upload','failed','t','boom')"
    )


# ---- state enums match the schema ----------------------------------------

def test_state_enums_match_check_constraints(db):
    for i, s in enumerate(FileStatus):
        add_file(db, f"s{i}", status=s.value, sha=SHA)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_file(db, "bad", status="weird")
    for i, s in enumerate(ChunkState):
        add_chunk(db, f"{i:064x}", state=s.value)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_chunk(db, H2, state="weird")
    for t in OperationType:
        db.execute("INSERT INTO operations(operation_type) VALUES (?)", (t.value,))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        db.execute("INSERT INTO operations(operation_type) VALUES ('weird')")
    for s in OperationStatus:
        db.execute(
            "INSERT INTO operations(operation_type,status,completed_at,error_message) VALUES ('upload',?,?,?)",
            (s.value, None if s is OperationStatus.RUNNING else "t", "e"),
        )


# ---- reference-count invariant -------------------------------------------

def test_ref_count_counts_each_occurrence(db):
    add_file(db, "f1")
    add_file(db, "f2")
    add_chunk(db)
    map_chunk(db, "f1", 0)
    map_chunk(db, "f1", 1)  # same chunk twice in one file
    assert ref_count(db, H1) == 2
    map_chunk(db, "f2", 0)  # and once in another file
    assert ref_count(db, H1) == 3
    assert db.execute("SELECT COUNT(*) FROM v_ref_count_mismatch").fetchone()[0] == 0


def test_ref_count_released_on_file_delete_cascade(db):
    add_file(db, "f1")
    add_file(db, "f2")
    add_chunk(db)
    map_chunk(db, "f1", 0)
    map_chunk(db, "f1", 1)
    map_chunk(db, "f2", 0)
    db.execute("DELETE FROM files WHERE file_id='f1'")
    assert ref_count(db, H1) == 1
    db.execute("DELETE FROM files WHERE file_id='f2'")
    assert ref_count(db, H1) == 0
    db.execute("DELETE FROM chunks WHERE hash=?", (H1,))  # now unreferenced: allowed


def test_mismatch_view_detects_drift(db):
    add_file(db)
    add_chunk(db)
    map_chunk(db, "f1", 0)
    db.execute("UPDATE chunks SET ref_count = 5 WHERE hash=?", (H1,))
    assert [tuple(r) for r in db.execute("SELECT * FROM v_ref_count_mismatch")] == [(H1, 5, 1)]


def test_immutable_rows(db):
    add_file(db)
    add_chunk(db)
    add_chunk(db, H2)
    map_chunk(db, "f1", 0)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE file_chunks SET chunk_hash=? WHERE file_id='f1'", (H2,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE chunks SET size_bytes=1 WHERE hash=?", (H1,))
    db.execute("UPDATE chunks SET state='corrupt' WHERE hash=?", (H1,))  # state is mutable


# ---- transactions ---------------------------------------------------------

def test_transaction_commits(db):
    with transaction(db):
        add_file(db)
    assert db.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1


def test_transaction_rolls_back_everything_on_error(db):
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(db):
            add_file(db)
            add_chunk(db)
            map_chunk(db, "f1", 0)
            map_chunk(db, "f1", 0)  # PK violation
    for t in ("files", "chunks", "file_chunks"):
        assert db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    assert get_schema_version(db) == SCHEMA_VERSION
