"""Benchmark reporting additions: seconds/MiB, aggregate on-disk footprint ratio, and the cache-state declaration.

These are calculations on values the benchmark really measured. Results saved before these fields existed are never
rewritten or back-filled.
"""

import json
import statistics

import pytest

from app.benchmarking.interfaces import MIB, BenchmarkSpec
from app.benchmarking.runner import (
    CONDITIONS, FOOTPRINT_DEFINITION, METHODOLOGY, footprint_figures, run_benchmark, run_chunking_comparison,
    run_pipeline_iteration, seconds_per_mib,
)
from app.core.config import KIB, CDCConfig
from tests.test_benchmarks import CDC, NOPROGRESS, spec

# ---------------------------------------------------------------- the formulas


@pytest.mark.parametrize("nbytes,seconds,expected", [
    (MIB, 2.0, 2.0),
    (512 * KIB, 1.0, 2.0),          # half a MiB in one second is two seconds per MiB
    (4 * MIB, 1.0, 0.25),
    (3 * MIB, 1.5, 0.5),
])
def test_seconds_per_mib_is_seconds_divided_by_mebibytes(nbytes, seconds, expected):
    assert seconds_per_mib(nbytes, seconds) == pytest.approx(expected)


@pytest.mark.parametrize("nbytes,seconds", [(0, 1.0), (MIB, 0), (MIB, -1.0), (None, 1.0), (MIB, None)])
def test_seconds_per_mib_is_none_when_it_cannot_be_computed(nbytes, seconds):
    assert seconds_per_mib(nbytes, seconds) is None


def test_seconds_per_mib_is_the_reciprocal_of_throughput():
    nbytes, seconds = 7 * MIB + 123, 3.3
    throughput = (nbytes / MIB) / seconds
    assert seconds_per_mib(nbytes, seconds) == pytest.approx(1 / throughput)


def test_footprint_figures_use_the_stated_numerator_and_denominator():
    f = footprint_figures(logical_bytes=1000, chunk_store_bytes=400, database_bytes=200)
    assert f["footprint_bytes"] == 600  # chunk files + SQLite file
    assert f["footprint_ratio"] == pytest.approx(1000 / 600)  # logical / footprint
    assert f["footprint_savings_percent"] == pytest.approx(100 * (1000 - 600) / 1000)


def test_footprint_savings_can_be_negative_when_overhead_exceeds_reuse():
    f = footprint_figures(logical_bytes=1000, chunk_store_bytes=1000, database_bytes=100)
    assert f["footprint_ratio"] < 1 and f["footprint_savings_percent"] == pytest.approx(-10.0)


def test_footprint_figures_handle_degenerate_inputs():
    assert footprint_figures(0, 0, 0) == {"footprint_bytes": 0, "footprint_ratio": None, "footprint_savings_percent": None}
    assert footprint_figures(0, 10, 10)["footprint_savings_percent"] is None
    assert footprint_figures(500, 0, 0)["footprint_ratio"] is None


def test_the_definition_text_names_both_sides_and_what_is_excluded():
    d = FOOTPRINT_DEFINITION
    for part in ("logical bytes", "SQLite database file", "chunk files", "block rounding", "-wal", "staging"):
        assert part in d
    assert FOOTPRINT_DEFINITION in METHODOLOGY


# ---------------------------------------------------------------- recorded on real measurements

@pytest.fixture(scope="module")
def run():
    return run_pipeline_iteration(spec(workload="identical", size_bytes=64 * KIB), CDC, 1)


def test_every_measured_operation_carries_seconds_per_mib(run):
    for op in run["uploads"] + run["restores"]:
        assert op["seconds_per_mib"] == pytest.approx(op["total_seconds"] / (op["size_bytes"] / MIB))
        assert op["seconds_per_mib"] == pytest.approx(1 / op["throughput_mib_s"])


