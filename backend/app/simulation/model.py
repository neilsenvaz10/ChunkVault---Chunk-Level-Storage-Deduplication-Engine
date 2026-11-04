"""Capacity and scalability model.

THIS IS A MATHEMATICAL MODEL, NOT A MEASUREMENT AND NOT A LOAD TEST. Every output is a pure function
of the stated assumptions: nothing is read from the database, the chunk store or a benchmark, and
nothing is executed. It exists to show how storage and metadata scale if the assumptions hold.

Definitions (N files, S average file size, C average chunks per file, d duplicate fraction):
  chunk references   R  = N * C                     (manifest rows)
  unique chunks      U  = R * (1 - d)
  average chunk size a  = S / C
  logical storage    L  = N * S
  physical chunks    P  = U * a                     (= L * (1 - d))
  storage savings       = (L - P) / L               (= d)
  dedup ratio           = L / P                     (undefined when P = 0)
  metadata           M  = N*Mf + R*Mr + U*Mc        (files rows + manifest rows + chunk rows)
  total disk            = P + M                     (excludes filesystem block overhead)
Growth: ingested(t) = N * (1+g)^t files in total after t months; with retention of T months the
files ingested at or before month t-T have expired, so active(t) = ingested(t) - ingested(t-T).
The duplicate fraction d is assumed constant for the active set.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_CHUNK_TARGET = 16 * 1024

FORMULAS = [
    "chunk references R = files x chunks per file",
    "unique chunks U = R x (1 - duplicate fraction d)",
    "average chunk size a = average file size / chunks per file",
    "logical storage L = files x average file size",
    "physical chunk storage P = U x a  (equals L x (1 - d))",
    "storage savings = (L - P) / L  (equals d)",
    "deduplication ratio = L / P  (undefined if P = 0)",
    "metadata M = files x per-file + R x per-manifest-row + U x per-unique-chunk",
    "total disk = P + M  (no filesystem block overhead)",
    "ingested(t) = files x (1 + monthly growth)^t ;  active(t) = ingested(t) - ingested(t - retention)",
]

ASSUMPTIONS = [
    "The duplicate fraction applies to chunk references and stays constant as data grows.",
    "Average chunk size is file size divided by chunks per file; real chunk sizes vary around it.",
    "Metadata byte sizes are user-supplied estimates, not measured values; the Performance page measures the real database growth on this machine.",
    "Growth is compound and monthly; retention deletes whole months of the oldest files; reclamation of unshared chunks is assumed perfect and immediate.",
    "No filesystem block rounding, SQLite page slack, write amplification, backups or replicas are modelled.",
    "Hashing, CDC CPU cost and lookup latency are not modelled; see the limitations section for why they matter.",
]


@dataclass(frozen=True)
class SimulationInput:
    file_count: int
    avg_file_size_bytes: int
    avg_chunks_per_file: float | None
    duplicate_percent: float
    metadata_bytes_per_file: int
    metadata_bytes_per_unique_chunk: int
    metadata_bytes_per_manifest_row: int
    monthly_growth_percent: float
    retention_months: int
    horizon_months: int


def _point(n: float, s: float, c: float, d: float, mf: float, mr: float, mc: float) -> dict:
    refs = n * c
    unique = refs * (1.0 - d)
    logical = n * s
    physical = unique * (s / c)
    meta = n * mf + refs * mr + unique * mc
    return {"files": n, "logical_bytes": logical, "physical_bytes": physical, "metadata_bytes": meta,
            "total_bytes": physical + meta, "chunk_references": refs, "unique_chunks": unique,
            "savings_percent": 100.0 * (logical - physical) / logical if logical else None,
            "dedup_ratio": (logical / physical) if physical > 0 else None}


def simulate(p: SimulationInput) -> dict:
    c = p.avg_chunks_per_file if p.avg_chunks_per_file is not None else max(1.0, math.ceil(p.avg_file_size_bytes / DEFAULT_CHUNK_TARGET))
    d = p.duplicate_percent / 100.0
    n, s = float(p.file_count), float(p.avg_file_size_bytes)
    mf, mr, mc = float(p.metadata_bytes_per_file), float(p.metadata_bytes_per_manifest_row), float(p.metadata_bytes_per_unique_chunk)
    base = _point(n, s, c, d, mf, mr, mc)

    result = {
        "model": "mathematical model - not a measurement or load test",
        "inputs": {**p.__dict__, "avg_chunks_per_file_used": c, "duplicate_fraction": d},
        "outputs": {
            "avg_chunk_bytes": s / c, "chunk_references": base["chunk_references"], "unique_chunks": base["unique_chunks"],
            "logical_bytes": base["logical_bytes"], "physical_chunk_bytes": base["physical_bytes"],
            "savings_percent": base["savings_percent"], "dedup_ratio": base["dedup_ratio"],
            "metadata_bytes": base["metadata_bytes"],
            "metadata_breakdown": {"files": n * mf, "manifest_rows": base["chunk_references"] * mr,
                                   "unique_chunks": base["unique_chunks"] * mc},
            "total_disk_bytes": base["total_bytes"],
            "metadata_percent_of_total": (100.0 * base["metadata_bytes"] / base["total_bytes"]) if base["total_bytes"] else None,
        },
        "formulas": FORMULAS, "assumptions": ASSUMPTIONS,
    }

    # file count vs storage / metadata (10 evenly spaced counts up to the input)
    result["file_count_sweep"] = [_point(n * k / 10.0, s, c, d, mf, mr, mc) for k in range(1, 11)]
    # duplicate rate vs savings at the input file count
    result["dedup_sweep"] = [{"duplicate_percent": pct, **_point(n, s, c, pct / 100.0, mf, mr, mc)} for pct in range(0, 101, 10)]

    # growth over time with optional retention
    g = p.monthly_growth_percent / 100.0
    T = p.retention_months

    def ingested(x: int) -> float:
        return n * (1.0 + g) ** x if x >= 0 else 0.0

    growth = []
    for t in range(0, p.horizon_months + 1):
        active = ingested(t) - (ingested(t - T) if T > 0 and t >= T else 0.0)
        growth.append({"month": t, **_point(active, s, c, d, mf, mr, mc)})
    result["growth"] = growth
    result["growth_summary"] = {"months": p.horizon_months, "retention_months": T, "final_active_files": growth[-1]["files"],
                                "final_total_bytes": growth[-1]["total_bytes"],
                                "peak_total_bytes": max(g_["total_bytes"] for g_ in growth)}
    return result
