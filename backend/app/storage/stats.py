"""Storage statistics computed from real metadata rows and the real chunk store."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.database.factory import build_database, build_repository
from app.database.interfaces import Database
from app.database.repository import MetadataRepository
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


def compute_stats(settings: Settings, store: FilesystemChunkStore, db: Database | None = None,
                  repo: MetadataRepository | None = None) -> StorageStats:
    """``db``/``repo`` default to the ones the configured backend builds (callers inside the app pass their own)."""
    db = db or build_database(settings)
    repo = repo or build_repository(settings)
    with db.connection() as conn, db.snapshot(conn):  # one consistent read of all the aggregates
        t = repo.storage_totals(conn)
    files, logical, refs = t.files, t.logical_bytes, t.references
    uniq, uniq_bytes = t.unique_chunks, t.unique_chunk_bytes
    unref, unref_bytes = t.unreferenced_chunks, t.unreferenced_chunk_bytes
    db_bytes = db.footprint_bytes()
    if getattr(store, "remote", False):
        # Remote store: never list the whole bucket on a request. 'Stored' chunk rows are the recorded inventory; the explicit
        # audit compares it with the object store.
        chunk_files, chunk_bytes = t.stored_chunks, t.stored_chunk_bytes
    else:
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
