"""Deduplication manager and upload orchestrator: interfaces and shared types.

Implementations: ``deduplication/manager.py`` and ``deduplication/upload.py``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import BinaryIO, Protocol

from app.core.errors import ChunkVaultError


class UploadError(ChunkVaultError):
    """Upload failed and was rolled back; no file row remains 'complete'."""

    code = "upload_failed"
    status_code = 500


class EmptyFilenameError(UploadError):
    code = "invalid_filename"
    status_code = 422


class UploadTooLargeError(UploadError):
    code = "upload_too_large"
    status_code = 413


class InsufficientStorageError(UploadError):
    """Not enough free space on the chunk store's volume (advisory preflight) or the volume filled up mid-upload.
    Nothing from the upload is kept: staging is removed, chunks it created are un-published and the file is 'failed'."""

    code = "insufficient_storage"
    status_code = 507


class ChunkIdentityConflictError(UploadError):
    """Same SHA-256 but different size. Treated as corruption, never as a duplicate."""

    code = "chunk_identity_conflict"
    status_code = 409


class ChunkUnavailableError(UploadError):
    """Metadata says a chunk is stored but its bytes are missing, or the chunk is
    flagged corrupt. Ordinary uploads never overwrite or repair such chunks."""

    code = "chunk_unavailable"
    status_code = 409


class ChunkDecision(StrEnum):
    NEW = "new"  # no row, no bytes: stage + publish bytes, register row
    DUPLICATE = "duplicate"  # row is 'stored' and its bytes verified: add mapping only
    ADOPT = "adopt"  # no row but valid bytes on disk (earlier interrupted upload): register row only


@dataclass(frozen=True)
class ChunkOutcome:
    """What the upload did with one manifest position. Recorded only in the upload
    response (not persisted), because it is a fact about that one upload."""

    chunk_index: int
    chunk_hash: str
    chunk_size: int
    decision: str  # new | reused | adopted | repeated_in_file


@dataclass(frozen=True)
class UploadResult:
    file_id: str
    filename: str
    original_size: int
    sha256: str
    chunk_count: int
    new_chunks: int
    duplicate_chunks: int
    bytes_written: int
    chunk_outcomes: tuple[ChunkOutcome, ...] = ()


class DeduplicationManager(Protocol):
    def decide(self, conn: sqlite3.Connection, chunk_hash: str, size_bytes: int) -> ChunkDecision:
        """Classify a chunk from metadata AND verified bytes.

        Raises ``ChunkIdentityConflictError`` on a size mismatch for a known hash,
        ``ChunkUnavailableError`` if a known chunk is corrupt/missing, and
        ``ChunkCorruptedError`` if unreferenced bytes with this name do not match it.
        Occurrences repeated within one upload are handled by the orchestrator:
        each still gets its own manifest row.
        """
        ...


class UploadOrchestrator(Protocol):
    def upload(self, filename: str, stream: BinaryIO) -> UploadResult:
        """Run the upload pipeline: stage -> CDC -> SHA-256 -> dedup -> store -> commit.

        Raises : ``EmptyFilenameError``, ``UploadTooLargeError``, ``WriterBusyError``,
        ``ChunkIdentityConflictError``, ``ChunkUnavailableError``, ``UploadError``.
        Failure handling: the operation and file are marked 'failed', staging
        files are removed, chunks this upload published are removed again if no
        metadata references them, and anything left by a crash is found by the
        integrity audit and removed by explicit reconciliation. A file becomes
        'complete' only in the single metadata transaction that maps every chunk.
        """
        ...
