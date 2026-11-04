"""SQLite implementation of ``MetadataRepository``.

It never begins, commits or rolls back transactions: callers own boundaries
(``database.connection.transaction``). It also never touches ``ref_count``:
the Phase 1 triggers on ``file_chunks`` are the only mechanism that maintains it.
"""

from __future__ import annotations

import sqlite3

from app.database.repository import (
    ChunkRecord,
    ChunkSizeConflict,
    FileChunkRecord,
    FileNotFoundInMetadata,
    FileRecord,
    OperationRecord,
    RepositoryError,
)
from app.database.states import ChunkState, FileStatus, OperationStatus, OperationType

_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def _file(r: sqlite3.Row) -> FileRecord:
    return FileRecord(r["file_id"], r["filename"], r["original_size"], r["sha256"],
                      FileStatus(r["status"]), r["created_at"])


def _chunk(r: sqlite3.Row) -> ChunkRecord:
    return ChunkRecord(r["hash"], r["size_bytes"], r["ref_count"], ChunkState(r["state"]), r["created_at"])


def _op(r: sqlite3.Row) -> OperationRecord:
    return OperationRecord(r["operation_id"], r["file_id"], OperationType(r["operation_type"]),
                           OperationStatus(r["status"]), r["started_at"], r["completed_at"], r["error_message"])


