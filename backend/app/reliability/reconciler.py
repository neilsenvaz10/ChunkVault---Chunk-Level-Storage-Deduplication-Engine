from __future__ import annotations

import time

from app.core.config import Settings
from app.core.locking import WriterLock
from app.database.connection import connect, transaction
from app.database.repository import MetadataRepository
from app.database.states import FileStatus, OperationStatus, OperationType
from app.reliability.auditor import Auditor, parse_ts
from app.reliability.interfaces import RepairAction, ReconciliationReport
from app.storage.filesystem import FilesystemChunkStore
from app.storage.interfaces import ChunkStoreError


class ReconcilerImpl:
    def __init__(self, settings: Settings, repo: MetadataRepository, store: FilesystemChunkStore,
                 auditor: Auditor, lock: WriterLock) -> None:
        self._s, self._repo, self._store, self._auditor, self._lock = settings, repo, store, auditor, lock

    def reconcile(self) -> ReconciliationReport:
        actions: list[RepairAction] = []
        with self._lock.acquire():  # no upload/delete can be in flight while we hold this
            now = time.time()
            stale = self._s.staging_stale_seconds
            # 1. leftover staging files. Restore temp files are written without the
            #    writer lock, so those are only removed once stale.
            for p in self._store.iter_staged():
                try:
                    in_flight_restore = p.name.startswith("restore-") and now - p.stat().st_mtime < stale
                    if in_flight_restore:
                        continue
                    p.unlink()
                    actions.append(RepairAction("delete_staging_file", p.name, "removed leftover staging file"))
                except OSError as exc:
                    actions.append(RepairAction("delete_staging_file_failed", p.name, str(exc.strerror or exc)))
            conn = connect(self._s.db_path)
            try:
                with transaction(conn):
                    # 2. operations stuck in 'running' (restores only once stale)
                    for op in self._repo.list_running_operations(conn):
                        if op.operation_type is OperationType.RESTORE and now - parse_ts(op.started_at) < stale:
                            continue
                        self._repo.finish_operation(conn, op.operation_id, OperationStatus.ABORTED,
                                                    "interrupted; aborted by reconciliation")
                        actions.append(RepairAction("abort_operation", str(op.operation_id),
                                                    f"{op.operation_type.value} operation marked aborted"))
                    # 3. files stuck in 'uploading' can never complete (we hold the writer lock)
                    for f in self._repo.list_stuck_files(conn):
                        self._repo.set_file_status(conn, f.file_id, FileStatus.FAILED)
                        actions.append(RepairAction("fail_incomplete_file", f.file_id,
                                                    "file stuck in 'uploading' marked failed"))
                    # 4. metadata of unreferenced chunks (ref_count 0)
                    candidates = [c.hash for c in self._repo.unreferenced_chunks(conn)]
                    removed_rows = self._repo.delete_unreferenced_chunk_rows(conn, candidates)
                    row_hashes = {c.hash for c in self._repo.list_chunks(conn, -1, 0)}
                for h in removed_rows:
                    actions.append(RepairAction("delete_unreferenced_chunk_row", h, "ref_count was 0"))
                # 5. bytes: after the metadata commit, delete chunk files nobody references
                for h in sorted(set(removed_rows) | {x for x in self._store.iter_hashes() if x not in row_hashes}):
                    try:
                        if self._store.delete(h):
                            actions.append(RepairAction("delete_orphan_chunk", h, "no metadata row references it"))
                    except ChunkStoreError as exc:
                        actions.append(RepairAction("delete_orphan_chunk_failed", h, exc.message))
            finally:
                conn.close()
        return ReconciliationReport(actions=actions, remaining=self._auditor.verify_store())
