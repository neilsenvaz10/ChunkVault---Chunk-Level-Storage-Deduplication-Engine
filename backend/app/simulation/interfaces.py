"""Capacity simulation engine: interface only (Phase 7).

This is an analytical model driven by user-supplied assumptions. Its output is
labelled as model output, never as a measurement, and it makes no claim about
distributed deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.errors import ChunkVaultError


class SimulationError(ChunkVaultError):
    code = "simulation_invalid"
    status_code = 422


@dataclass(frozen=True)
class SimulationParameters:
    logical_data_tb: float
    assumed_dedup_ratio: float
    avg_chunk_size_bytes: int
    metadata_bytes_per_chunk: int
    growth_rate_per_month: float
    months: int


@dataclass(frozen=True)
class SimulationResult:
    parameters: SimulationParameters
    physical_data_tb: float
    chunk_count: int
    metadata_gb: float


class CapacitySimulator(Protocol):
    def simulate(self, params: SimulationParameters) -> SimulationResult:
        """Pure function of ``params``. Raises ``SimulationError`` for
        non-positive sizes or a dedup ratio below 1.0. Touches no files or database."""
        ...
