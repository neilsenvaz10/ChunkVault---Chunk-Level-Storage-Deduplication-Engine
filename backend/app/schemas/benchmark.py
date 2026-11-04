from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class BenchmarkRequest(BaseModel):
    """POST /api/benchmarks. Bounded: size 16 KiB-4 MiB, 1-10 iterations, a total processing budget."""

    kind: Literal["pipeline", "chunking_comparison"] = "pipeline"
    workload: str | None = Field(default=None, description="Required for 'pipeline' (see GET /api/benchmarks/workloads).")
    size_bytes: int | None = Field(default=None, ge=16 * 1024, le=4 * 1024 * 1024)
    iterations: int = Field(default=3, ge=1, le=10)
    seed: int = Field(default=1, ge=0, le=2**31 - 1)
    fixed_chunk_bytes: int | None = Field(default=None, ge=1024, le=128 * 1024)


class JobStarted(BaseModel):
    job_id: str
    status: Literal["running"] = "running"


class WorkloadInfo(BaseModel):
    id: str
    description: str
    uploads: int
    preset_size_bytes: int | None


class WorkloadCatalog(BaseModel):
    workloads: list[WorkloadInfo]
    comparison_cases: list[dict[str, str]]
    limits: dict[str, int]
    methodology: list[str]


class JobStatus(BaseModel):
    job_id: str
    status: Literal["running", "done", "failed", "cancelled"]
    progress: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class ResultSummary(BaseModel):
    job_id: str
    kind: str
    spec: dict[str, Any]
    finished_at: str
    upload_throughput_mib_s: float | None = None
    restore_throughput_mib_s: float | None = None
    dedup_ratio: float | None = None
    savings_percent: float | None = None
    total_execution_seconds: float | None = None


class BenchmarkList(BaseModel):
    running: dict[str, Any] | None
    results: list[ResultSummary]
