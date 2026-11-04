"""Request/response schemas for the file, chunk and statistics endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Sha256Hex = Field(pattern=r"^[0-9a-f]{64}$", description="Lowercase hex SHA-256.")


class UploadChunkOutcome(BaseModel):
    chunk_index: int = Field(ge=0)
    chunk_hash: str = Sha256Hex
    chunk_size: int = Field(gt=0)
    decision: Literal["new", "reused", "adopted", "repeated_in_file"] = Field(
        description="new: bytes written by this upload; reused: an already-stored chunk; "
                    "adopted: valid orphan bytes from an interrupted upload; "
                    "repeated_in_file: an earlier position of this same upload used it.")


class UploadResponse(BaseModel):
    """POST /api/files -> 201."""

    file_id: str
    filename: str
    original_size: int = Field(ge=0)
    sha256: str = Sha256Hex
    chunk_count: int = Field(ge=0)
    new_chunks: int = Field(ge=0, description="Chunks whose bytes were written by this upload.")
    duplicate_chunks: int = Field(ge=0, description="Chunk occurrences that reused existing bytes (incl. repeats within the file).")
    bytes_written: int = Field(ge=0, description="Bytes actually added to the chunk store.")
    chunk_outcomes: list[UploadChunkOutcome] = Field(description="Per-position decisions taken by THIS upload (not persisted).")


class DeleteFileResponse(BaseModel):
    """DELETE /api/files/{file_id} -> 200."""

    file_id: str
    chunks_released: int = Field(ge=0, description="Manifest rows removed (each lowers a ref_count by one).")
    chunks_reclaimed: int = Field(ge=0, description="Chunk files physically deleted (ref_count reached 0).")
    bytes_reclaimed: int = Field(ge=0)
    cleanup_failed: int = Field(ge=0, description="Unreferenced chunk files that could not be removed; reconciliation removes them.")


class FileSummary(BaseModel):
    file_id: str
    filename: str
    original_size: int = Field(ge=0)
    sha256: str | None
    status: Literal["uploading", "complete", "failed", "deleting"]
    created_at: str
    chunk_count: int = Field(ge=0, description="Manifest rows (chunk occurrences) of this file.")


class FileListResponse(BaseModel):
    """GET /api/files -> 200 (newest first)."""

    items: list[FileSummary]
    total: int = Field(ge=0)
    limit: int
    offset: int


class FileChunkInfo(BaseModel):
    chunk_index: int = Field(ge=0)
    chunk_hash: str = Sha256Hex
    chunk_size: int = Field(gt=0)
    ref_count: int = Field(ge=0, description="How many manifest rows reference this chunk.")


class FileDetailResponse(FileSummary):
    """GET /api/files/{file_id} -> 200."""

    unique_chunk_count: int = Field(ge=0)
    chunks: list[FileChunkInfo]


class ChunkInfo(BaseModel):
    hash: str = Sha256Hex
    size_bytes: int = Field(gt=0)
    ref_count: int = Field(ge=0)
    state: Literal["pending", "stored", "corrupt"]
    created_at: str


class ChunkListResponse(BaseModel):
    """GET /api/chunks -> 200. No filesystem paths are exposed."""

    items: list[ChunkInfo]
    total: int = Field(ge=0)
    limit: int
    offset: int


class StorageStatsResponse(BaseModel):
    """GET /api/storage/stats -> 200. Every value is computed from real rows/files."""

    total_files: int
    logical_bytes: int
    unique_chunks: int
    unique_chunk_bytes: int
    total_chunk_references: int
    reused_chunk_references: int
    unreferenced_chunks: int
    unreferenced_chunk_bytes: int
    storage_savings_percent: float | None = Field(
        description="100*(logical-unique)/logical; null when logical_bytes is 0.")
    deduplication_ratio: float | None = Field(
        description="logical/unique; null when unique_chunk_bytes is 0.")
    database_bytes: int
    chunk_files: int
    chunk_store_bytes: int = Field(description="Sum of chunk file sizes; excludes filesystem block overhead.")
    staging_files: int
    staging_bytes: int
