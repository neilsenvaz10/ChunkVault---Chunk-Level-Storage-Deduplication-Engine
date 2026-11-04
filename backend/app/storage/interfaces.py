"""Content-addressed chunk store: interface (implemented in storage/filesystem.py).

Layout: ``data/chunks/<h[0:2]>/<hash>``; writes go to ``data/staging/`` first and
are published with an atomic same-volume rename. Implemented in
``storage/filesystem.py``. See docs/architecture/consistency-protocol.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.errors import ChunkVaultError


class ChunkStoreError(ChunkVaultError):
    code = "chunk_store_error"
    status_code = 500


class ChunkNotFoundError(ChunkStoreError):
    code = "chunk_not_found"


class ChunkCorruptedError(ChunkStoreError):
    """An existing stored chunk does not match its digest. Never overwritten."""

    code = "chunk_corrupt"


class ChunkWriteError(ChunkStoreError):
    """Staging or publishing failed (disk full, permissions, rename failure)."""

    code = "chunk_write_error"


@dataclass(frozen=True)
class StagedChunk:
    hash: str
    path: Path  # file inside the staging directory
    size_bytes: int


class ChunkStore(Protocol):
    def stage(self, chunk_hash: str, data: bytes) -> StagedChunk:
        """Write bytes to a unique staging file and fsync it.
        Raises ``ChunkWriteError``. On failure the partial staging file is removed."""
        ...

    def publish(self, staged: StagedChunk) -> bool:
        """Atomically move a staged chunk to its content address.

        Returns True if this call created the chunk, False if it already existed
        (the staged copy is then discarded). Idempotent. Raises ``ChunkWriteError``.
        """
        ...

    def discard(self, staged: StagedChunk) -> None:
        """Remove a staged file; no error if already gone."""
        ...

    def exists(self, chunk_hash: str) -> bool: ...

    def read(self, chunk_hash: str) -> bytes:
        """Raises ``ChunkNotFoundError`` if absent."""
        ...

    def delete(self, chunk_hash: str) -> bool:
        """Remove a published chunk. Only legal once metadata shows ref_count == 0.
        Returns False if it was already absent (idempotent)."""
        ...

    def iter_hashes(self) -> Iterator[str]:
        """Enumerate published chunk hashes (used by reconciliation)."""
        ...

    def iter_staged(self) -> Iterator[Path]:
        """Enumerate leftover staging files (used by reconciliation)."""
        ...
