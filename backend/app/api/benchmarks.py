"""Benchmark endpoints. Benchmarks run in a separate, bounded worker process against a private
temporary store; the request returns immediately with a job id (202) and is polled."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from app.benchmarking import datasets
from app.benchmarking.interfaces import (
    MAX_BYTES_PROCESSED, MAX_ITERATIONS, MAX_RUNTIME_SECONDS, MAX_SIZE, MIN_SIZE, WORKLOADS, validate_spec,
)
from app.benchmarking.jobs import BenchmarkJobs
from app.benchmarking.runner import METHODOLOGY
from app.schemas.benchmark import (
    BenchmarkList, BenchmarkRequest, JobStarted, JobStatus, ResultSummary, WorkloadCatalog, WorkloadInfo,
)
from app.schemas.common import ErrorResponse

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])

JobId = Annotated[str, Path(pattern=r"^[0-9a-f]{12}$")]
_ERR = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


def jobs_of(request: Request) -> BenchmarkJobs:
    return request.app.state.benchmarks


@router.get("/workloads", response_model=WorkloadCatalog, summary="Workloads, limits and methodology")
def workloads():
    cases = datasets.comparison_cases(MIN_SIZE, 1)
    return WorkloadCatalog(
        workloads=[WorkloadInfo(id=k, description=v[0], uploads=v[1], preset_size_bytes=v[2]) for k, v in WORKLOADS.items()],
        comparison_cases=[{"id": c["id"], "title": c["title"], "note": c["note"]} for c in cases],
        limits={"min_size_bytes": MIN_SIZE, "max_size_bytes": MAX_SIZE, "max_iterations": MAX_ITERATIONS,
                "max_bytes_processed": MAX_BYTES_PROCESSED, "max_runtime_seconds": MAX_RUNTIME_SECONDS},
        methodology=METHODOLOGY)


@router.post("", response_model=JobStarted, status_code=202, responses=_ERR, summary="Start a bounded benchmark (returns immediately)")
def start(body: BenchmarkRequest, request: Request):
    spec = validate_spec(body.kind, body.workload, body.size_bytes, body.iterations, body.seed, body.fixed_chunk_bytes)
    return JobStarted(job_id=jobs_of(request).start(spec))


@router.get("", response_model=BenchmarkList, summary="The running benchmark (if any) and saved results, newest first")
def list_benchmarks(request: Request):
    jobs = jobs_of(request)
    return BenchmarkList(running=jobs.current(), results=[ResultSummary(**r) for r in jobs.list_results()])


@router.get("/{job_id}", response_model=JobStatus, responses=_ERR, summary="Progress or the full result of one benchmark")
def status(job_id: JobId, request: Request):
    return JobStatus(**jobs_of(request).status(job_id))


@router.post("/{job_id}/cancel", response_model=JobStatus, responses=_ERR, summary="Cancel the running benchmark")
def cancel(job_id: JobId, request: Request):
    return JobStatus(**jobs_of(request).cancel(job_id))
