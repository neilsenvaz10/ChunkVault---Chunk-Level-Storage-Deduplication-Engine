"""Schemas for read-only system information and the chunking demonstration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CdcConfigInfo(BaseModel):
    min_chunk_size: int
    avg_chunk_size: int
    max_chunk_size: int
    window_size: int
    boundary_divisor: int = Field(description="D = avg - min; a boundary is declared when fingerprint mod D == D - 1.")
    boundary_remainder: int = Field(description="D - 1")
    polynomial: str = Field(description="Irreducible GF(2) polynomial of the rolling fingerprint, hex.")
    polynomial_degree: int


class SystemConfigResponse(BaseModel):
    """GET /api/system/config. The values the running server actually uses."""

    cdc: CdcConfigInfo
    max_upload_bytes: int
    chunk_path_layout: str = Field(description="Relative, content-addressed layout inside the chunk store.")


class DemoChunk(BaseModel):
    offset: int = Field(ge=0)
    size: int = Field(gt=0)
    reused: bool | None = Field(
        default=None,
        description="Modified input only: this chunk's SHA-256 also occurs among the original input's chunks.")


class SchemeResult(BaseModel):
    scheme: str
    original_chunks: list[DemoChunk]
    modified_chunks: list[DemoChunk]
    reused_chunks: int
    reused_bytes: int
    reuse_percent: float | None


class ChunkingDemoResponse(BaseModel):
    """GET /api/system/chunking-demo. Computed now by the implemented chunker on SEEDED GENERATED data.
    Nothing is read from or written to the chunk store or the database."""

    seed: int
    original_size: int
    modified_size: int
    insert_at: int
    insert_len: int
    fixed_chunk_size: int
    cdc: SchemeResult
    fixed: SchemeResult
    note: str
