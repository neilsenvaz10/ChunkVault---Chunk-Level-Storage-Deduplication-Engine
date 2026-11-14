"""The cloud upload pipeline (PostgreSQL + Supabase Storage).

Phases, and what each may touch
    1. STAGE     (no lock, no database)  stream the body to local scratch: size limit, whole-file SHA-256, fsync.
    2. ANALYSE   (no lock, no database)  content-defined chunking + SHA-256 of every chunk -> a small PLAN (index, offset, size, hash).
                                          This is the CPU-heavy part (seconds on a shared 0.1 vCPU); it needs no shared state.
    3. PREFLIGHT (no lock, read-only)    advisory early refusal when the upload clearly cannot fit in the quota. The authoritative quota
                                          check happens inside the database at registration time (``reserve_chunks``).
    4. LOCK      the single-writer lock, waited for at most min(writer_lock_timeout, time left before the deadline).
    5. COMMIT    under the lock: T1 (file + operation rows), look up every chunk row at once, verify the stored duplicates with one
                 Storage listing per prefix, register and publish the new chunks in parallel batches, then T2 (manifest, chunk rows
                 'stored', file 'complete') with the row-lock fence of C4.

Only phase 5 holds the writer lock, and it contains no CPU-heavy work and no body transfer: the lock is held for database steps and
Storage I/O only.

Failure
    Every failure after T1 marks the file and operation 'failed'. The chunk rows this upload ITSELF created (still 'pending', never
    referenced) are then given back: claimed 'deleting' under the usual guards, their objects deleted, the rows released, within
    ``upload_cleanup_budget_seconds``. Rows that existed before this upload, and anything referenced by any file, are never touched.
    What does not fit in the budget (or fails because Storage is down) stays in the durable 'deleting' state: counted by the quota,
    refused by uploads, finished by the next explicit garbage collection. A crash (BaseException) is not cleaned up here; the audit finds
    the leftovers and reconciliation/GC remove them after their grace windows.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from app.core.config import CloudSettings, Settings
from app.core.diskfull import is_disk_full
from app.core.errors import ChunkVaultError, ConcurrentConflictError, is_concurrency_conflict
from app.database.interfaces import Database
from app.database.repository import ChunkReclaiming, MetadataRepository, QuotaConflict
from app.database.states import ChunkState, FileStatus, OperationStatus, OperationType
from app.deduplication.interfaces import (
    ChunkIdentityConflictError,
    ChunkOutcome,
    ChunkUnavailableError,
    InsufficientStorageError,
    QuotaExceededError,
    UploadDeadlineError,
    UploadError,
    UploadResult,
)
from app.hashing.sha256 import Sha256Service
from app.storage.filesystem import ChunkStatus

_LOOKUP_SLICE = 500


@dataclass(frozen=True)
class PlannedChunk:
    index: int
    offset: int
    size: int
    hash: str


class Deadline:
    """A budget for one request. ``check`` is called at safe points only (between phases and batches), never inside a transaction."""

    def __init__(self, seconds: float, clock=time.monotonic) -> None:
        self._seconds, self._clock = seconds, clock
        self._end = clock() + seconds

    def remaining(self) -> float:
        return self._end - self._clock()

    def check(self, stage: str) -> None:
        if self.remaining() <= 0:
            raise UploadDeadlineError(
                f"the upload did not finish within {self._seconds:g} s (stopped while {stage}); nothing was committed, retry",
                details={"stage": stage, "deadline_seconds": self._seconds})


@dataclass
class _Tracker:
    """What THIS upload created, so a failure gives back exactly that and nothing else."""

    reserved: list[str] = field(default_factory=list)  # chunk rows this upload registered (as 'pending')


class CloudUploadPipeline:
    def __init__(self, uploader, settings: Settings, db: Database, repo: MetadataRepository, store, chunker, hasher: Sha256Service,
                 lock, cloud: CloudSettings, clock=time.monotonic) -> None:
        self._u, self._s, self._db, self._repo, self._store = uploader, settings, db, repo, store
        self._chunker, self._hasher, self._lock, self._c, self._clock = chunker, hasher, lock, cloud, clock

    # ------------------------------------------------------------------------------------------------ entry point
    def upload(self, name: str, stream: BinaryIO) -> UploadResult:
        deadline = Deadline(self._c.upload_deadline_seconds, self._clock)
        file_id = uuid.uuid4().hex
        part = self._store.staging_dir / f"upload-{file_id}.part"
        try:
            size, whole_sha = self._u._stage_upload(stream, part)  # 1. STAGE (reuses the local implementation: limit, SHA-256, fsync)
            self._u.checkpoint("staged")
            deadline.check("staging the upload")
            plan = self._analyse(part)  # 2. ANALYSE
            deadline.check("chunking the upload")
            with self._db.connection() as conn:  # 3. PREFLIGHT (advisory)
                self._preflight_quota(conn, plan, size)
            wait_s = max(0.0, min(self._s.writer_lock_timeout_seconds, deadline.remaining()))
            with self._lock.acquire(timeout_seconds=wait_s):  # 4. LOCK
                deadline.check("waiting for the storage lock")
                with self._db.connection() as conn:  # 5. COMMIT
                    return self._commit(conn, file_id, name, part, size, whole_sha, plan, deadline)
        except ChunkVaultError:
            raise
        except Exception as exc:
            if is_concurrency_conflict(exc):
                raise ConcurrentConflictError("a concurrent operation conflicted with this upload; nothing was committed, retry") from exc
            raise UploadError("upload failed and was rolled back") from exc
        finally:
            part.unlink(missing_ok=True)  # the scratch file never outlives the request

    # ------------------------------------------------------------------------------------------------ phases 2-3
    def _analyse(self, part: Path) -> list[PlannedChunk]:
        plan: list[PlannedChunk] = []
        sizes: dict[str, int] = {}
        with open(part, "rb") as src:
            for chunk in self._chunker.chunk_stream(src):
                h = self._hasher.digest(chunk.data)
                if sizes.setdefault(h, chunk.size) != chunk.size:
                    raise ChunkIdentityConflictError("same digest with different sizes inside one file")
                plan.append(PlannedChunk(chunk.index, chunk.offset, chunk.size, h))
        return plan

    def _lookup(self, conn, hashes: list[str]) -> dict:
        rows: dict = {}
        for i in range(0, len(hashes), _LOOKUP_SLICE):
            rows.update(self._repo.get_chunks(conn, hashes[i:i + _LOOKUP_SLICE]))
        return rows

    def _preflight_quota(self, conn, plan: list[PlannedChunk], size: int) -> None:
        """Advisory: refuse early, before any lock or Storage traffic, an upload that cannot fit. Uses the plan, so the file is not
        chunked a second time. A stale answer is harmless: the authoritative check is ``reserve_chunks`` inside the database."""
        cap = self._c.stored_bytes_cap
        used = self._repo.chunk_bytes_total(conn)
        if used + size <= cap:
            return
        distinct = {p.hash: p.size for p in plan}
        existing = self._lookup(conn, list(distinct))
        needed = sum(s for h, s in distinct.items() if h not in existing)
        if used + needed > cap:
            raise QuotaExceededError(used, needed, cap)

    # ------------------------------------------------------------------------------------------------ phase 5
    def _commit(self, conn, file_id, name, part: Path, size, whole_sha, plan, deadline) -> UploadResult:
        tracker = _Tracker()
        op_id = None
        file_row_created = False
        try:
            with self._db.transaction(conn):  # T1
                self._repo.create_file(conn, file_id, name, size)
                file_row_created = True
                op_id = self._repo.start_operation(conn, OperationType.UPLOAD, file_id)
            self._u.checkpoint("t1_committed")
            ingest = self._ingest(conn, part, plan, deadline, tracker)
            if sum(m[2] for m in ingest.manifest) != size:
                raise UploadError("chunk sizes do not add up to the uploaded size")
            self._u.checkpoint("chunks_ready")
            deadline.check("finishing")  # the last point at which giving up is free: after T2 the file is committed
            with self._db.transaction(conn):  # T2: the only place a file becomes 'complete'
                # FENCE (independent of the writer lock): row-lock, in hash order, every chunk this manifest will reference.
                wanted = list(ingest.known)
                if set(self._repo.lock_chunks_for_reference(conn, wanted)) != set(wanted):
                    raise ChunkReclaiming("a chunk this upload needs was reclaimed while it ran; nothing was committed, retry")
                self._repo.mark_chunks_state(conn, [h for h, _ in ingest.to_register], ChunkState.STORED)
                self._u.checkpoint("in_t2")
                self._repo.append_file_chunks(conn, file_id, ingest.manifest)
                if not self._repo.complete_file(conn, file_id, whole_sha):
                    raise UploadError("the upload was marked failed by reconciliation while it ran; nothing was committed")
                self._repo.finish_operation(conn, op_id, OperationStatus.SUCCEEDED)
            self._u.checkpoint("after_commit")
            return UploadResult(file_id, name, size, whole_sha, len(ingest.manifest), ingest.new_chunks, ingest.duplicate_chunks,
                                ingest.bytes_written, tuple(ingest.outcomes))
        except Exception as exc:
            disk_full = is_disk_full(exc)
            self._fail(conn, file_id, file_row_created, op_id, tracker, exc)
            if isinstance(exc, InsufficientStorageError):
                raise
            if disk_full:
                raise InsufficientStorageError("the storage volume ran out of space during the upload; nothing from it was kept") from exc
            if isinstance(exc, ChunkVaultError):
                raise
            if is_concurrency_conflict(exc):
                raise ConcurrentConflictError("a concurrent operation conflicted with this upload; nothing was committed, retry") from exc
            raise UploadError("upload failed and was rolled back") from exc

    # ------------------------------------------------------------------------------------------------ chunk phase
    def _ingest(self, conn, part: Path, plan: list[PlannedChunk], deadline: Deadline, tracker: _Tracker):
        from app.deduplication.upload import _Ingest  # the shared result shape (local import: upload.py imports this module)

        c = self._c
        manifest = [(p.index, p.hash, p.size) for p in plan]
        first: dict[str, PlannedChunk] = {}
        for p in plan:
            first.setdefault(p.hash, p)
        repeated = len(plan) - len(first)
        rows = self._lookup(conn, list(first))  # ONE lookup for the whole file, under the lock: current rows, current states

        new_items: list[PlannedChunk] = []
        stored_items: list[PlannedChunk] = []
        for h, p in first.items():
            row = rows.get(h)
            if row is not None and row.size_bytes != p.size:
                raise ChunkIdentityConflictError("an existing chunk has the same digest but a different size")
            if row is None or row.state is ChunkState.PENDING:
                new_items.append(p)  # publish() is idempotent: an object that already exists is verified, never replaced
            elif row.state is ChunkState.STORED:
                stored_items.append(p)
            elif row.state is ChunkState.DELETING:
                raise ChunkReclaiming(f"chunk {h[:12]}… is being reclaimed by garbage collection; finish it, then retry")
            else:
                raise ChunkUnavailableError(f"existing chunk {h[:12]}… is in state '{row.state.value}'; run an integrity audit")
        deadline.check("looking up existing chunks")

        # Duplicates are VERIFIED (mode 'exists' / 'full'; 'none' is an explicit operator choice), all at once, not one request each.
        for p, status in zip(stored_items, self._verify_duplicates(stored_items)):
            if status is not ChunkStatus.OK:
                raise ChunkUnavailableError(f"existing chunk {p.hash[:12]}… is {status.value} in the chunk store; run an integrity audit")
        deadline.check("verifying existing chunks")

        created_of: dict[str, bool] = {}
        with ThreadPoolExecutor(max_workers=c.io_workers, thread_name_prefix="chunk-io") as pool, open(part, "rb") as src:
            for i in range(0, len(new_items), c.batch_chunks):
                deadline.check("publishing chunks")
                batch = new_items[i:i + c.batch_chunks]
                missing = [(p.hash, p.size) for p in batch if rows.get(p.hash) is None]
                if missing:
                    # quota check + registration are ONE atomic step inside the database (see reserve_chunks)
                    try:
                        with self._db.transaction(conn):
                            tracker.reserved.extend(self._repo.reserve_chunks(conn, missing, c.stored_bytes_cap))
                    except QuotaConflict as q:
                        raise QuotaExceededError(q.used_bytes, q.needed_bytes, c.stored_bytes_cap) from None
                data = {}
                for p in batch:
                    src.seek(p.offset)
                    data[p.hash] = src.read(p.size)
                    if len(data[p.hash]) != p.size:
                        raise UploadError("the staged upload ended before a planned chunk")
                futures = [(p, pool.submit(self._publish_one, p.hash, data[p.hash])) for p in batch]
                for _, fut in futures:  # every parallel write finishes (or fails) before anything else happens
                    fut.exception()
                for p, fut in futures:
                    created_of[p.hash] = fut.result()  # re-raises the first failure
                self._u.checkpoint("chunk_published")

        new_chunks = sum(1 for v in created_of.values() if v)
        adopted = len(created_of) - new_chunks
        bytes_written = sum(first[h].size for h, v in created_of.items() if v)
        label = {p.hash: ("new" if created_of[p.hash] else "adopted") for p in new_items}
        label.update({p.hash: "reused" for p in stored_items})
        outcomes = [ChunkOutcome(p.index, p.hash, p.size, "repeated_in_file" if p.index != first[p.hash].index else label[p.hash]) for p in plan]
        known = {h: p.size for h, p in first.items()}
        to_register = [(p.hash, p.size) for p in new_items]
        return _Ingest(manifest, outcomes, to_register, known, new_chunks, repeated + adopted + len(stored_items), bytes_written)

    def _verify_duplicates(self, items: list[PlannedChunk]) -> list[ChunkStatus]:
        mode = self._c.verify_on_reuse
        if not items or mode == "none":
            return [ChunkStatus.OK] * len(items)
        if mode == "exists":
            found = self._store.check_many([(p.hash, p.size) for p in items])  # one listing per prefix involved, in parallel
            return [found[p.hash] for p in items]
        with ThreadPoolExecutor(max_workers=self._c.io_workers, thread_name_prefix="chunk-verify") as pool:  # 'full': download + re-hash
            return list(pool.map(lambda p: self._store.verify(p.hash, p.size), items))

    def _publish_one(self, h: str, data: bytes) -> bool:
        return self._store.publish(self._store.stage(h, data))

    # ------------------------------------------------------------------------------------------------ failure handling
    def _fail(self, conn, file_id, file_row_created, op_id, tracker: _Tracker, exc: Exception) -> None:
        """Best effort; never raises. 1) bookkeeping, 2) give back this upload's own reservations."""
        try:
            with self._db.transaction(conn):
                if file_row_created:
                    self._repo.set_file_status(conn, file_id, FileStatus.FAILED)
                if op_id is not None:
                    self._repo.finish_operation(conn, op_id, OperationStatus.FAILED, str(exc)[:500] or type(exc).__name__)
        except Exception:
            pass
        try:
            self._release_reservations(conn, tracker.reserved)
        except Exception:
            pass

    def _release_reservations(self, conn, hashes: list[str]) -> None:
        """Remove exactly the chunk rows this upload created (still 'pending', unreferenced) and their objects.

        The durable protocol, restricted to those hashes: claim 'deleting' (guarded) -> delete the objects -> release the rows whose
        object is gone. Anything not finished within the budget, or whose object cannot be deleted right now (Storage outage), stays
        'deleting': counted by the quota and finished by garbage collection. A chunk any file references is never claimed."""
        if not hashes:
            return
        with self._db.transaction(conn):
            claimed = [r.hash for r in self._repo.claim_pending_chunks(conn, hashes)]
        if not claimed:
            return
        pool = ThreadPoolExecutor(max_workers=self._c.io_workers, thread_name_prefix="chunk-release")
        futures = {h: pool.submit(self._store.delete, h) for h in claimed}
        done, _ = wait(list(futures.values()), timeout=self._c.upload_cleanup_budget_seconds)
        pool.shutdown(wait=False, cancel_futures=True)  # unfinished deletes are not waited for; their rows stay 'deleting'
        gone = [h for h, f in futures.items() if f in done and f.exception() is None]
        if gone:
            with self._db.transaction(conn):
                self._repo.release_deleted_chunks(conn, gone)
