"""Reference-counted file deletion and chunk reclamation.

Metadata first, bytes second:
  T: delete the file row (the FK cascade removes its manifest rows and the Phase 1
     triggers lower ref_counts), delete chunk rows that reached ref_count 0, and
     record the operation -- one transaction.
  Then, after COMMIT, unlink the now-unreferenced chunk files.
A crash between the two leaves only orphan chunk files (valid bytes nobody
references): harmless, found by the audit, removed by reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.locking import WriterLock
from app.database.interfaces import Database
from app.database.repository import FileNotFoundInMetadata, MetadataRepository
from app.database.states import OperationStatus, OperationType
from app.storage.filesystem import FilesystemChunkStore
from app.storage.interfaces import ChunkStoreError


@dataclass(frozen=True)
class DeleteResult:
    file_id: str
    chunks_released: int  # manifest rows removed (each lowered a ref_count by 1)
    chunks_reclaimed: int  # chunk files physically deleted
    bytes_reclaimed: int
    cleanup_failed: int  # unreferenced chunk files that could not be removed (left as orphans)
    chunks_pending_gc: int = 0  # cloud: unreferenced chunks left for explicit garbage collection


class FileDeleter:
    def __init__(self, settings: Settings, db: Database, repo: MetadataRepository, store: FilesystemChunkStore,
                 lock: WriterLock) -> None:
        self._s, self._db, self._repo, self._store, self._lock = settings, db, repo, store, lock

    def delete(self, file_id: str) -> DeleteResult:
        """Raises ``FileNotFoundInMetadata`` (404) if absent -- so a repeated
        delete is a clean, predictable 404 and changes nothing."""
        with self._lock.acquire():
            with self._db.connection() as conn:
                with self._db.transaction(conn):
                    if self._repo.get_file(conn, file_id) is None:
                        raise FileNotFoundInMetadata("file not found")
                    released = len(self._repo.get_file_chunks(conn, file_id))
                    op_id = self._repo.start_operation(conn, OperationType.DELETE, file_id)
                    zero = self._repo.delete_file(conn, file_id)
                    if getattr(self._store, "remote", False):
                        # Cloud: metadata only. The now-unreferenced chunk rows (and their objects) are kept until an administrator
                        # runs garbage collection; they still count toward the quota. A shared chunk is never touched here.
                        sizes, removed = {}, []
                    else:
                        sizes = {h: self._repo.get_chunk(conn, h).size_bytes for h in zero}
                        removed = self._repo.delete_unreferenced_chunk_rows(conn, zero)
                    self._repo.finish_operation(conn, op_id, OperationStatus.SUCCEEDED)
                reclaimed = failed = reclaimed_bytes = 0
                for h in removed:  # committed: now safe to remove bytes
                    try:
                        self._store.delete(h)
                        reclaimed += 1
                        reclaimed_bytes += sizes[h]
                    except ChunkStoreError:
                        failed += 1
                pending = len(zero) if getattr(self._store, "remote", False) else 0
                return DeleteResult(file_id, released, reclaimed, reclaimed_bytes, failed, pending)