class SqliteMetadataRepository:
    # -- files ---------------------------------------------------------------

    def create_file(self, conn, file_id, filename, original_size) -> FileRecord:
        conn.execute(
            "INSERT INTO files(file_id, filename, original_size, status) VALUES (?,?,?,?)",
            (file_id, filename, original_size, FileStatus.UPLOADING.value),
        )
        return self.get_file(conn, file_id)  # type: ignore[return-value]

    def set_file_status(self, conn, file_id, status: FileStatus, sha256: str | None = None) -> None:
        if sha256 is None:
            cur = conn.execute("UPDATE files SET status=? WHERE file_id=?", (status.value, file_id))
        else:
            cur = conn.execute("UPDATE files SET status=?, sha256=? WHERE file_id=?",
                               (status.value, sha256, file_id))
        if cur.rowcount == 0:
            raise FileNotFoundInMetadata("file not found")

    def get_file(self, conn, file_id) -> FileRecord | None:
        r = conn.execute("SELECT * FROM files WHERE file_id=?", (file_id,)).fetchone()
        return _file(r) if r else None

    def list_files(self, conn, limit, offset) -> list[FileRecord]:
        rows = conn.execute("SELECT * FROM files ORDER BY created_at DESC, file_id LIMIT ? OFFSET ?",
                            (limit, offset)).fetchall()
        return [_file(r) for r in rows]

    def count_files(self, conn) -> int:
        return conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def list_stuck_files(self, conn) -> list[FileRecord]:
        rows = conn.execute("SELECT * FROM files WHERE status='uploading' ORDER BY created_at").fetchall()
        return [_file(r) for r in rows]

    def delete_file(self, conn, file_id) -> list[str]:
        hashes = [r[0] for r in conn.execute(
            "SELECT DISTINCT chunk_hash FROM file_chunks WHERE file_id=?", (file_id,))]
        cur = conn.execute("DELETE FROM files WHERE file_id=?", (file_id,))
        if cur.rowcount == 0:
            raise FileNotFoundInMetadata("file not found")
        if not hashes:
            return []
        marks = ",".join("?" * len(hashes))
        return [r[0] for r in conn.execute(
            f"SELECT hash FROM chunks WHERE ref_count=0 AND hash IN ({marks}) ORDER BY hash", hashes)]

    # -- chunks --------------------------------------------------------------

    def get_chunk(self, conn, chunk_hash) -> ChunkRecord | None:
        r = conn.execute("SELECT * FROM chunks WHERE hash=?", (chunk_hash,)).fetchone()
        return _chunk(r) if r else None

    def register_chunk(self, conn, chunk_hash, size_bytes, state=ChunkState.PENDING):
        existing = self.get_chunk(conn, chunk_hash)
        if existing:
            if existing.size_bytes != size_bytes:
                raise ChunkSizeConflict("chunk hash already registered with a different size")
            return existing, False
        conn.execute("INSERT INTO chunks(hash, size_bytes, state) VALUES (?,?,?)",
                     (chunk_hash, size_bytes, state.value))
        return self.get_chunk(conn, chunk_hash), True

    def mark_chunk_state(self, conn, chunk_hash, state: ChunkState) -> None:
        conn.execute("UPDATE chunks SET state=? WHERE hash=?", (state.value, chunk_hash))

    def list_chunks(self, conn, limit, offset, state: ChunkState | None = None) -> list[ChunkRecord]:
        if state is None:
            rows = conn.execute("SELECT * FROM chunks ORDER BY hash LIMIT ? OFFSET ?", (limit, offset))
        else:
            rows = conn.execute("SELECT * FROM chunks WHERE state=? ORDER BY hash LIMIT ? OFFSET ?",
                                (state.value, limit, offset))
        return [_chunk(r) for r in rows.fetchall()]

    def count_chunks(self, conn, state: ChunkState | None = None) -> int:
        if state is None:
            return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM chunks WHERE state=?", (state.value,)).fetchone()[0]

    def unreferenced_chunks(self, conn) -> list[ChunkRecord]:
        return [_chunk(r) for r in conn.execute("SELECT * FROM chunks WHERE ref_count=0 ORDER BY hash")]

    def delete_unreferenced_chunk_rows(self, conn, hashes) -> list[str]:
        deleted = []
        for h in hashes:  # ref_count = 0 re-checked in SQL; the RESTRICT FK is a second guard
            cur = conn.execute("DELETE FROM chunks WHERE hash=? AND ref_count=0", (h,))
            if cur.rowcount:
                deleted.append(h)
        return deleted

    def chunks_referenced_by_file(self, conn, chunk_hash) -> list[str]:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT file_id FROM file_chunks WHERE chunk_hash=? ORDER BY file_id", (chunk_hash,))]

    # -- manifest ------------------------------------------------------------

    def append_file_chunk(self, conn, file_id, chunk_index, chunk_hash, chunk_size) -> None:
        conn.execute(
            "INSERT INTO file_chunks(file_id, chunk_index, chunk_hash, chunk_size) VALUES (?,?,?,?)",
            (file_id, chunk_index, chunk_hash, chunk_size),
        )

    def delete_chunk_row(self, conn, chunk_hash) -> None:
        conn.execute("DELETE FROM chunks WHERE hash=?", (chunk_hash,))

    def get_file_chunks(self, conn, file_id) -> list[FileChunkRecord]:
        rows = conn.execute(
            "SELECT * FROM file_chunks WHERE file_id=? ORDER BY chunk_index", (file_id,))
        return [FileChunkRecord(r["file_id"], r["chunk_index"], r["chunk_hash"], r["chunk_size"]) for r in rows]

    # -- operations ----------------------------------------------------------

    def start_operation(self, conn, operation_type: OperationType, file_id: str | None) -> int:
        cur = conn.execute("INSERT INTO operations(file_id, operation_type) VALUES (?,?)",
                           (file_id, operation_type.value))
        return cur.lastrowid

    def finish_operation(self, conn, operation_id, status: OperationStatus, error_message=None) -> None:
        if status is OperationStatus.RUNNING:
            raise RepositoryError("cannot finish an operation with status 'running'")
        if status is OperationStatus.FAILED and not error_message:
            error_message = "failed"
        conn.execute(
            f"UPDATE operations SET status=?, error_message=?, completed_at={_NOW} WHERE operation_id=?",
            (status.value, error_message, operation_id),
        )

    def list_running_operations(self, conn) -> list[OperationRecord]:
        return [_op(r) for r in conn.execute(
            "SELECT * FROM operations WHERE status='running' ORDER BY operation_id")]

    def ref_count_mismatches(self, conn) -> list[tuple[str, int, int]]:
        return [(r[0], r[1], r[2]) for r in conn.execute("SELECT * FROM v_ref_count_mismatch")]
