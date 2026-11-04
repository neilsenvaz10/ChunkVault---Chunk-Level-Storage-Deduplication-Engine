"""Deduplication manager and upload orchestrator: interfaces only (Phase 3)."""

from __future__ import annotations

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


class ChunkIdentityConflictError(UploadError):
    """Same SHA-256 but different size. Treated as corruption, never as a duplicate."""

    code = "chunk_identity_conflict"
    status_code = 409


class ChunkDecision(StrEnum):
    NEW = "new"  # no row: register pending, stage, publish, mark stored
    DUPLICATE = "duplicate"  # row is 'stored': only add the file_chunks mapping
    REPAIR = "repair"  # row is 'pending' (earlier crash) or 'corrupt': bytes must be rewritten


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


class DeduplicationManager(Protocol):
    def decide(self, chunk_hash: str, size_bytes: int) -> ChunkDecision:
        """Classify a chunk by consulting metadata (never the filesystem alone).

        Raises ``ChunkIdentityConflictError`` on a size mismatch for a known hash.
        A hash repeated inside the same upload is a DUPLICATE after its first
        occurrence is registered, and still gets its own file_chunks row.
        """
        ...


class UploadOrchestrator(Protocol):
    def upload(self, filename: str, stream: BinaryIO) -> UploadResult:
        """Run the upload pipeline: stage -> CDC -> SHA-256 -> dedup -> store -> commit.

        Inputs : validated filename and a readable binary stream.
        Outputs: ``UploadResult``.
        Raises : ``EmptyFilenameError``, ``ChunkIdentityConflictError``, ``UploadError``.
        Failure handling: any failure marks the operation 'failed', the file
        'failed', removes its staging files, and leaves published-but-unreferenced
        chunks for reconciliation. A file is never reported 'complete' unless every
        chunk is 'stored' and mapped.
        """
        ...
