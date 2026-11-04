"""PLANNED contracts for integrity checks, benchmarking and capacity simulation.

Not implemented in Phase 1; no route uses these models yet.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class IntegrityCheckRequest(BaseModel):
    """POST /api/integrity/checks. Omit ``file_id`` to audit the whole store."""

    file_id: str | None = None
    verify_chunk_bytes: bool = Field(default=True, description="Re-hash stored bytes, not just check presence.")


class IntegrityFinding(BaseModel):
    kind: Literal[
        "missing_chunk_file",
        "corrupt_chunk",
        "orphan_chunk_file",
        "orphan_staging_file",
        "ref_count_mismatch",
        "file_hash_mismatch",
        "stuck_operation",
    ]
    subject: str = Field(description="Chunk hash, file id, or path the finding is about.")
    detail: str


class IntegrityCheckResponse(BaseModel):
    operation_id: int
    chunks_checked: int = Field(ge=0)
    findings: list[IntegrityFinding]
    healthy: bool


class BenchmarkRequest(BaseModel):
    """POST /api/benchmarks. Runs against caller-supplied or generated datasets."""

    dataset: Literal["uploaded_files", "generated_random", "generated_shifted"]
    size_bytes: int = Field(gt=0, le=1024**3)
    repetitions: int = Field(default=3, ge=1, le=20)


class BenchmarkResponse(BaseModel):
    operation_id: int
    chunks_per_second: float = Field(ge=0)
    throughput_mib_per_second: float = Field(ge=0)
    dedup_ratio: float = Field(ge=0)
    note: str = "Measured on this machine; not a distributed-system benchmark."


class SimulationRequest(BaseModel):
    """POST /api/simulations. Analytical capacity model, not a measurement."""

    logical_data_tb: float = Field(gt=0)
    assumed_dedup_ratio: float = Field(ge=1.0)
    avg_chunk_size_bytes: int = Field(gt=0)
    metadata_bytes_per_chunk: int = Field(default=100, gt=0)
    growth_rate_per_month: float = Field(default=0.0, ge=0.0)
    months: int = Field(default=12, ge=1, le=120)

    @model_validator(mode="after")
    def _growth_is_a_fraction(self) -> SimulationRequest:
        if self.growth_rate_per_month > 10:
            raise ValueError("growth_rate_per_month is a fraction (0.05 = 5%), not a percentage")
        return self


class SimulationResponse(BaseModel):
    physical_data_tb: float
    chunk_count: int
    metadata_gb: float
    assumptions: SimulationRequest
    note: str = "Model output derived from the stated assumptions; not measured data."
