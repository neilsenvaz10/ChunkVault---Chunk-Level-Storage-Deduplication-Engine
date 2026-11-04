"""Benchmark specification and validation.

Benchmarks run in a separate worker process against a private temporary store (never the
application's store) and are bounded in size, iterations and wall-clock time. Results are
measurements taken on the local machine and are persisted under data/benchmarks/ together with the
parameters that produced them. Nothing is hard-coded.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import KIB
from app.core.errors import ChunkVaultError

MIB = 1024 * KIB
MIN_SIZE = 16 * KIB
MAX_SIZE = 4 * MIB
MAX_ITERATIONS = 10
MAX_SEED = 2**31 - 1
MAX_BYTES_PROCESSED = 48 * MIB  # budget: file size x uploads x iterations
MAX_RUNTIME_SECONDS = 240

KINDS = ("pipeline", "chunking_comparison")

# workload -> (description, number of uploaded versions, preset size or None)
WORKLOADS: dict[str, tuple[str, int, int | None]] = {
    "small": ("One small random file", 1, 64 * KIB),
    "medium": ("One medium random file", 1, 512 * KIB),
    "large": ("One large random file (bounded by the safe resource limit)", 1, 2 * MIB),
    "identical": ("The same file uploaded twice", 2, None),
    "partial_modified": ("A file, then a copy with a small in-place modification in the middle", 2, None),
    "repeated_content": ("One file made of a repeating block", 1, None),
    "random": ("One random file (no duplicate content)", 1, None),
    "inserted_bytes": ("A file, then a copy with bytes inserted near the beginning", 2, None),
    "deleted_bytes": ("A file, then a copy with bytes deleted near the beginning", 2, None),
}
DEFAULT_SIZE = 512 * KIB


class BenchmarkError(ChunkVaultError):
    code = "benchmark_failed"
    status_code = 500


class InvalidBenchmarkSpec(ChunkVaultError):
    code = "invalid_benchmark"
    status_code = 422


@dataclass(frozen=True)
class BenchmarkSpec:
    kind: str
    workload: str | None
    size_bytes: int
    iterations: int
    seed: int
    fixed_chunk_bytes: int | None = None  # chunking_comparison only; default = CDC target size


def effective_size(workload: str | None, size_bytes: int | None) -> int:
    if size_bytes is not None:
        return size_bytes
    preset = WORKLOADS[workload][2] if workload in WORKLOADS else None
    return preset or DEFAULT_SIZE


def validate_spec(kind: str, workload: str | None, size_bytes: int | None, iterations: int, seed: int,
                  fixed_chunk_bytes: int | None = None) -> BenchmarkSpec:
    """Raise InvalidBenchmarkSpec for anything unbounded or inconsistent."""
    if kind not in KINDS:
        raise InvalidBenchmarkSpec(f"kind must be one of {', '.join(KINDS)}")
    if kind == "pipeline" and workload not in WORKLOADS:
        raise InvalidBenchmarkSpec(f"workload must be one of {', '.join(WORKLOADS)}")
    if kind == "chunking_comparison" and workload is not None:
        raise InvalidBenchmarkSpec("chunking_comparison does not take a workload")
    size = effective_size(workload, size_bytes)
    if not MIN_SIZE <= size <= MAX_SIZE:
        raise InvalidBenchmarkSpec(f"size_bytes must be between {MIN_SIZE} and {MAX_SIZE}")
    if not 1 <= iterations <= MAX_ITERATIONS:
        raise InvalidBenchmarkSpec(f"iterations must be between 1 and {MAX_ITERATIONS}")
    if not 0 <= seed <= MAX_SEED:
        raise InvalidBenchmarkSpec(f"seed must be between 0 and {MAX_SEED}")
    if fixed_chunk_bytes is not None:
        if kind != "chunking_comparison":
            raise InvalidBenchmarkSpec("fixed_chunk_bytes only applies to chunking_comparison")
        if not KIB <= fixed_chunk_bytes <= 128 * KIB:
            raise InvalidBenchmarkSpec("fixed_chunk_bytes must be between 1 KiB and 128 KiB")
    versions = WORKLOADS[workload][1] if kind == "pipeline" else 14  # 7 cases x 2 versions
    cost = size * versions * iterations * (1 if kind == "pipeline" else 2)  # comparison chunks with 2 schemes
    if cost > MAX_BYTES_PROCESSED * (2 if kind == "chunking_comparison" else 1):
        raise InvalidBenchmarkSpec(
            f"this combination would process about {cost // MIB} MiB; the limit is "
            f"{MAX_BYTES_PROCESSED // MIB * (2 if kind == 'chunking_comparison' else 1)} MiB. Reduce size or iterations")
    return BenchmarkSpec(kind, workload, size, iterations, seed, fixed_chunk_bytes)
