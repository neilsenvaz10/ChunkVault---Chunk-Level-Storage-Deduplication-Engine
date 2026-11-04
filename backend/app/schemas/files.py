"""PLANNED contracts for upload, restore, deletion and metadata inspection.

These models define the request/response shapes for endpoints that are NOT
implemented in Phase 1. No route uses them yet. See docs/api/planned-contracts.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Sha256Hex = Field(pattern=r"^[0-9a-f]{64}$", description="Lowercase hex SHA-256.")


class UploadResponse(BaseModel):
    """POST /api/files (multipart upload) -> 201."""

    file_id: str
    filename: str
    original_size: int = Field(ge=0)
    sha256: str = Sha256Hex
    chunk_count: int = Field(ge=0)
    new_chunks: int = Field(ge=0, description="Chunks whose bytes were written by this upload.")
    duplicate_chunks: int = Field(ge=0, description="Chunks already stored (incl. repeats within the file).")
    bytes_written: int = Field(ge=0, description="Bytes actually added to the chunk store.")


class RestoreHeaders(BaseModel):
    """GET /api/files/{file_id}/content streams raw bytes (200). Response headers:"""

    content_disposition: str
    x_chunkvault_sha256: str = Sha256Hex


class DeleteFileResponse(BaseModel):
    """DELETE /api/files/{file_id} -> 200."""

    file_id: str
    chunks_released: int = Field(ge=0, description="file_chunks rows removed (ref_count decrements).")
    chunks_reclaimed: int = Field(ge=0, description="Chunk files physically deleted (ref_count reached 0).")
    bytes_reclaimed: int = Field(ge=0)


class FileSummary(BaseModel):
    file_id: str
    filename: str
    original_size: int = Field(ge=0)
    sha256: str | None
    status: Literal["uploading", "complete", "failed", "deleting"]
    created_at: str


class FileListResponse(BaseModel):
    """GET /api/files -> 200."""

    items: list[FileSummary]
    total: int = Field(ge=0)


class FileChunkInfo(BaseModel):
    chunk_index: int = Field(ge=0)
    chunk_hash: str = Sha256Hex
    chunk_size: int = Field(gt=0)
    ref_count: int = Field(ge=0, description="How many file_chunks rows reference this chunk.")


class FileDetailResponse(FileSummary):
    """GET /api/files/{file_id} -> 200."""

    chunks: list[FileChunkInfo]
