from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.core.errors import ChunkVaultError
from app.database.interfaces import Database
from app.database.repository import MetadataRepository
from app.database.states import FileStatus, OperationStatus, OperationType
from app.hashing.sha256 import Sha256Service
from app.restoration.interfaces import (
    ChunkCorruptionError,
    FileHashMismatchError,
    FileNotRestorableError,
    ManifestError,
    MissingChunkError,
    RestoreError,
    RestoreResult,
)
from app.storage.filesystem import FilesystemChunkStore
from app.storage.interfaces import ChunkCorruptedError, ChunkNotFoundError


class Restorer:
    def __init__(self, settings: Settings, db: Database, repo: MetadataRepository, store: FilesystemChunkStore,
                 hasher: Sha256Service) -> None:
        self._s, self._db, self._repo, self._store, self._hasher = settings, db, repo, store, hasher

    def restore_to_file(self, file_id: str, dest: Path) -> RestoreResult:
        """Restore a file to ``dest``.

        Connections are used only for the three short metadata steps and are released in between, so a slow chunk store (the cloud
        backend fetches over the network) can never pin a pooled database connection:

        1. ONE consistent read-only snapshot of the file row and its manifest, then the operation row is recorded;
        2. the chunks are fetched, verified and written WITHOUT any connection;
        3. a fresh connection records the outcome (best effort, as before).

        Integrity does not depend on the connection: every chunk is verified against its digest and the whole file against the
        recorded SHA-256 and size before success is reported."""
        with self._db.connection() as conn:
            with self._db.snapshot(conn):  # file row + manifest from one point in time
                file = self._repo.get_file(conn, file_id)
                if file is None or file.status is not FileStatus.COMPLETE:
                    raise FileNotRestorableError("file does not exist or is not complete")
                manifest = self._repo.get_file_chunks(conn, file_id)
            with self._db.transaction(conn):
                op_id = self._repo.start_operation(conn, OperationType.RESTORE, file_id)
        try:
            if [m.chunk_index for m in manifest] != list(range(len(manifest))):
                raise ManifestError("chunk list has gaps or duplicate positions")
            self._reassemble(file, manifest, dest)
        except BaseException as exc:
            dest.unlink(missing_ok=True)
            self._finish(op_id, OperationStatus.FAILED, str(exc))
            if isinstance(exc, Exception) and not isinstance(exc, ChunkVaultError):
                raise RestoreError("restore failed") from exc
            raise
        self._finish(op_id, OperationStatus.SUCCEEDED)
        return RestoreResult(file_id, file.filename, file.original_size, file.sha256, dest)

    def _reassemble(self, file, manifest, dest: Path) -> None:
        whole = self._hasher.new_hasher()
        total = 0
        read_many = getattr(self._store, "read_many", None)  # remote stores prefetch with bounded concurrency
        chunks = read_many([m.chunk_hash for m in manifest]) if read_many else None
        try:
            with open(dest, "xb") as out:
                for m in manifest:
                    try:
                        data = next(chunks) if chunks is not None else self._store.read(m.chunk_hash)
                    except ChunkNotFoundError:
                        raise MissingChunkError(
                            f"chunk {m.chunk_index} ({m.chunk_hash[:12]}…) is missing from the chunk store"
                        ) from None
                    except ChunkCorruptedError:
                        raise ChunkCorruptionError(
                            f"chunk {m.chunk_index} ({m.chunk_hash[:12]}…) failed verification"
                        ) from None
                    if len(data) != m.chunk_size or not self._hasher.verify(data, m.chunk_hash):
                        raise ChunkCorruptionError(
                            f"chunk {m.chunk_index} ({m.chunk_hash[:12]}…) failed verification"
                        )
                    out.write(data)
                    whole.update(data)
                    total += len(data)
                out.flush()
        except OSError as exc:
            raise RestoreError(f"cannot write restored file: {exc.strerror or exc}") from exc
        finally:
            if chunks is not None:
                chunks.close()  # cancels read-ahead that was not consumed
        if total != file.original_size:
            raise FileHashMismatchError(f"restored size {total} != recorded size {file.original_size}")
        if whole.hexdigest() != file.sha256:
            raise FileHashMismatchError("restored file SHA-256 does not match the recorded SHA-256")

    def _finish(self, op_id, status, message=None) -> None:
        """Record the outcome on a FRESH short connection (the restore itself held none). Best effort: a restored file is never
        withheld because the bookkeeping row could not be updated; a row left 'running' is aborted by reconciliation."""
        try:
            with self._db.connection() as conn, self._db.transaction(conn):
                self._repo.finish_operation(conn, op_id, status, (message or None) and message[:500])
        except Exception:
            pass
