"""Metadata repository: the interface, implemented by ``sqlite_repository`` (local) and ``postgres_repository`` (cloud).

Every method takes the opaque connection handed out by ``Database.connection()``. The repository never opens or commits
transactions: the caller (upload orchestrator, restoration engine, auditor) owns the boundary via ``Database.transaction``.
See docs/database/schema.md for the transaction boundaries. All SQL lives in the implementations, never in callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.errors import ChunkVaultError
from app.database.interfaces import Conn
from app.database.states import ChunkState, FileStatus, OperationStatus, OperationType


class RepositoryError(ChunkVaultError):
    """An unexpected database failure (wraps the driver error)."""

    code = "repository_error"
    status_code = 500


class FileNotFoundInMetadata(ChunkVaultError):
    code = "file_not_found"
    status_code = 404


class QuotaConflict(Exception):
    """The registration would take the stored-bytes quota over its limit (raised by ``reserve_chunks``)."""

    def __init__(self, used_bytes: int, needed_bytes: int) -> None:
        super().__init__("quota exceeded")
        self.used_bytes, self.needed_bytes = used_bytes, needed_bytes


class ChunkReclaiming(ChunkVaultError):
    """A chunk this operation needs has been claimed by garbage collection ('deleting'); it must not be referenced or reused.
    Finish the garbage collection (an administrator re-runs it), then retry."""

    code = "chunk_reclaiming"
    status_code = 409


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


@dataclass(frozen=True)
class ChunkReference:
    """One manifest row that references a chunk, joined with its file."""
    file_id: str
    filename: str
    file_status: str
    chunk_index: int


@dataclass(frozen=True)
class StorageTotals:
    """Aggregates behind /api/storage/stats; computed in one consistent read by the caller's snapshot."""
    files: int  # status 'complete'
    logical_bytes: int
    references: int  # manifest rows of complete files
    unique_chunks: int  # distinct chunks referenced by complete files
    unique_chunk_bytes: int
    unreferenced_chunks: int  # rows with ref_count 0
    unreferenced_chunk_bytes: int
    stored_chunks: int = 0  # rows in state 'stored' (the cloud statistics use these instead of listing the object store)
    stored_chunk_bytes: int = 0


# Whitelisted tables of the schema explorer and their stable display order (never built from user input).
INSPECTABLE_TABLES = ("files", "chunks", "file_chunks", "operations")