def test_the_footprint_is_built_from_the_measured_components(run):
    s = run["storage"]
    assert s["footprint_bytes"] == s["chunk_store_bytes"] + s["database_bytes"]
    assert s["footprint_ratio"] == pytest.approx(s["logical_bytes"] / s["footprint_bytes"])
    assert s["footprint_savings_percent"] == pytest.approx(100 * (s["logical_bytes"] - s["footprint_bytes"]) / s["logical_bytes"])
    # a small file set is dominated by the (page-granular) database: the footprint ratio is below the payload ratio,
    # which is exactly the difference the two figures are meant to expose
    assert s["footprint_ratio"] < s["dedup_ratio"]


def test_summary_aggregates_the_new_figures_across_iterations():
    out = run_benchmark(spec(workload="identical", size_bytes=32 * KIB, iterations=2), CDC, NOPROGRESS)
    s = out["summary"]
    for key in ("upload_seconds_per_mib", "restore_seconds_per_mib", "footprint_ratio", "footprint_savings_percent"):
        assert s[key]["n"] == 2 and s[key]["median"] is not None, key
    its = out["iterations"]
    ups = [sum(u["total_seconds"] for u in it["uploads"]) / (sum(u["size_bytes"] for u in it["uploads"]) / MIB) for it in its]
    assert s["upload_seconds_per_mib"]["min"] == pytest.approx(min(ups))
    assert s["upload_seconds_per_mib"]["max"] == pytest.approx(max(ups))
    assert s["footprint_ratio"]["median"] == pytest.approx(statistics.median(i["storage"]["footprint_ratio"] for i in its))


def test_chunking_comparison_records_seconds_per_mib():
    out = run_chunking_comparison(spec(kind="chunking_comparison", workload=None, size_bytes=96 * KIB, iterations=2, seed=4),
                                  CDC, NOPROGRESS)
    for case in out["cases"]:
        for scheme in ("cdc", "fixed"):
            r = case[scheme]
            assert r["processing_seconds_per_mib"]["n"] == 2
            lo = r["processing_seconds"]["min"] / (r["logical_bytes"] / MIB)
            hi = r["processing_seconds"]["max"] / (r["logical_bytes"] / MIB)
            assert r["processing_seconds_per_mib"]["min"] == pytest.approx(lo)
            assert r["processing_seconds_per_mib"]["max"] == pytest.approx(hi)


# ---------------------------------------------------------------- cache state

def test_cache_state_is_declared_unknown_and_never_described_as_cold_or_warm():
    assert CONDITIONS["cache_state"] == "unknown"
    out = run_benchmark(spec(workload="identical", size_bytes=16 * KIB), CDC, NOPROGRESS)
    assert out["conditions"] == CONDITIONS
    text = " ".join(out["methodology"]).lower()
    assert "cache state is unknown" in text
    assert "first iteration may include cold caches" not in text  # the old, unmeasured claim is gone


# ---------------------------------------------------------------- old results are untouched

def test_results_saved_before_these_fields_are_served_unchanged(client, settings):
    old = {
        "job_id": "abcdef012345", "status": "done", "kind": "pipeline", "finished_at": "2026-11-01T00:00:00+00:00",
        "spec": {"kind": "pipeline", "workload": "identical", "size_bytes": 131072, "iterations": 3, "seed": 1,
                 "fixed_chunk_bytes": None},
        "iterations": [], "environment": {"python": "3.14"}, "methodology": ["old text"],
        "summary": {"upload_throughput_mib_s": {"median": 2.0}, "restore_throughput_mib_s": {"median": 20.0},
                    "dedup_ratio": {"median": 2.0}, "savings_percent": {"median": 50.0}, "total_execution_seconds": 1.0},
    }
    settings.benchmarks_dir.mkdir(parents=True, exist_ok=True)
    path = settings.benchmarks_dir / "abcdef012345.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    got = client.get("/api/benchmarks/abcdef012345").json()["result"]
    assert got == old  # nothing back-filled, nothing recomputed
    assert "conditions" not in got and "upload_seconds_per_mib" not in got["summary"]
    assert json.loads(path.read_text(encoding="utf-8")) == old
    assert [r["job_id"] for r in client.get("/api/benchmarks").json()["results"]] == ["abcdef012345"]
