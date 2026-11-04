from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.core.errors import ChunkVaultError
from app.database.connection import connect, transaction
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
from app.storage.interfaces import ChunkNotFoundError


class Restorer:
    def __init__(self, settings: Settings, repo: MetadataRepository, store: FilesystemChunkStore,
                 hasher: Sha256Service) -> None:
        self._s, self._repo, self._store, self._hasher = settings, repo, store, hasher

    def restore_to_file(self, file_id: str, dest: Path) -> RestoreResult:
        conn = connect(self._s.db_path)
        op_id = None
        try:
            file = self._repo.get_file(conn, file_id)
            if file is None or file.status is not FileStatus.COMPLETE:
                raise FileNotRestorableError("file does not exist or is not complete")
            with transaction(conn):
                op_id = self._repo.start_operation(conn, OperationType.RESTORE, file_id)
            try:
                manifest = self._repo.get_file_chunks(conn, file_id)
                if [m.chunk_index for m in manifest] != list(range(len(manifest))):
                    raise ManifestError("chunk list has gaps or duplicate positions")
                self._reassemble(file, manifest, dest)
            except BaseException as exc:
                dest.unlink(missing_ok=True)
                self._finish(conn, op_id, OperationStatus.FAILED, str(exc))
                if isinstance(exc, Exception) and not isinstance(exc, ChunkVaultError):
                    raise RestoreError("restore failed") from exc
                raise
            self._finish(conn, op_id, OperationStatus.SUCCEEDED)
            return RestoreResult(file_id, file.filename, file.original_size, file.sha256, dest)
        finally:
            conn.close()

    def _reassemble(self, file, manifest, dest: Path) -> None:
        whole = self._hasher.new_hasher()
        total = 0
        try:
            with open(dest, "xb") as out:
                for m in manifest:
                    try:
                        data = self._store.read(m.chunk_hash)
                    except ChunkNotFoundError:
                        raise MissingChunkError(
                            f"chunk {m.chunk_index} ({m.chunk_hash[:12]}…) is missing from the chunk store"
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
        if total != file.original_size:
            raise FileHashMismatchError(f"restored size {total} != recorded size {file.original_size}")
        if whole.hexdigest() != file.sha256:
            raise FileHashMismatchError("restored file SHA-256 does not match the recorded SHA-256")

    def _finish(self, conn, op_id, status, message=None) -> None:
        try:
            with transaction(conn):
                self._repo.finish_operation(conn, op_id, status, (message or None) and message[:500])
        except Exception:
            pass
