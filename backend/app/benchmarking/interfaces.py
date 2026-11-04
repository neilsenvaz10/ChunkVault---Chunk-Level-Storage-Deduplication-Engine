"""Benchmark runner: interface only (Phase 6).

Results are measurements taken on the local machine and must be stored under
data/benchmarks/ with the parameters that produced them. Nothing is hard-coded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.core.errors import ChunkVaultError


class BenchmarkError(ChunkVaultError):
    code = "benchmark_failed"
    status_code = 500


@dataclass(frozen=True)
class BenchmarkSpec:
    dataset: Literal["uploaded_files", "generated_random", "generated_shifted"]
    size_bytes: int
    repetitions: int
    seed: int | None = None  # generated datasets must be reproducible


@dataclass(frozen=True)
class BenchmarkResult:
    spec: BenchmarkSpec
    chunks_per_second: float
    throughput_mib_per_second: float
    dedup_ratio: float
    result_path: str  # JSON file under data/benchmarks/


class BenchmarkRunner(Protocol):
    def run(self, spec: BenchmarkSpec) -> BenchmarkResult:
        """Run the pipeline against an isolated store (never the user's real store).

        Raises ``BenchmarkError`` for an invalid spec or an I/O failure. On
        failure no result file is written. Timing uses a monotonic clock.
        """
        ...
