from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SimulationRequest(BaseModel):
    """POST /api/simulations. A mathematical model driven by stated assumptions (pure computation)."""

    file_count: int = Field(default=100_000, ge=1, le=10**12)
    avg_file_size_bytes: int = Field(default=5 * 1024 * 1024, ge=1, le=10**12)
    avg_chunks_per_file: float | None = Field(
        default=None, gt=0, le=10**9, description="Default: ceil(average file size / 16 KiB).")
    duplicate_percent: float = Field(default=30.0, ge=0, le=100, description="Share of chunk references that repeat existing content.")
    metadata_bytes_per_file: int = Field(default=300, ge=0, le=10**6)
    metadata_bytes_per_unique_chunk: int = Field(default=150, ge=0, le=10**6)
    metadata_bytes_per_manifest_row: int = Field(default=80, ge=0, le=10**6)
    monthly_growth_percent: float = Field(default=5.0, ge=0, le=50)
    retention_months: int = Field(default=0, ge=0, le=120, description="0 keeps every file.")
    horizon_months: int = Field(default=24, ge=1, le=120)


class SimulationResponse(BaseModel):
    model: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    formulas: list[str]
    assumptions: list[str]
    file_count_sweep: list[dict[str, Any]]
    dedup_sweep: list[dict[str, Any]]
    growth: list[dict[str, Any]]
    growth_summary: dict[str, Any]
