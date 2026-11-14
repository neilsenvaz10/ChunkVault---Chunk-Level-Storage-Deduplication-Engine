"""Upload orchestrator: the consistency protocol from docs/architecture/consistency-protocol.md.

Order of effects (SQLite and the filesystem cannot share a transaction):

 1. validate filename; take the single-writer lock
 2. stream the upload into staging/ (enforces the size limit, computes the whole-file SHA-256)
 3. T1: insert files(status='uploading') + operations(upload, running)
 4. CDC -> SHA-256 -> dedup decision per chunk; new chunks are staged, fsynced and
    atomically published (bytes first)
 5. verify every chunk the manifest needs is present
 6. T2 (one transaction): register new chunk rows as 'stored', insert the ordered
    manifest (triggers maintain ref_count), set file 'complete' + sha256, finish operation
 7. remove the staged upload

Free space: after staging, and before any chunk is published, ``_check_free_space`` compares the free space on the
chunk store's volume with the bytes this upload could add. It is advisory (another process can still fill the disk),
so ENOSPC / "disk is full" failures at any later step are also translated to ``insufficient_storage`` after the normal
cleanup below.

Any ``Exception`` before the T2 commit leaves the file 'failed', the operation
'failed', removes staged files and un-publishes chunks this upload created that
no metadata references. A ``BaseException`` (modelling a process crash) is not
cleaned up here; the integrity audit finds the leftovers and reconciliation
removes them.
"""

from __future__ import annotations

import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.chunking.interfaces import Chunker
from app.core.config import CloudSettings, Settings
from app.core.diskfull import is_disk_full
from app.deduplication.cloud_upload import CloudUploadPipeline
from app.core.errors import ChunkVaultError, ConcurrentConflictError, is_concurrency_conflict
from app.core.locking import WriterLock
from app.database.interfaces import Database
from app.database.repository import MetadataRepository
from app.database.states import ChunkState, FileStatus, OperationStatus, OperationType
from app.deduplication.interfaces import (
    ChunkDecision,
    ChunkOutcome,
    ChunkIdentityConflictError,
    DeduplicationManager,
    EmptyFilenameError,
    InsufficientStorageError,
    UploadError,
    UploadResult,
    UploadTooLargeError,
)
from app.hashing.sha256 import Sha256Service
from app.storage.filesystem import FilesystemChunkStore

