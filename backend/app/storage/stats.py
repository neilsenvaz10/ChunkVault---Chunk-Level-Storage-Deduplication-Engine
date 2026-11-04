"""Storage statistics computed from real SQLite rows and the real filesystem."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.database.connection import connect
from app.storage.filesystem import FilesystemChunkStore


@dataclass(frozen=True)
class StorageStats:
    total_files: int  # status 'complete'
    logical_bytes: int
    unique_chunks: int  # distinct chunks referenced by complete files
    unique_chunk_bytes: int
    total_chunk_references: int  # manifest rows of complete files
    reused_chunk_references: int  # references - unique_chunks
    unreferenced_chunks: int  # rows with ref_count 0 (awaiting reclamation)
    unreferenced_chunk_bytes: int
    storage_savings_percent: float | None  # None when logical_bytes == 0
    deduplication_ratio: float | None  # None when unique_chunk_bytes == 0
    database_bytes: int
    chunk_files: int
    chunk_store_bytes: int  # sum of file sizes; excludes filesystem block overhead
    staging_files: int
    staging_bytes: int


def compute_stats(settings: Settings, store: FilesystemChunkStore) -> StorageStats:
    conn = connect(settings.db_path)
    try:
        conn.execute("BEGIN")
        files, logical = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(original_size),0) FROM files WHERE status='complete'").fetchone()
        refs = conn.execute(
            "SELECT COUNT(*) FROM file_chunks fc JOIN files f USING(file_id) WHERE f.status='complete'"
        ).fetchone()[0]
        uniq, uniq_bytes = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM chunks WHERE hash IN "
            "(SELECT fc.chunk_hash FROM file_chunks fc JOIN files f USING(file_id) WHERE f.status='complete')"
        ).fetchone()
        unref, unref_bytes = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM chunks WHERE ref_count=0").fetchone()
        conn.execute("COMMIT")
    finally:
        conn.close()

    db_bytes = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            db_bytes += Path(str(settings.db_path) + suffix).stat().st_size
        except OSError:
            pass
    chunk_files, chunk_bytes = store.disk_usage()
    staging_files = staging_bytes = 0
    for p in store.iter_staged():
        try:
            staging_bytes += p.stat().st_size
            staging_files += 1
        except OSError:
            pass
    return StorageStats(
        total_files=files, logical_bytes=logical, unique_chunks=uniq, unique_chunk_bytes=uniq_bytes,
        total_chunk_references=refs, reused_chunk_references=refs - uniq,
        unreferenced_chunks=unref, unreferenced_chunk_bytes=unref_bytes,
        storage_savings_percent=(100.0 * (logical - uniq_bytes) / logical) if logical else None,
        deduplication_ratio=(logical / uniq_bytes) if uniq_bytes else None,
        database_bytes=db_bytes, chunk_files=chunk_files, chunk_store_bytes=chunk_bytes,
        staging_files=staging_files, staging_bytes=staging_bytes,
    )