class MetadataRepository(Protocol):
    def create_file(self, conn: Conn, file_id: str, filename: str, original_size: int) -> FileRecord: ...

    def set_file_status(
        self, conn: Conn, file_id: str, status: FileStatus, sha256: str | None = None
    ) -> None:
        """Raises ``FileNotFoundInMetadata``; the schema rejects 'complete' without sha256."""
        ...

    def get_file(self, conn: Conn, file_id: str) -> FileRecord | None: ...

    def list_files(self, conn: Conn, limit: int, offset: int) -> list[FileRecord]:
        """Newest first."""
        ...

    def get_chunk(self, conn: Conn, chunk_hash: str) -> ChunkRecord | None: ...

    def register_chunk(
        self, conn: Conn, chunk_hash: str, size_bytes: int, state: ChunkState = ChunkState.PENDING
    ) -> tuple[ChunkRecord, bool]:
        """Insert with ``state`` (default 'pending') if absent. Returns (record, created).
        Raises ``ChunkSizeConflict`` if the hash exists with a different size."""
        ...

    def mark_chunk_state(self, conn: Conn, chunk_hash: str, state: ChunkState) -> None: ...

    def append_file_chunk(
        self, conn: Conn, file_id: str, chunk_index: int, chunk_hash: str, chunk_size: int
    ) -> None:
        """Insert one mapping row; the schema trigger increments ref_count."""
        ...

    def get_file_chunks(self, conn: Conn, file_id: str) -> list[FileChunkRecord]:
        """Ordered by chunk_index."""
        ...

    def delete_file(self, conn: Conn, file_id: str) -> list[str]:
        """Delete the file row (cascading to file_chunks; triggers decrement ref_count).
        Returns hashes whose ref_count is now 0, i.e. candidates for physical deletion."""
        ...

    def delete_chunk_row(self, conn: Conn, chunk_hash: str) -> None:
        """Remove a chunk row; the schema's RESTRICT FK refuses if still referenced."""
        ...

    def start_operation(
        self, conn: Conn, operation_type: OperationType, file_id: str | None
    ) -> int: ...

    def finish_operation(
        self, conn: Conn, operation_id: int, status: OperationStatus, error_message: str | None = None
    ) -> None: ...

    def count_files(self, conn: Conn) -> int: ...

    def list_chunks(
        self, conn: Conn, limit: int, offset: int, state: ChunkState | None = None
    ) -> list[ChunkRecord]: ...

    def count_chunks(self, conn: Conn, state: ChunkState | None = None) -> int: ...

    def unreferenced_chunks(self, conn: Conn) -> list[ChunkRecord]:
        """Chunk rows with ref_count == 0 (garbage-collection candidates)."""
        ...

    def delete_unreferenced_chunk_rows(self, conn: Conn, hashes: list[str]) -> list[str]:
        """Delete rows from ``hashes`` whose ref_count is 0; returns those actually deleted."""
        ...

    def list_stuck_files(self, conn: Conn) -> list[FileRecord]:
        """Files still in status 'uploading'."""
        ...

    def list_running_operations(self, conn: Conn) -> list[OperationRecord]: ...

    def chunks_referenced_by_file(self, conn: Conn, chunk_hash: str) -> list[str]:
        """Ids of files whose manifest contains the chunk."""
        ...

    def ref_count_mismatches(self, conn: Conn) -> list[tuple[str, int, int]]:
        """Rows of v_ref_count_mismatch: (hash, recorded, actual)."""
        ...

    # -- read models for the API and statistics (formerly raw SQL in the API layer) --------------------------

    def file_chunk_counts(self, conn: Conn, file_ids: list[str]) -> dict[str, int]:
        """Manifest rows per file for the given files (files without rows are absent)."""
        ...

    def count_manifest_references(self, conn: Conn, chunk_hash: str) -> int: ...

    def list_manifest_references(self, conn: Conn, chunk_hash: str, limit: int) -> list[ChunkReference]:
        """Ordered by file_id, chunk_index; at most ``limit`` rows."""
        ...

    def storage_totals(self, conn: Conn) -> StorageTotals: ...

    def count_rows(self, conn: Conn, table: str) -> int:
        """Row count of one of ``INSPECTABLE_TABLES`` (anything else raises ``ValueError``)."""
        ...

    def list_rows(self, conn: Conn, table: str, limit: int, offset: int) -> tuple[list[str], list[tuple]]:
        """(column names, rows) of one of ``INSPECTABLE_TABLES`` in its stable order; timestamps as UTC ISO-8601 strings."""
        ...

    # -- batched operations (one round trip each; the cloud pipeline relies on them) -------------------------

    def get_chunks(self, conn: Conn, hashes: list[str]) -> dict[str, ChunkRecord]:
        """The rows that exist among ``hashes`` (absent hashes are simply missing from the result)."""
        ...

    def register_chunks(self, conn: Conn, items: list[tuple[str, int]], state: ChunkState = ChunkState.PENDING) -> list[str]:
        """Insert the rows that do not exist yet (hash, size). Returns the hashes created. Raises ``ChunkSizeConflict`` if an
        existing row has a different size. Existing rows are not changed."""
        ...

    def mark_chunks_state(self, conn: Conn, hashes: list[str], state: ChunkState) -> None: ...

    def append_file_chunks(self, conn: Conn, file_id: str, rows: list[tuple[int, str, int]]) -> None:
        """Insert a whole manifest (chunk_index, chunk_hash, chunk_size) in one statement; the triggers count every row."""
        ...

    def chunk_bytes_total(self, conn: Conn) -> int:
        """Sum of size_bytes over ALL chunk rows (referenced, pending and awaiting reclamation): the cloud quota's usage figure."""
        ...

    def gc_candidates(self, conn: Conn, older_than_seconds: float, limit: int) -> list[ChunkRecord]:
        """Chunk rows nobody references (ref_count 0 AND no manifest row) at least ``older_than_seconds`` old, oldest first."""
        ...

    # -- cloud coordination (PostgreSQL only; the SQLite implementation raises NotImplementedError) -------------------

    def reserve_chunks(self, conn: Conn, items: list[tuple[str, int]], cap_bytes: int) -> list[str]:
        """Register the missing rows as 'pending' ATOMICALLY with the quota check, inside the caller's transaction.

        A transaction-scoped advisory lock serialises every reservation, then usage is recomputed from the committed rows, so two
        reservations can never both pass the check -- with or without the session-level writer lock. Raises ``QuotaConflict``,
        ``ChunkSizeConflict`` or ``ChunkReclaiming`` (a row is 'deleting'). Returns the hashes created."""
        ...

    def lock_chunks_for_reference(self, conn: Conn, hashes: list[str]) -> list[str]:
        """Take a row lock (FOR NO KEY UPDATE, in hash order) on the given chunk rows that are 'pending' or 'stored' and return those hashes.

        Called inside the transaction that inserts a manifest: a concurrent garbage-collection claim (an UPDATE of the same rows)
        then either waits for this transaction -- and finds the chunks referenced -- or has already marked a row 'deleting', which
        is then missing from the result so the caller aborts."""
        ...

    def claim_chunks_for_deletion(self, conn: Conn, older_than_seconds: float, limit: int) -> list[ChunkRecord]:
        """Atomically mark up to ``limit`` unreferenced rows 'deleting' (rows already 'deleting' are returned again, first).
        Candidates: ref_count 0 AND no manifest row AND (already 'deleting' OR at least ``older_than_seconds`` old)."""
        ...

    def release_deleted_chunks(self, conn: Conn, hashes: list[str]) -> list[str]:
        """Delete the 'deleting' rows among ``hashes`` that are still unreferenced; returns those removed."""
        ...

    def complete_file(self, conn: Conn, file_id: str, sha256: str) -> bool:
        """Mark a file 'complete' only if it is still 'uploading' (False otherwise: e.g. it was failed by reconciliation)."""
        ...

    def claim_pending_chunks(self, conn: Conn, hashes: list[str]) -> list[ChunkRecord]:
        """Mark 'deleting' exactly the given rows that are still 'pending', unreferenced (ref_count 0, no manifest row).

        Used by a FAILED upload to give back the reservations it made itself: only rows it created and never turned into 'stored'.
        Rows locked in hash order; the guard is re-checked after any lock wait, so a row that gained a reference is skipped."""
        ...