_COPY_BLOCK = 1024 * 1024
# Rough SQLite growth per chunk (chunk row + manifest row + index entries), used only for the advisory margin.
_METADATA_BYTES_PER_CHUNK = 512
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_filename(raw: str | None) -> str:
    """Keep only the final path component; reject empty/control-character names."""
    if raw is None:
        raise EmptyFilenameError("filename is required")
    name = unicodedata.normalize("NFC", raw.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    if not name or name in {".", ".."} or _CONTROL.search(name):
        raise EmptyFilenameError("filename is empty or contains invalid characters")
    if len(name) > 255:
        raise EmptyFilenameError("filename is longer than 255 characters")
    return name


def _no_checkpoint(_name: str) -> None:
    return None


@dataclass
class _Ingest:
    """What the chunk phase produced, ready for the metadata transaction."""

    manifest: list[tuple[int, str, int]]
    outcomes: list[ChunkOutcome]
    to_register: list[tuple[str, int]]  # chunks whose rows must be 'stored' once the manifest commits
    known: dict[str, int]
    new_chunks: int
    duplicate_chunks: int
    bytes_written: int


class Uploader:
    # Named points in the protocol (staged, t1_committed, chunk_published, chunks_ready,
    # in_t2, after_commit). Production leaves this a no-op; the isolated fault lab and tests
    # replace it to raise a failure at exactly that point of the real code path.
    checkpoint = staticmethod(_no_checkpoint)

    def __init__(
        self,
        settings: Settings,
        db: Database,
        repo: MetadataRepository,
        store: FilesystemChunkStore,
        chunker: Chunker,
        dedup: DeduplicationManager,
        hasher: Sha256Service,
        lock: WriterLock,
        cloud: CloudSettings | None = None,
    ) -> None:
        self._s, self._db, self._repo, self._store = settings, db, repo, store
        self._chunker, self._dedup, self._hasher, self._lock = chunker, dedup, hasher, lock
        self._cloud = cloud  # None = local backend (sequential, filesystem); set = cloud pipeline
        self._pipeline = (CloudUploadPipeline(self, settings, db, repo, store, chunker, hasher, lock, cloud) if cloud is not None else None)

    def upload(self, filename: str, stream: BinaryIO) -> UploadResult:
        name = sanitize_filename(filename)
        if self._pipeline is not None:  # cloud: stage and chunk BEFORE taking the writer lock (see cloud_upload.py)
            return self._pipeline.upload(name, stream)
        with self._lock.acquire():
            return self._upload_locked(name, stream)

    # -- internals -----------------------------------------------------------

    def _upload_locked(self, name: str, stream: BinaryIO) -> UploadResult:
        with self._db.connection() as conn:  # always returned/closed, also on failure
            return self._upload_on(conn, name, stream)

    def _upload_on(self, conn, name: str, stream: BinaryIO) -> UploadResult:
        file_id = uuid.uuid4().hex
        part = self._store.staging_dir / f"upload-{file_id}.part"
        op_id: int | None = None
        file_row_created = False
        published: list[str] = []  # chunks THIS upload created in the store (local backend only; see _cleanup_failure)
        try:
            size, whole_sha = self._stage_upload(stream, part)
            self.checkpoint("staged")
            self._check_free_space(conn, part, size)
            with self._db.transaction(conn):  # T1
                self._repo.create_file(conn, file_id, name, size)
                file_row_created = True
                op_id = self._repo.start_operation(conn, OperationType.UPLOAD, file_id)
            self.checkpoint("t1_committed")

            ingest = self._ingest_chunks(conn, part, published)
            if sum(m[2] for m in ingest.manifest) != size:
                raise UploadError("chunk sizes do not add up to the uploaded size")
            self.checkpoint("chunks_ready")
            for h in ingest.known:  # step 5: everything the manifest needs is on disk
                if not self._store.exists(h):
                    raise UploadError("a required chunk is missing before metadata commit")

            with self._db.transaction(conn):  # T2: the only place a file becomes 'complete'
                for h, csize in ingest.to_register:
                    self._repo.register_chunk(conn, h, csize, ChunkState.STORED)
                self.checkpoint("in_t2")
                for index, h, csize in ingest.manifest:
                    self._repo.append_file_chunk(conn, file_id, index, h, csize)
                self._repo.set_file_status(conn, file_id, FileStatus.COMPLETE, sha256=whole_sha)
                self._repo.finish_operation(conn, op_id, OperationStatus.SUCCEEDED)
            self.checkpoint("after_commit")
            part.unlink(missing_ok=True)  # step 7: the file is durable in the store now
            return UploadResult(file_id, name, size, whole_sha, len(ingest.manifest), ingest.new_chunks,
                                ingest.duplicate_chunks, ingest.bytes_written, tuple(ingest.outcomes))
        except Exception as exc:
            disk_full = is_disk_full(exc)
            self._cleanup_failure(conn, file_id, file_row_created, op_id, published, exc)
            if isinstance(exc, InsufficientStorageError):
                raise
            if disk_full:
                raise InsufficientStorageError(
                    "the storage volume ran out of space during the upload; nothing from it was kept"
                ) from exc
            if isinstance(exc, ChunkVaultError):
                raise
            if is_concurrency_conflict(exc):
                raise ConcurrentConflictError("a concurrent operation conflicted with this upload; nothing was committed, retry") from exc
            raise UploadError("upload failed and was rolled back") from exc

    # -- chunk phase: local (sequential, filesystem) ------------------------------------------------------------

    def _ingest_chunks(self, conn, part: Path, published: list[str]) -> "_Ingest":
        manifest: list[tuple[int, str, int]] = []
        outcomes: list[ChunkOutcome] = []
        known: dict[str, int] = {}  # hash -> size, for chunks seen in this upload
        to_register: list[tuple[str, int]] = []
        new_chunks = duplicate_chunks = bytes_written = 0
        with open(part, "rb") as src:
            for chunk in self._chunker.chunk_stream(src):
                h = self._hasher.digest(chunk.data)
                if h in known:
                    if known[h] != chunk.size:
                        raise ChunkIdentityConflictError("same digest with different sizes inside one file")
                    duplicate_chunks += 1
                    label = "repeated_in_file"
                else:
                    decision = self._dedup.decide(conn, h, chunk.size)
                    known[h] = chunk.size
                    label = {ChunkDecision.NEW: "new", ChunkDecision.DUPLICATE: "reused",
                             ChunkDecision.ADOPT: "adopted"}[decision]
                    if decision is ChunkDecision.DUPLICATE:
                        duplicate_chunks += 1
                    else:
                        to_register.append((h, chunk.size))
                        if decision is ChunkDecision.NEW:
                            staged = self._store.stage(h, chunk.data)
                            try:
                                created = self._store.publish(staged)
                            except Exception:
                                self._store.discard(staged)  # e.g. disk full: do not leave the temp file in staging
                                raise
                            if created:
                                published.append(h)
                            self.checkpoint("chunk_published")
                            bytes_written += chunk.size
                            new_chunks += 1
                        else:  # ADOPT
                            duplicate_chunks += 1
                manifest.append((chunk.index, h, chunk.size))
                outcomes.append(ChunkOutcome(chunk.index, h, chunk.size, label))
        return _Ingest(manifest, outcomes, to_register, known, new_chunks, duplicate_chunks, bytes_written)

    def _margin(self, size: int) -> int:
        """Extra bytes to keep free on top of the chunk bytes: the configured reserve, one chunk in flight in
        staging (a staged chunk is renamed into place, not copied), and SQLite growth for the worst-case chunk count."""
        cdc = self._s.cdc
        max_chunks = size // cdc.min_chunk_size + 1
        return self._s.free_space_reserve_bytes + cdc.max_chunk_size + max_chunks * _METADATA_BYTES_PER_CHUNK

    def _check_free_space(self, conn, part: Path, size: int) -> None:
        """Advisory protection, not a guarantee: refuse an upload that clearly cannot fit, before publishing anything.

        The staged copy already exists, so the worst case is that every chunk is new and adds ``size`` more bytes.
        If the free space on the chunk store's volume covers that plus the margin, the upload proceeds with no further
        work. Only when it does not is the staged file chunked (read-only) to count the bytes that are *really* new, so
        a duplicate upload that needs little or nothing is never refused. The check is skipped if the OS cannot report
        free space. Disk-full errors that still happen later are handled separately (see ``_upload_locked``).
        """
        if size == 0:
            return
        free = self._store.free_bytes()
        if free is None:
            return
        margin = self._margin(size)
        if free >= size + margin:
            return
        needed = self._new_bytes_needed(conn, part) + margin
        if free < needed:
            raise InsufficientStorageError(
                f"not enough free space on the storage volume: about {needed} bytes are needed, {free} are free",
                details={"required_bytes": needed, "free_bytes": free, "advisory": True},
            )

    def _new_bytes_needed(self, conn, part: Path) -> int:
        """Bytes of distinct chunks in the staged file that are not already stored (read-only dry run).
        Anything that is not a verified-present 'stored' row is counted, so the estimate errs on the side of caution."""
        seen: set[str] = set()
        total = 0
        with open(part, "rb") as src:
            for chunk in self._chunker.chunk_stream(src):
                h = self._hasher.digest(chunk.data)
                if h in seen:
                    continue
                seen.add(h)
                row = self._repo.get_chunk(conn, h)
                if row is None or row.state is not ChunkState.STORED:
                    total += chunk.size
        return total

    def _stage_upload(self, stream: BinaryIO, part: Path) -> tuple[int, str]:
        hasher = self._hasher.new_hasher()
        total = 0
        try:
            with open(part, "xb") as out:
                while block := stream.read(_COPY_BLOCK):
                    total += len(block)
                    if total > self._s.max_upload_bytes:
                        raise UploadTooLargeError(
                            f"upload exceeds the {self._s.max_upload_bytes}-byte limit"
                        )
                    hasher.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
        except OSError as exc:
            raise UploadError(f"cannot stage upload: {exc.strerror or exc}") from exc
        return total, hasher.hexdigest()

    def _cleanup_failure(self, conn, file_id, file_row_created, op_id, published, exc) -> None:
        """Best effort; never raises. Anything it cannot clean is reported by the audit."""
        try:
            (self._store.staging_dir / f"upload-{file_id}.part").unlink(missing_ok=True)
        except OSError:
            pass
        # A local store un-publishes chunks this upload created that no metadata references. The cloud store never deletes as
        # failure cleanup: such objects keep their 'pending' rows (counted by the quota) until explicit, authenticated GC.
        for h in (published if getattr(self._store, "deletes_on_failed_upload", True) else ()):
            try:
                row = self._repo.get_chunk(conn, h)
                if row is None:  # no metadata references it: safe to un-publish
                    self._store.delete(h)
            except Exception:
                pass
        try:
            with self._db.transaction(conn):
                if file_row_created:
                    self._repo.set_file_status(conn, file_id, FileStatus.FAILED)
                if op_id is not None:
                    self._repo.finish_operation(conn, op_id, OperationStatus.FAILED, str(exc)[:500] or type(exc).__name__)
        except Exception:
            pass
