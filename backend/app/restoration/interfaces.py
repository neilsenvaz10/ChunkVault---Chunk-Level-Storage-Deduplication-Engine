"""Restoration engine interface (implemented in restoration/engine.py)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.errors import ChunkVaultError


class RestoreError(ChunkVaultError):
    code = "restore_failed"
    status_code = 500


class FileNotRestorableError(RestoreError):
    """File does not exist or is not in the 'complete' state."""

    code = "file_not_restorable"
    status_code = 404


class ManifestError(RestoreError):
    """The stored chunk list is not a contiguous 0..n-1 sequence."""

    code = "manifest_inconsistent"


class MissingChunkError(RestoreError):
    code = "chunk_missing"


class ChunkCorruptionError(RestoreError):
    """Stored bytes do not match the recorded SHA-256 or size."""

    code = "chunk_corrupt"


class FileHashMismatchError(RestoreError):
    """All chunks verified but the reassembled size or SHA-256 differs from the file record."""

    code = "file_hash_mismatch"


@dataclass(frozen=True)
class RestoreResult:
    file_id: str
    filename: str
    size: int
    sha256: str
    path: Path  # verified output file


class RestorationEngine(Protocol):
    def restore_to_file(self, file_id: str, dest: Path) -> RestoreResult:
        """Reassemble ``file_id`` into ``dest`` and verify it completely.

        Chunks are read in chunk_index order; each chunk's size and SHA-256 are
        checked, then the total size and whole-file SHA-256. ``dest`` is only
        left on disk if EVERYTHING verified; on any failure it is removed and
        an explicit error is raised (``FileNotRestorableError``, ``ManifestError``,
        ``MissingChunkError``, ``ChunkCorruptionError`` or ``FileHashMismatchError``).
        Never returns a partial restoration. Records a 'restore' operation.
        """
        ...
