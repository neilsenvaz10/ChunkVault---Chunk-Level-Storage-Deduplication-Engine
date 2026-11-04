"""SQLite metadata repository: interface only (implemented in Phase 3).

Every method takes a ``sqlite3.Connection``. The repository never opens or
commits transactions: the caller (upload orchestrator, restoration engine,
auditor) owns the boundary via ``database.connection.transaction``. See
docs/database/schema.md for the transaction boundaries.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol

from app.core.errors import ChunkVaultError
from app.database.states import ChunkState, FileStatus, OperationStatus, OperationType


class RepositoryError(ChunkVaultError):
    """An unexpected database failure (wraps ``sqlite3.Error``)."""

    code = "repository_error"
    status_code = 500


class FileNotFoundInMetadata(ChunkVaultError):
    code = "file_not_found"
    status_code = 404


class ChunkSizeConflict(ChunkVaultError):
    """Same hash registered with a different size: indicates corruption or a bug."""

    code = "chunk_size_conflict"
    status_code = 409


@dataclass(frozen=True)
class FileRecord:
    file_id: str
    filename: str
    original_size: int
    sha256: str | None
    status: FileStatus
    created_at: str


@dataclass(frozen=True)
class ChunkRecord:
    hash: str
    size_bytes: int
    ref_count: int
    state: ChunkState
    created_at: str


@dataclass(frozen=True)
class FileChunkRecord:
    file_id: str
    chunk_index: int
    chunk_hash: str
    chunk_size: int


@dataclass(frozen=True)
class OperationRecord:
    operation_id: int
    file_id: str | None
    operation_type: OperationType
    status: OperationStatus
    started_at: str
    completed_at: str | None
    error_message: str | None


class MetadataRepository(Protocol):
    def create_file(self, conn: sqlite3.Connection, file_id: str, filename: str, original_size: int) -> FileRecord: ...

    def set_file_status(
        self, conn: sqlite3.Connection, file_id: str, status: FileStatus, sha256: str | None = None
    ) -> None:
        """Raises ``FileNotFoundInMetadata``; the schema rejects 'complete' without sha256."""
        ...

    def get_file(self, conn: sqlite3.Connection, file_id: str) -> FileRecord | None: ...

    def list_files(self, conn: sqlite3.Connection, limit: int, offset: int) -> list[FileRecord]: ...

    def get_chunk(self, conn: sqlite3.Connection, chunk_hash: str) -> ChunkRecord | None: ...

    def register_chunk(self, conn: sqlite3.Connection, chunk_hash: str, size_bytes: int) -> tuple[ChunkRecord, bool]:
        """Insert as 'pending' if absent. Returns (record, created).
        Raises ``ChunkSizeConflict`` if the hash exists with a different size."""
        ...

    def mark_chunk_state(self, conn: sqlite3.Connection, chunk_hash: str, state: ChunkState) -> None: ...

    def append_file_chunk(
        self, conn: sqlite3.Connection, file_id: str, chunk_index: int, chunk_hash: str, chunk_size: int
    ) -> None:
        """Insert one mapping row; the schema trigger increments ref_count."""
        ...

    def get_file_chunks(self, conn: sqlite3.Connection, file_id: str) -> list[FileChunkRecord]:
        """Ordered by chunk_index."""
        ...

    def delete_file(self, conn: sqlite3.Connection, file_id: str) -> list[str]:
        """Delete the file row (cascading to file_chunks; triggers decrement ref_count).
        Returns hashes whose ref_count is now 0, i.e. candidates for physical deletion."""
        ...

    def delete_chunk_row(self, conn: sqlite3.Connection, chunk_hash: str) -> None:
        """Remove a chunk row; the schema's RESTRICT FK refuses if still referenced."""
        ...

    def start_operation(
        self, conn: sqlite3.Connection, operation_type: OperationType, file_id: str | None
    ) -> int: ...

    def finish_operation(
        self, conn: sqlite3.Connection, operation_id: int, status: OperationStatus, error_message: str | None = None
    ) -> None: ...

    def find_stuck_operations(self, conn: sqlite3.Connection) -> list[OperationRecord]:
        """Operations still 'running' at startup/reconciliation time."""
        ...

    def ref_count_mismatches(self, conn: sqlite3.Connection) -> list[tuple[str, int, int]]:
        """Rows of v_ref_count_mismatch: (hash, recorded, actual)."""
        ...
