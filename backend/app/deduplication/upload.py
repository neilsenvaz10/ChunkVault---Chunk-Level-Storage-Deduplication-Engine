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

Any ``Exception`` before the T2 commit leaves the file 'failed', the operation
'failed', removes staged files and un-publishes chunks this upload created that
no metadata references. A ``BaseException`` (modelling a process crash) is not
cleaned up here; the integrity audit finds the leftovers and reconciliation
removes them.
"""

from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
import uuid
from pathlib import Path
from typing import BinaryIO

from app.chunking.interfaces import Chunker
from app.core.config import Settings
from app.core.errors import ChunkVaultError
from app.core.locking import WriterLock
from app.database.connection import connect, transaction
from app.database.repository import MetadataRepository
from app.database.states import ChunkState, FileStatus, OperationStatus, OperationType
from app.deduplication.interfaces import (
    ChunkDecision,
    ChunkOutcome,
    ChunkIdentityConflictError,
    DeduplicationManager,
    EmptyFilenameError,
    UploadError,
    UploadResult,
    UploadTooLargeError,
)
from app.hashing.sha256 import Sha256Service
from app.storage.filesystem import FilesystemChunkStore

_COPY_BLOCK = 1024 * 1024
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


class Uploader:
    def __init__(
        self,
        settings: Settings,
        repo: MetadataRepository,
        store: FilesystemChunkStore,
        chunker: Chunker,
        dedup: DeduplicationManager,
        hasher: Sha256Service,
        lock: WriterLock,
    ) -> None:
        self._s, self._repo, self._store = settings, repo, store
        self._chunker, self._dedup, self._hasher, self._lock = chunker, dedup, hasher, lock

    def upload(self, filename: str, stream: BinaryIO) -> UploadResult:
        name = sanitize_filename(filename)
        with self._lock.acquire():
            return self._upload_locked(name, stream)

    # -- internals -----------------------------------------------------------

    def _upload_locked(self, name: str, stream: BinaryIO) -> UploadResult:
        file_id = uuid.uuid4().hex
        part = self._store.staging_dir / f"upload-{file_id}.part"
        conn = connect(self._s.db_path)
        op_id: int | None = None
        file_row_created = False
        published: list[str] = []  # chunks THIS upload created on disk
        try:
            size, whole_sha = self._stage_upload(stream, part)
            with transaction(conn):  # T1
                self._repo.create_file(conn, file_id, name, size)
                file_row_created = True
                op_id = self._repo.start_operation(conn, OperationType.UPLOAD, file_id)

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
                                if self._store.publish(staged):
                                    published.append(h)
                                bytes_written += chunk.size
                                new_chunks += 1
                            else:  # ADOPT
                                duplicate_chunks += 1
                    manifest.append((chunk.index, h, chunk.size))
                    outcomes.append(ChunkOutcome(chunk.index, h, chunk.size, label))

            if sum(m[2] for m in manifest) != size:
                raise UploadError("chunk sizes do not add up to the uploaded size")
            for h in known:  # step 5: everything the manifest needs is on disk
                if not self._store.exists(h):
                    raise UploadError("a required chunk is missing before metadata commit")

            with transaction(conn):  # T2: the only place a file becomes 'complete'
                for h, csize in to_register:
                    self._repo.register_chunk(conn, h, csize, ChunkState.STORED)
                for index, h, csize in manifest:
                    self._repo.append_file_chunk(conn, file_id, index, h, csize)
                self._repo.set_file_status(conn, file_id, FileStatus.COMPLETE, sha256=whole_sha)
                self._repo.finish_operation(conn, op_id, OperationStatus.SUCCEEDED)
            part.unlink(missing_ok=True)  # step 7: the file is durable in the store now
            return UploadResult(file_id, name, size, whole_sha, len(manifest),
                                new_chunks, duplicate_chunks, bytes_written, tuple(outcomes))
        except Exception as exc:
            self._cleanup_failure(conn, file_id, file_row_created, op_id, published, exc)
            if isinstance(exc, ChunkVaultError):
                raise
            raise UploadError("upload failed and was rolled back") from exc
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass

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
        for h in published:
            try:
                row = self._repo.get_chunk(conn, h)
                if row is None:  # no metadata references it: safe to un-publish
                    self._store.delete(h)
            except Exception:
                pass
        try:
            with transaction(conn):
                if file_row_created:
                    self._repo.set_file_status(conn, file_id, FileStatus.FAILED)
                if op_id is not None:
                    self._repo.finish_operation(conn, op_id, OperationStatus.FAILED, str(exc)[:500] or type(exc).__name__)
        except Exception:
            pass
