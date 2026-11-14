"""Explicit, authenticated garbage collection of unreferenced chunks (cloud backend).

Why it exists: the cloud backend never deletes chunk objects as a side effect of a delete or a failed upload (a shared chunk must
never be removed on a guess). A chunk nobody references therefore stays as a metadata row with ``ref_count = 0``, still counted
by the storage quota, until an administrator runs this.

PROTOCOL (safe even if the writer lock is lost; each step is durable in the database):

1. CLAIM  (one short transaction): ``UPDATE chunks SET state = 'deleting'`` for rows that are unreferenced (ref_count 0 AND no
   manifest row) and older than the grace window; rows already 'deleting' from an interrupted run are claimed again first. The
   guard is evaluated again after any row-lock wait, so a chunk that gained a reference is skipped.
2. DELETE the claimed objects from Storage (remote, outside any transaction, in batches; the writer lock is re-verified before every
   batch). A failed delete simply leaves the row 'deleting'.
3. RELEASE (one short transaction per batch): delete the 'deleting' rows whose object is gone (the DELETE re-checks that they are
   still unreferenced).

Why no upload can be hurt: a row is 'deleting' from step 1 until its object is gone. Uploads refuse 'deleting' rows and, in the
transaction that writes a manifest, take a FOR SHARE row lock on every chunk row they reference and abort if any is not
'pending'/'stored'. So either the manifest committed first (the claim then sees a reference and skips the row) or the claim
committed first (the upload aborts); an object of a row that is 'deleting' is never referenced by any committed file. This holds
whatever the writer lock does; the lock only serialises the normal case.

Other rules (each tested): never automatic (no worker, no start-up hook; only ``POST /api/admin/gc`` with the admin token);
``dry_run`` is the default and changes nothing; bounded by ``limit``; a delete failure keeps the row 'deleting' (still counted by
the quota, retried next run); recorded as a ``reconciliation`` operation; idempotent and restart-safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings
from app.database.interfaces import Database
from app.database.repository import MetadataRepository
from app.database.states import OperationStatus, OperationType
from app.storage.interfaces import ChunkStoreError

BATCH = 50
MAX_LIMIT = 1000


@dataclass(frozen=True)
class GcReport:
    dry_run: bool
    grace_seconds: float
    candidates: int  # chunk rows that qualify (bounded by ``limit``)
    candidate_bytes: int
    rows_removed: int
    objects_removed: int
    objects_already_absent: int
    bytes_reclaimed: int
    failed: list[str] = field(default_factory=list)  # hashes whose object could not be deleted (re-registered for the next run)
    more_available: bool = False  # True if the limit cut the candidate list short


class GarbageCollector:
    def __init__(self, settings: Settings, db: Database, repo: MetadataRepository, store, lock) -> None:
        self._s, self._db, self._repo, self._store, self._lock = settings, db, repo, store, lock
        self._grace = settings.cloud.orphan_min_age_seconds if settings.cloud else 0.0

    def run(self, dry_run: bool = True, limit: int = 200) -> GcReport:
        """Every database step uses its own short connection: none is held while objects are being deleted from Storage."""
        limit = max(1, min(int(limit), MAX_LIMIT))
        with self._lock.acquire():
            if dry_run:
                with self._db.connection() as conn:
                    found = self._repo.gc_candidates(conn, self._grace, limit + 1)
                cands = found[:limit]
                return GcReport(True, self._grace, len(cands), sum(c.size_bytes for c in cands), 0, 0, 0, 0, [], len(found) > limit)
            self._lock.ensure_held()
            with self._db.connection() as conn, self._db.transaction(conn):
                op_id = self._repo.start_operation(conn, OperationType.RECONCILIATION, None)
            try:
                with self._db.connection() as conn, self._db.transaction(conn):  # 1. CLAIM
                    claimed = self._repo.claim_chunks_for_deletion(conn, self._grace, limit)
                sizes = {c.hash: c.size_bytes for c in claimed}
                rows_removed = objects_removed = absent = reclaimed = 0
                failed: list[str] = []
                order = [c.hash for c in claimed]
                for i in range(0, len(order), BATCH):
                    self._lock.ensure_held()  # a lost lock stops the run between batches (the claim state keeps it safe anyway)
                    batch = order[i:i + BATCH]
                    gone: list[str] = []
                    for h in batch:  # 2. DELETE the objects (no database connection held)
                        if getattr(self._store, "delete_in_flight", lambda _h: False)(h):
                            failed.append(h)  # an earlier, abandoned delete may still land: keep the row 'deleting' until it is over
                            continue
                        try:
                            if self._store.delete(h):
                                objects_removed += 1
                            else:
                                absent += 1
                            gone.append(h)
                        except ChunkStoreError:
                            failed.append(h)  # the row stays 'deleting': counted, retried by the next run
                    with self._db.connection() as conn, self._db.transaction(conn):  # 3. RELEASE the rows whose object is gone
                        released = self._repo.release_deleted_chunks(conn, gone)
                    rows_removed += len(released)
                    reclaimed += sum(sizes[h] for h in released)
                with self._db.connection() as conn:
                    more = len(claimed) >= limit and bool(self._repo.gc_candidates(conn, self._grace, 1))
            except BaseException as exc:
                self._finish(op_id, OperationStatus.FAILED, f"garbage collection interrupted: {type(exc).__name__}")
                raise
            self._finish(op_id, OperationStatus.SUCCEEDED, None)
            return GcReport(False, self._grace, len(claimed), sum(sizes.values()), rows_removed, objects_removed, absent,
                            reclaimed, failed, more)

    def _finish(self, op_id, status, message) -> None:
        try:
            with self._db.connection() as conn, self._db.transaction(conn):
                self._repo.finish_operation(conn, op_id, status, message)
        except Exception:  # noqa: BLE001 - bookkeeping must not mask the real outcome
            pass
