"""Restoration engine: interface only (Phase 4)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from app.core.errors import ChunkVaultError


class RestoreError(ChunkVaultError):
    code = "restore_failed"
    status_code = 500


class FileNotRestorableError(RestoreError):
    """File does not exist or is not in the 'complete' state."""

    code = "file_not_restorable"
    status_code = 404


class MissingChunkError(RestoreError):
    code = "chunk_missing"


class ChunkCorruptionError(RestoreError):
    """Stored bytes do not hash to the recorded SHA-256."""

    code = "chunk_corrupt"


class FileHashMismatchError(RestoreError):
    """All chunks verified but the reassembled file hash differs from files.sha256."""

    code = "file_hash_mismatch"


class RestorationEngine(Protocol):
    def restore(self, file_id: str) -> Iterator[bytes]:
        """Yield the original bytes in chunk_index order.

        Each chunk is re-hashed before being yielded; the whole-file SHA-256 is
        checked at the end. Raises ``FileNotRestorableError`` before yielding
        anything; ``MissingChunkError`` / ``ChunkCorruptionError`` /
        ``FileHashMismatchError`` abort the stream (never silently return wrong
        data). Read-only: restoration modifies no metadata except its operation row.
        """
        ...
