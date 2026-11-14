"""PostgreSQL implementation of ``MetadataRepository`` (psycopg 3, plain SQL, no ORM).

Same contract as the SQLite implementation: it never begins, commits or rolls back (callers own boundaries through
``Database.transaction``) and it never touches ``ref_count`` (the triggers of migration 0001 are the only mechanism that
maintains it). Timestamps are returned as UTC ISO-8601 strings in the same shape SQLite produced, so API responses do not
change. Ordering uses ``COLLATE "C"`` wherever SQLite ordered by binary string comparison.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database.repository import (
    ChunkRecord,
    ChunkReclaiming,
    ChunkReference,
    ChunkSizeConflict,
    FileChunkRecord,
    FileNotFoundInMetadata,
    FileRecord,
    INSPECTABLE_TABLES,
    OperationRecord,
    QuotaConflict,
    RepositoryError,
    StorageTotals,
)
from app.database.states import ChunkState, FileStatus, OperationStatus, OperationType

# Never trust the counter alone: a chunk is only reclaimable if NO manifest row uses it.
_NO_MANIFEST_ROWS = "NOT EXISTS (SELECT 1 FROM file_chunks fc WHERE fc.chunk_hash = chunks.hash)"
_TABLE_ORDER = {
    "files": 'created_at DESC, file_id COLLATE "C"', "chunks": 'hash COLLATE "C"',
    "file_chunks": 'file_id COLLATE "C", chunk_index', "operations": "operation_id DESC",
}


def ts(value: Any) -> Any:
    """datetime -> 'YYYY-MM-DDTHH:MM:SS.mmmZ' (UTC), the format the SQLite backend stores; anything else unchanged."""
    if isinstance(value, datetime):
        v = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return v.strftime("%Y-%m-%dT%H:%M:%S.") + f"{v.microsecond // 1000:03d}Z"
    return value


def _file(r) -> FileRecord:
    return FileRecord(r["file_id"], r["filename"], r["original_size"], r["sha256"], FileStatus(r["status"]), ts(r["created_at"]))


def _chunk(r) -> ChunkRecord:
    return ChunkRecord(r["hash"], r["size_bytes"], r["ref_count"], ChunkState(r["state"]), ts(r["created_at"]))


def _op(r) -> OperationRecord:
    return OperationRecord(r["operation_id"], r["file_id"], OperationType(r["operation_type"]), OperationStatus(r["status"]),
                           ts(r["started_at"]), ts(r["completed_at"]), r["error_message"])


class PostgresMetadataRepository:
    # -- files ---------------------------------------------------------------

    def create_file(self, conn, file_id, filename, original_size) -> FileRecord:
        conn.execute("INSERT INTO files(file_id, filename, original_size, status) VALUES (%s,%s,%s,%s)",
                     (file_id, filename, original_size, FileStatus.UPLOADING.value))
        return self.get_file(conn, file_id)  # type: ignore[return-value]

    def set_file_status(self, conn, file_id, status: FileStatus, sha256: str | None = None) -> None:
        if sha256 is None:
            cur = conn.execute("UPDATE files SET status=%s WHERE file_id=%s", (status.value, file_id))
        else:
            cur = conn.execute("UPDATE files SET status=%s, sha256=%s WHERE file_id=%s", (status.value, sha256, file_id))
        if cur.rowcount == 0:
            raise FileNotFoundInMetadata("file not found")

    def get_file(self, conn, file_id) -> FileRecord | None:
        r = conn.execute("SELECT * FROM files WHERE file_id=%s", (file_id,)).fetchone()
        return _file(r) if r else None

    def list_files(self, conn, limit, offset) -> list[FileRecord]:
        rows = conn.execute('SELECT * FROM files ORDER BY created_at DESC, file_id COLLATE "C" LIMIT %s OFFSET %s',
                            (None if limit < 0 else limit, offset)).fetchall()
        return [_file(r) for r in rows]

    def count_files(self, conn) -> int:
        return conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def list_stuck_files(self, conn) -> list[FileRecord]:
        rows = conn.execute("SELECT * FROM files WHERE status='uploading' ORDER BY created_at, file_id COLLATE \"C\"").fetchall()
        return [_file(r) for r in rows]

    def delete_file(self, conn, file_id) -> list[str]:
        hashes = [r[0] for r in conn.execute("SELECT DISTINCT chunk_hash FROM file_chunks WHERE file_id=%s", (file_id,))]
        cur = conn.execute("DELETE FROM files WHERE file_id=%s", (file_id,))
        if cur.rowcount == 0:
            raise FileNotFoundInMetadata("file not found")
        if not hashes:
            return []
        return [r[0] for r in conn.execute(
            f'SELECT hash FROM chunks WHERE ref_count=0 AND hash = ANY(%s) AND {_NO_MANIFEST_ROWS} ORDER BY hash COLLATE "C"',
            (hashes,))]

    # -- chunks --------------------------------------------------------------

    def get_chunk(self, conn, chunk_hash) -> ChunkRecord | None:
        r = conn.execute("SELECT * FROM chunks WHERE hash=%s", (chunk_hash,)).fetchone()
        return _chunk(r) if r else None

    def register_chunk(self, conn, chunk_hash, size_bytes, state=ChunkState.PENDING):
        cur = conn.execute("INSERT INTO chunks(hash, size_bytes, state) VALUES (%s,%s,%s) ON CONFLICT (hash) DO NOTHING",
                           (chunk_hash, size_bytes, state.value))
        created = cur.rowcount == 1
        record = self.get_chunk(conn, chunk_hash)
        if record is None:  # cannot happen inside the caller's transaction; never guess
            raise RepositoryError("chunk row vanished while it was being registered")
        if not created and record.size_bytes != size_bytes:
            raise ChunkSizeConflict("chunk hash already registered with a different size")
        return record, created

    def mark_chunk_state(self, conn, chunk_hash, state: ChunkState) -> None:
        conn.execute("UPDATE chunks SET state=%s WHERE hash=%s", (state.value, chunk_hash))

    def list_chunks(self, conn, limit, offset, state: ChunkState | None = None) -> list[ChunkRecord]:
        lim = None if limit < 0 else limit
        if state is None:
            rows = conn.execute('SELECT * FROM chunks ORDER BY hash COLLATE "C" LIMIT %s OFFSET %s', (lim, offset))
        else:
            rows = conn.execute('SELECT * FROM chunks WHERE state=%s ORDER BY hash COLLATE "C" LIMIT %s OFFSET %s',
                                (state.value, lim, offset))
        return [_chunk(r) for r in rows.fetchall()]

    def count_chunks(self, conn, state: ChunkState | None = None) -> int:
        if state is None:
            return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM chunks WHERE state=%s", (state.value,)).fetchone()[0]

    def unreferenced_chunks(self, conn) -> list[ChunkRecord]:
        return [_chunk(r) for r in conn.execute(
            f'SELECT * FROM chunks WHERE ref_count=0 AND {_NO_MANIFEST_ROWS} ORDER BY hash COLLATE "C"')]

    def delete_unreferenced_chunk_rows(self, conn, hashes) -> list[str]:
        if not hashes:
            return []
        # counter AND manifest are re-checked inside the DELETE; the RESTRICT foreign key is a third guard.
        deleted = {r[0] for r in conn.execute(
            f"DELETE FROM chunks WHERE hash = ANY(%s) AND ref_count=0 AND {_NO_MANIFEST_ROWS} RETURNING hash", (list(hashes),))}
        return [h for h in hashes if h in deleted]  # input order, like the SQLite implementation

    def chunks_referenced_by_file(self, conn, chunk_hash) -> list[str]:
        return [r[0] for r in conn.execute(
            'SELECT file_id FROM (SELECT DISTINCT file_id FROM file_chunks WHERE chunk_hash=%s) d ORDER BY file_id COLLATE "C"',
            (chunk_hash,))]

    # -- manifest ------------------------------------------------------------

    def append_file_chunk(self, conn, file_id, chunk_index, chunk_hash, chunk_size) -> None:
        conn.execute("INSERT INTO file_chunks(file_id, chunk_index, chunk_hash, chunk_size) VALUES (%s,%s,%s,%s)",
                     (file_id, chunk_index, chunk_hash, chunk_size))

    def delete_chunk_row(self, conn, chunk_hash) -> None:
        conn.execute("DELETE FROM chunks WHERE hash=%s", (chunk_hash,))

    def get_file_chunks(self, conn, file_id) -> list[FileChunkRecord]:
        rows = conn.execute("SELECT * FROM file_chunks WHERE file_id=%s ORDER BY chunk_index", (file_id,))
        return [FileChunkRecord(r["file_id"], r["chunk_index"], r["chunk_hash"], r["chunk_size"]) for r in rows]

    # -- operations ----------------------------------------------------------

    def start_operation(self, conn, operation_type: OperationType, file_id: str | None) -> int:
        return conn.execute("INSERT INTO operations(file_id, operation_type) VALUES (%s,%s) RETURNING operation_id",
                            (file_id, operation_type.value)).fetchone()[0]

    def finish_operation(self, conn, operation_id, status: OperationStatus, error_message=None) -> None:
        if status is OperationStatus.RUNNING:
            raise RepositoryError("cannot finish an operation with status 'running'")
        if status is OperationStatus.FAILED and not error_message:
            error_message = "failed"
        conn.execute("UPDATE operations SET status=%s, error_message=%s, completed_at=now() WHERE operation_id=%s",
                     (status.value, error_message, operation_id))

    def list_running_operations(self, conn) -> list[OperationRecord]:
        return [_op(r) for r in conn.execute("SELECT * FROM operations WHERE status='running' ORDER BY operation_id")]

    def ref_count_mismatches(self, conn) -> list[tuple[str, int, int]]:
        return [(r[0], r[1], r[2]) for r in conn.execute(
            'SELECT hash, recorded_ref_count, actual_ref_count FROM v_ref_count_mismatch ORDER BY hash COLLATE "C"')]

    # -- read models for the API and statistics -------------------------------------------------------------

    def file_chunk_counts(self, conn, file_ids) -> dict[str, int]:
        if not file_ids:
            return {}
        return {r[0]: r[1] for r in conn.execute(
            "SELECT file_id, COUNT(*) FROM file_chunks WHERE file_id = ANY(%s) GROUP BY file_id", (list(file_ids),))}

    def count_manifest_references(self, conn, chunk_hash) -> int:
        return conn.execute("SELECT COUNT(*) FROM file_chunks WHERE chunk_hash=%s", (chunk_hash,)).fetchone()[0]

    def list_manifest_references(self, conn, chunk_hash, limit) -> list[ChunkReference]:
        return [ChunkReference(r[0], r[1], r[2], r[3]) for r in conn.execute(
            "SELECT fc.file_id, f.filename, f.status, fc.chunk_index FROM file_chunks fc "
            'JOIN files f USING(file_id) WHERE fc.chunk_hash=%s ORDER BY fc.file_id COLLATE "C", fc.chunk_index LIMIT %s',
            (chunk_hash, limit))]

    def storage_totals(self, conn) -> StorageTotals:
        files, logical = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(original_size),0)::bigint FROM files WHERE status='complete'").fetchone()
        refs = conn.execute(
            "SELECT COUNT(*) FROM file_chunks fc JOIN files f USING(file_id) WHERE f.status='complete'").fetchone()[0]
        uniq, uniq_bytes = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes),0)::bigint FROM chunks WHERE hash IN "
            "(SELECT fc.chunk_hash FROM file_chunks fc JOIN files f USING(file_id) WHERE f.status='complete')").fetchone()
        unref, unref_bytes = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes),0)::bigint FROM chunks WHERE ref_count=0").fetchone()
        stored, stored_bytes = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes),0)::bigint FROM chunks WHERE state='stored'").fetchone()
        return StorageTotals(files, logical, refs, uniq, uniq_bytes, unref, unref_bytes, stored, stored_bytes)

    def count_rows(self, conn, table) -> int:
        if table not in INSPECTABLE_TABLES:
            raise ValueError("table is not inspectable")
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # table is whitelisted above

    def list_rows(self, conn, table, limit, offset):
        if table not in INSPECTABLE_TABLES:
            raise ValueError("table is not inspectable")
        cur = conn.execute(f"SELECT * FROM {table} ORDER BY {_TABLE_ORDER[table]} LIMIT %s OFFSET %s", (limit, offset))
        return [d.name for d in cur.description], [tuple(ts(v) for v in r) for r in cur.fetchall()]

    # -- batched operations ----------------------------------------------------------------------------------

    def get_chunks(self, conn, hashes) -> dict[str, ChunkRecord]:
        unique = list(dict.fromkeys(hashes))
        if not unique:
            return {}
        return {r["hash"]: _chunk(r) for r in conn.execute("SELECT * FROM chunks WHERE hash = ANY(%s)", (unique,))}

    def register_chunks(self, conn, items, state=ChunkState.PENDING) -> list[str]:
        if not items:
            return []
        hashes = [h for h, _ in items]
        sizes = [s for _, s in items]
        created = {r[0] for r in conn.execute(
            "INSERT INTO chunks(hash, size_bytes, state) SELECT h, s, %s FROM unnest(%s::text[], %s::int[]) AS t(h, s) "
            "ON CONFLICT (hash) DO NOTHING RETURNING hash", (state.value, hashes, sizes))}
        existing = self.get_chunks(conn, hashes)
        for h, size in items:
            if existing[h].size_bytes != size:
                raise ChunkSizeConflict("chunk hash already registered with a different size")
        return [h for h in hashes if h in created]

    def mark_chunks_state(self, conn, hashes, state: ChunkState) -> None:
        if hashes:
            conn.execute("UPDATE chunks SET state=%s WHERE hash = ANY(%s)", (state.value, list(hashes)))

    def append_file_chunks(self, conn, file_id, rows) -> None:
        if not rows:
            return
        conn.execute(
            "INSERT INTO file_chunks(file_id, chunk_index, chunk_hash, chunk_size) "
            "SELECT %s, i, h, s FROM unnest(%s::int[], %s::text[], %s::int[]) AS t(i, h, s)",
            (file_id, [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]))

    def chunk_bytes_total(self, conn) -> int:
        return conn.execute("SELECT COALESCE(SUM(size_bytes),0)::bigint FROM chunks").fetchone()[0]

    def gc_candidates(self, conn, older_than_seconds, limit) -> list[ChunkRecord]:
        return [_chunk(r) for r in conn.execute(
            f"SELECT * FROM chunks WHERE ref_count=0 AND {_NO_MANIFEST_ROWS} "
            "AND (state = 'deleting' OR created_at <= now() - make_interval(secs => %s)) "
            'ORDER BY (state = \'deleting\') DESC, created_at, hash COLLATE "C" LIMIT %s',
            (float(older_than_seconds), limit))]

    # -- cloud coordination ------------------------------------------------------------------------------------------

    def reserve_chunks(self, conn, items, cap_bytes) -> list[str]:
        if not items:
            return []
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('chunkvault.quota:' || current_schema(), 0))")
        hashes = [h for h, _ in items]
        existing = self.get_chunks(conn, hashes)  # read AFTER the lock: sees everything an earlier reservation committed
        for h, size in items:
            row = existing.get(h)
            if row is not None and row.size_bytes != size:
                raise ChunkSizeConflict("chunk hash already registered with a different size")
            if row is not None and row.state is ChunkState.DELETING:
                raise ChunkReclaiming(f"chunk {h[:12]}… is being reclaimed by garbage collection; finish it, then retry")
        new = [(h, s) for h, s in items if h not in existing]
        needed = sum(s for _, s in new)
        if not new:
            return []
        used = self.chunk_bytes_total(conn)
        if used + needed > cap_bytes:
            raise QuotaConflict(used, needed)
        return self.register_chunks(conn, new, ChunkState.PENDING)

    def lock_chunks_for_reference(self, conn, hashes) -> list[str]:
        if not hashes:
            return []
        # FOR NO KEY UPDATE (not FOR SHARE): the transaction goes on to UPDATE these rows (mark them stored), and two transactions that
        # each hold a share lock and then wait to upgrade it deadlock. Rows are locked in hash order everywhere (see the claim), so
        # transactions cannot lock in opposite orders. It does not conflict with the key-share locks that foreign-key checks take.
        return [r[0] for r in conn.execute(
            "SELECT hash FROM chunks WHERE hash = ANY(%s) AND state IN ('pending', 'stored') "
            'ORDER BY hash COLLATE "C" FOR NO KEY UPDATE', (list(hashes),))]

    def claim_chunks_for_deletion(self, conn, older_than_seconds, limit) -> list[ChunkRecord]:
        picked = [r[0] for r in conn.execute(
            f"SELECT hash FROM chunks WHERE ref_count = 0 AND {_NO_MANIFEST_ROWS} "
            "AND (state = 'deleting' OR created_at <= now() - make_interval(secs => %s)) "
            'ORDER BY (state = \'deleting\') DESC, created_at, hash COLLATE "C" LIMIT %s',
            (float(older_than_seconds), limit))]
        if not picked:
            return []
        # Lock the picked rows in HASH order (the order every other transaction uses) and re-check the guard on the locked,
        # latest version of each row: a chunk that gained a reference while we waited is skipped.
        return [_chunk(r) for r in conn.execute(
            "UPDATE chunks SET state = 'deleting' WHERE hash IN ("
            "  SELECT c.hash FROM chunks c WHERE c.hash = ANY(%s) AND c.ref_count = 0 "
            "  AND NOT EXISTS (SELECT 1 FROM file_chunks fc WHERE fc.chunk_hash = c.hash) "
            '  ORDER BY c.hash COLLATE "C" FOR UPDATE) '
            f"AND ref_count = 0 AND {_NO_MANIFEST_ROWS} RETURNING *", (picked,))]

    def release_deleted_chunks(self, conn, hashes) -> list[str]:
        if not hashes:
            return []
        return [r[0] for r in conn.execute(
            f"DELETE FROM chunks WHERE hash = ANY(%s) AND state = 'deleting' AND ref_count = 0 AND {_NO_MANIFEST_ROWS} RETURNING hash",
            (list(hashes),))]

    def complete_file(self, conn, file_id, sha256) -> bool:
        return conn.execute("UPDATE files SET status = 'complete', sha256 = %s WHERE file_id = %s AND status = 'uploading'",
                            (sha256, file_id)).rowcount == 1

    def claim_pending_chunks(self, conn, hashes) -> list[ChunkRecord]:
        if not hashes:
            return []
        return [_chunk(r) for r in conn.execute(
            "UPDATE chunks SET state = 'deleting' WHERE hash IN ("
            "  SELECT c.hash FROM chunks c WHERE c.hash = ANY(%s) AND c.state = 'pending' AND c.ref_count = 0 "
            "  AND NOT EXISTS (SELECT 1 FROM file_chunks fc WHERE fc.chunk_hash = c.hash) "
            '  ORDER BY c.hash COLLATE "C" FOR UPDATE) '
            f"AND state = 'pending' AND ref_count = 0 AND {_NO_MANIFEST_ROWS} RETURNING *", (list(hashes),))]
