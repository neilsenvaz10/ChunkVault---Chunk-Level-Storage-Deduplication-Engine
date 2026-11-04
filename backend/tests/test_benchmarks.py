"""Benchmark engine: datasets, validation, profiler, runner, comparison, jobs and API."""

import tempfile
import time
from pathlib import Path

import pytest

import app.deduplication.upload as upload_mod
import app.restoration.engine as restore_mod
from app.benchmarking import datasets
from app.benchmarking.interfaces import (
    DEFAULT_SIZE, MAX_BYTES_PROCESSED, MAX_SIZE, MIB, MIN_SIZE, WORKLOADS, BenchmarkSpec, InvalidBenchmarkSpec,
    effective_size, validate_spec,
)
from app.benchmarking.jobs import BenchmarkJobs
from app.benchmarking.profiler import Profiler, summarize
from app.benchmarking.runner import run_benchmark, run_chunking_comparison, run_pipeline_iteration
from app.core.config import KIB, CDCConfig
from tests.conftest import make_data
from tests.test_storage_api import err

CDC = CDCConfig()
NOPROGRESS = lambda *a: None  # noqa: E731


def spec(**kw):
    base = dict(kind="pipeline", workload="identical", size_bytes=32 * KIB, iterations=1, seed=5)
    base.update(kw)
    return BenchmarkSpec(base["kind"], base["workload"], base["size_bytes"], base["iterations"], base["seed"],
                         base.get("fixed_chunk_bytes"))


# ---- deterministic datasets -------------------------------------------------------------

def test_datasets_are_deterministic_and_seed_dependent():
    assert datasets.random_bytes(3, 5000) == datasets.random_bytes(3, 5000)
    assert datasets.random_bytes(3, 5000) != datasets.random_bytes(4, 5000)
    assert len(datasets.random_bytes(1, 12345)) == 12345
    for w in WORKLOADS:
        a = datasets.workload_versions(w, 40 * KIB, 9)
        b = datasets.workload_versions(w, 40 * KIB, 9)
        assert a == b and len(a) == WORKLOADS[w][1]


def test_dataset_edits_have_the_documented_shape():
    base = datasets.random_bytes(1, 50_000)
    ins = datasets.insert_near_start(base, 1)
    assert len(ins) == len(base) + datasets.EDIT_LEN and ins[:100] == base[:100] and ins[100 + datasets.EDIT_LEN:] == base[100:]
    dele = datasets.delete_near_start(base)
    assert len(dele) == len(base) - datasets.EDIT_LEN and dele[:100] == base[:100] and dele[100:] == base[100 + datasets.EDIT_LEN:]
    mod = datasets.modify_middle(base)
    assert len(mod) == len(base) and mod != base and sum(a != b for a, b in zip(base, mod)) == datasets.EDIT_LEN
    rep = datasets.repeated_bytes(1, 70_000)
    assert len(rep) == 70_000 and rep[:20_000] == rep[20_000:40_000]


def test_workload_versions_relationships():
    v = dict(datasets.workload_versions("identical", 20 * KIB, 1))
    assert v["v1"] == v["v2-identical"]
    assert len(datasets.workload_versions("random", 20 * KIB, 1)) == 1
    with pytest.raises(ValueError):
        datasets.workload_versions("nope", 20 * KIB, 1)


def test_comparison_cases_cover_the_seven_required_datasets():
    cases = datasets.comparison_cases(40 * KIB, 3)
    assert [c["id"] for c in cases] == ["original", "identical_duplicate", "insert_start", "delete_start",
                                        "modify_middle", "repeated_content", "random_content"]
    assert cases[1]["versions"][0] == cases[1]["versions"][1]


# ---- validation and bounds ----------------------------------------------------------------

def test_valid_specs_and_presets():
    s = validate_spec("pipeline", "small", None, 3, 1)
    assert s.size_bytes == 64 * KIB and effective_size("large", None) == 2 * MIB and effective_size("identical", None) == DEFAULT_SIZE
    assert validate_spec("chunking_comparison", None, 64 * KIB, 2, 1, 8 * KIB).fixed_chunk_bytes == 8 * KIB


@pytest.mark.parametrize("args", [
    ("nope", "small", None, 1, 1), ("pipeline", "nope", None, 1, 1), ("pipeline", None, None, 1, 1),
    ("pipeline", "small", MIN_SIZE - 1, 1, 1), ("pipeline", "small", MAX_SIZE + 1, 1, 1),
    ("pipeline", "small", None, 0, 1), ("pipeline", "small", None, 11, 1),
    ("pipeline", "small", None, 1, -1), ("pipeline", "small", None, 1, 2**31),
    ("chunking_comparison", "small", None, 1, 1), ("pipeline", "small", None, 1, 1, 4096),
    ("chunking_comparison", None, None, 1, 1, 100), ("chunking_comparison", None, None, 1, 1, 10**9),
])
def test_invalid_specs_are_rejected(args):
    with pytest.raises(InvalidBenchmarkSpec):
        validate_spec(*args)


def test_total_work_is_bounded():
    with pytest.raises(InvalidBenchmarkSpec, match="limit"):
        validate_spec("pipeline", "identical", MAX_SIZE, 10, 1)  # 4 MiB x 2 x 10
    assert MAX_BYTES_PROCESSED == 48 * MIB
    with pytest.raises(InvalidBenchmarkSpec, match="limit"):
        validate_spec("chunking_comparison", None, MAX_SIZE, 10, 1)
    validate_spec("pipeline", "large", None, 10, 1)  # 2 MiB x 10 = 20 MiB: allowed


# ---- profiler: exclusive timing, no double counting --------------------------------------------

def test_profiler_attributes_each_instant_to_exactly_one_stage():
    t = [0.0]
    p = Profiler(clock=lambda: t[0])
    with p.section("outer"):
        t[0] += 1.0
        with p.section("inner"):
            t[0] += 2.0
            with p.section("innermost"):
                t[0] += 0.5
            t[0] += 0.25
        t[0] += 4.0
    assert p.snapshot() == {"outer": 5.0, "inner": 2.25, "innermost": 0.5}
    assert sum(p.snapshot().values()) == t[0]  # stages sum to the wall time exactly
    assert p.current() is None


def test_profiler_current_and_reset():
    p = Profiler()
    with p.section("a"):
        assert p.current() == "a"
        with p.section("b"):
            assert p.current() == "b"
        assert p.current() == "a"
    p.reset()
    assert p.snapshot() == {}


def test_summary_statistics():
    s = summarize([1.0, 2.0, 3.0, 10.0])
    assert (s["n"], s["mean"], s["median"], s["min"], s["max"]) == (4, 4.0, 2.5, 1.0, 10.0)
    assert s["stdev"] == pytest.approx(4.08248290463863)
    assert summarize([7.0])["stdev"] is None
    assert summarize([])["mean"] is None


# ---- pipeline runner on the real services ------------------------------------------------------

@pytest.fixture(scope="module")
def identical_run():
    return run_pipeline_iteration(spec(workload="identical", size_bytes=64 * KIB), CDC, 1)


def test_pipeline_measures_real_uploads_and_restores(identical_run):
    r = identical_run
    assert [u["name"] for u in r["uploads"]] == ["v1", "v2-identical"]
    for u in r["uploads"]:
        assert u["total_seconds"] > 0 and u["size_bytes"] == 64 * KIB and u["sha256_matches_source"]
        assert u["throughput_mib_s"] == pytest.approx((u["size_bytes"] / MIB) / u["total_seconds"])
    assert all(x["integrity_verified"] for x in r["restores"])
    assert all(x["throughput_mib_s"] > 0 for x in r["restores"])


def test_stage_timings_never_double_count(identical_run):
    for rec in identical_run["uploads"] + identical_run["restores"]:
        stages = rec["stages_seconds"]
        assert all(v >= 0 for v in stages.values())
        assert sum(stages.values()) == pytest.approx(rec["total_seconds"], rel=1e-9, abs=1e-9)  # 'other' is the remainder
        assert sum(rec["stages_percent"].values()) == pytest.approx(100.0)
    up = identical_run["uploads"][0]["stages_seconds"]
    for stage in ("cdc", "metadata", "chunk_persistence", "upload_staging", "sha256"):
        assert up[stage] > 0
    assert identical_run["restores"][0]["stages_seconds"]["chunk_read"] > 0


def test_identical_upload_is_fully_deduplicated(identical_run):
    first, second = identical_run["uploads"]
    assert first["new_chunks"] == first["chunk_count"] and first["reused_chunks"] == 0
    assert second["new_chunks"] == 0 and second["reused_chunks"] == second["chunk_count"] and second["bytes_written"] == 0
    st = identical_run["storage"]
    assert st["logical_bytes"] == 2 * 64 * KIB and st["unique_chunk_bytes"] == 64 * KIB
    assert st["dedup_ratio"] == pytest.approx(2.0) and st["savings_percent"] == pytest.approx(50.0)
    assert st["chunks_generated"] == 2 * first["chunk_count"] and st["reused_chunks"] == first["chunk_count"]
    assert st["unique_chunks"] == first["chunk_count"] and st["manifest_rows"] == st["chunks_generated"]
    assert st["avg_chunk_bytes"] == pytest.approx(st["unique_chunk_bytes"] / st["unique_chunks"])
    assert st["database_bytes"] > 0 and st["database_overhead_bytes"] >= 0


@pytest.mark.parametrize("workload,expect_reuse", [
    ("random", False), ("repeated_content", None), ("partial_modified", True), ("inserted_bytes", True), ("deleted_bytes", True)])
def test_every_workload_runs_and_restores_exactly(workload, expect_reuse):
    r = run_pipeline_iteration(spec(workload=workload, size_bytes=96 * KIB), CDC, 1)
    assert all(x["integrity_verified"] for x in r["restores"]) and len(r["uploads"]) == WORKLOADS[workload][1]
    if expect_reuse is True:
        assert r["uploads"][1]["reused_chunks"] > 0 and r["storage"]["savings_percent"] > 0
    if expect_reuse is False:
        assert r["storage"]["savings_percent"] == 0


def test_instrumentation_is_removed_afterwards(identical_run):
    from app.core.services import build_services
    from app.database.init import initialize_database

    assert upload_mod.transaction.__module__ == "app.database.connection"
    assert upload_mod.connect.__module__ == "app.database.connection"
    assert restore_mod.transaction.__module__ == "app.database.connection"
    assert restore_mod.connect.__module__ == "app.database.connection"


def test_benchmark_runs_leave_no_temporary_stores():
    before = set(Path(tempfile.gettempdir()).glob("chunkvault-bench-*"))
    run_pipeline_iteration(spec(workload="small", size_bytes=16 * KIB), CDC, 1)
    assert set(Path(tempfile.gettempdir()).glob("chunkvault-bench-*")) == before


def test_summary_aggregates_iterations():
    out = run_benchmark(spec(workload="identical", size_bytes=32 * KIB, iterations=3), CDC, NOPROGRESS)
    assert len(out["iterations"]) == 3
    s = out["summary"]
    assert s["upload_total_seconds"]["n"] == 3 and s["integrity_all_verified"] is True
    assert s["dedup_ratio"]["median"] == pytest.approx(2.0)
    assert out["environment"]["cdc"]["avg_chunk_size"] == 16 * KIB and out["environment"]["python"]
    assert any("exclusive" in m for m in out["methodology"])
    # identical input every iteration: the work is the same, only the timing differs
    assert len({it["storage"]["chunks_generated"] for it in out["iterations"]}) == 1


def test_progress_is_reported_and_bounded():
    seen = []
    run_benchmark(spec(workload="small", size_bytes=16 * KIB, iterations=2), CDC, lambda *a: seen.append(a))
    assert [a[1] for a in seen][-1] == 2 and seen[0][1] == 0 and all(a[1] <= a[2] for a in seen)


# ---- CDC vs fixed-size ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def comparison():
    return run_chunking_comparison(spec(kind="chunking_comparison", workload=None, size_bytes=192 * KIB, iterations=1, seed=11),
                                   CDC, NOPROGRESS)


def test_comparison_uses_identical_data_for_both_schemes(comparison):
    assert [c["id"] for c in comparison["cases"]][:2] == ["original", "identical_duplicate"]
    for c in comparison["cases"]:
        assert c["cdc"]["logical_bytes"] == c["fixed"]["logical_bytes"] == sum(c["versions"])
        assert c["cdc"]["processing_seconds"]["n"] == c["fixed"]["processing_seconds"]["n"] == 1
        assert c["cdc"]["processing_seconds"]["median"] > 0


def test_comparison_metrics_are_internally_consistent(comparison):
    for c in comparison["cases"]:
        for scheme in ("cdc", "fixed"):
            r = c[scheme]
            assert r["unique_chunks"] + r["reused_chunks"] == r["chunk_count"]
            assert r["unique_bytes"] + r["reused_bytes"] == r["logical_bytes"]
            assert r["savings_percent"] == pytest.approx(100.0 * r["reused_bytes"] / r["logical_bytes"])
            assert r["avg_chunk_bytes"] == pytest.approx(r["logical_bytes"] / r["chunk_count"])


def by_id(comparison):
    return {c["id"]: c for c in comparison["cases"]}


def test_cdc_wins_after_insertions_and_deletions(comparison):
    c = by_id(comparison)
    for case in ("insert_start", "delete_start"):
        assert c[case]["cdc"]["savings_percent"] > 30 and c[case]["fixed"]["savings_percent"] == 0
        assert c[case]["verdict"] == "CDC saves more" and c[case]["savings_difference_points"] > 30


def test_cdc_has_no_advantage_on_unrelated_or_unshifted_data(comparison):
    c = by_id(comparison)
    assert c["random_content"]["cdc"]["savings_percent"] == 0 and c["random_content"]["fixed"]["savings_percent"] == 0
    assert c["random_content"]["verdict"] == "no meaningful difference"
    assert c["original"]["verdict"] == "no meaningful difference"
    assert c["identical_duplicate"]["cdc"]["savings_percent"] == pytest.approx(50) == pytest.approx(c["identical_duplicate"]["fixed"]["savings_percent"])
    # an in-place edit does not shift data: here the fixed-size scheme is NOT beaten (not hard-coded: derived from the numbers)
    m = c["modify_middle"]
    assert m["cdc"]["savings_percent"] <= m["fixed"]["savings_percent"] + 1.0
    assert m["verdict"] in ("fixed-size saves more", "no meaningful difference")


def test_verdicts_follow_the_measured_difference(comparison):
    for c in comparison["cases"]:
        d = c["cdc"]["savings_percent"] - c["fixed"]["savings_percent"]
        assert c["savings_difference_points"] == pytest.approx(d)
        assert c["verdict"] == ("CDC saves more" if d > 1 else "fixed-size saves more" if d < -1 else "no meaningful difference")


def test_comparison_is_deterministic_and_seed_sensitive():
    kw = dict(kind="chunking_comparison", workload=None, size_bytes=64 * KIB, iterations=1)

    def counts(seed):
        r = run_chunking_comparison(spec(seed=seed, **kw), CDC, NOPROGRESS)
        return [(c["cdc"]["chunk_count"], c["cdc"]["reused_bytes"], c["fixed"]["reused_bytes"]) for c in r["cases"]]

    assert counts(1) == counts(1)
    assert counts(1) != counts(2)


def test_fixed_chunk_size_parameter_is_honoured():
    r = run_chunking_comparison(spec(kind="chunking_comparison", workload=None, size_bytes=64 * KIB, iterations=1, fixed_chunk_bytes=8 * KIB),
                                CDC, NOPROGRESS)
    assert r["fixed_chunk_bytes"] == 8 * KIB
    assert by_id(r)["original"]["fixed"]["chunk_count"] == 8


# ---- background jobs and API -------------------------------------------------------------------------

def wait_done(client, job_id, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        s = client.get(f"/api/benchmarks/{job_id}").json()
        if s["status"] != "running":
            return s
        time.sleep(0.2)
    raise AssertionError("benchmark did not finish")


TINY = {"kind": "pipeline", "workload": "identical", "size_bytes": 16384, "iterations": 1, "seed": 3}


def test_catalog_describes_workloads_limits_and_methodology(client):
    c = client.get("/api/benchmarks/workloads").json()
    assert {w["id"] for w in c["workloads"]} == set(WORKLOADS)
    assert [x["id"] for x in c["comparison_cases"]][0] == "original" and len(c["comparison_cases"]) == 7
    assert c["limits"]["max_iterations"] == 10 and c["limits"]["max_runtime_seconds"] > 0 and c["methodology"]


def test_full_job_lifecycle_runs_in_a_worker_and_persists_the_result(client, settings):
    r = client.post("/api/benchmarks", json=TINY)
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    done = wait_done(client, job_id)
    assert done["status"] == "done" and done["error"] is None
    res = done["result"]
    assert res["spec"] == {"kind": "pipeline", "workload": "identical", "size_bytes": 16384, "iterations": 1, "seed": 3,
                           "fixed_chunk_bytes": None}
    assert res["summary"]["integrity_all_verified"] is True and res["environment"]["python"]
    assert (settings.benchmarks_dir / f"{job_id}.json").exists()  # persisted next to the app data, nothing else was
    listing = client.get("/api/benchmarks").json()
    assert listing["running"] is None and [x["job_id"] for x in listing["results"]] == [job_id]
    assert listing["results"][0]["dedup_ratio"] == pytest.approx(2.0)
    assert not list(settings.chunks_dir.rglob("*.*"))  # the application store was never used
    assert client.get("/api/files").json()["total"] == 0
    assert str(settings.data_dir) not in str(res)  # no filesystem paths in results


def test_comparison_job_via_api(client):
    r = client.post("/api/benchmarks", json={"kind": "chunking_comparison", "size_bytes": 32768, "iterations": 1, "seed": 2})
    assert r.status_code == 202
    done = wait_done(client, r.json()["job_id"])
    assert done["status"] == "done" and len(done["result"]["cases"]) == 7


def test_only_one_benchmark_runs_at_a_time_and_it_can_be_cancelled(client):
    slow = {"kind": "pipeline", "workload": "large", "iterations": 6, "seed": 1}
    first = client.post("/api/benchmarks", json=slow)
    assert first.status_code == 202
    job_id = first.json()["job_id"]
    err(client.post("/api/benchmarks", json=TINY), 409, "conflict")
    running = client.get("/api/benchmarks").json()["running"]
    assert running["job_id"] == job_id and running["status"] == "running"
    cancelled = client.post(f"/api/benchmarks/{job_id}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert client.get(f"/api/benchmarks/{job_id}").json()["status"] == "cancelled"
    assert client.get("/api/benchmarks").json()["running"] is None
    assert not list(Path(tempfile.gettempdir()).glob("chunkvault-bench-*"))  # private store removed
    err(client.post(f"/api/benchmarks/{job_id}/cancel"), 404, "not_found")
    assert client.post("/api/benchmarks", json=TINY).status_code == 202  # the slot is free again
    wait_done(client, client.get("/api/benchmarks").json()["running"]["job_id"])


def test_invalid_requests_are_rejected_before_any_work_starts(client):
    for bad in ({"kind": "pipeline"}, {"kind": "pipeline", "workload": "nope"}, {"kind": "nope"},
                {"kind": "pipeline", "workload": "small", "size_bytes": 10}, {"kind": "pipeline", "workload": "small", "iterations": 99},
                {"kind": "pipeline", "workload": "small", "seed": -5}, {"kind": "chunking_comparison", "workload": "small"},
                {"kind": "pipeline", "workload": "identical", "size_bytes": 4 * 1024 * 1024, "iterations": 10}):
        assert client.post("/api/benchmarks", json=bad).status_code == 422, bad
    assert client.get("/api/benchmarks").json() == {"running": None, "results": []}


def test_unknown_and_malformed_job_ids(client):
    err(client.get("/api/benchmarks/" + "0" * 12), 404, "not_found")
    for bad in ("short", "G" * 12, "0" * 13):
        err(client.get(f"/api/benchmarks/{bad}"), 422, "validation_error")
    assert client.get("/api/benchmarks/../../etc").status_code in (404, 422)  # never reaches the filesystem


def test_worker_failure_is_reported_not_hidden(tmp_path):
    jobs = BenchmarkJobs(tmp_path / "b", CDC)
    job_id = jobs.start(BenchmarkSpec("pipeline", "no_such_workload", 16384, 1, 1))  # bypasses API validation
    end = time.time() + 60
    while jobs.status(job_id)["status"] == "running" and time.time() < end:
        time.sleep(0.2)
    s = jobs.status(job_id)
    assert s["status"] == "failed" and "workload" in s["error"] and s["result"] is None
    assert jobs.current() is None and jobs.list_results() == []


def test_time_limit_terminates_the_worker(tmp_path):
    jobs = BenchmarkJobs(tmp_path / "b", CDC, max_seconds=0.05)
    job_id = jobs.start(BenchmarkSpec("pipeline", "large", 2 * MIB, 5, 1))
    time.sleep(0.5)
    s = jobs.status(job_id)
    assert s["status"] == "failed" and "timed out" in s["error"]
    assert jobs.current() is None


def test_shutdown_leaves_no_worker_running(tmp_path):
    jobs = BenchmarkJobs(tmp_path / "b", CDC)
    jobs.start(BenchmarkSpec("pipeline", "large", 2 * MIB, 5, 1))
    jobs.shutdown()
    assert jobs.current() is None


def test_main_application_is_not_blocked_while_a_benchmark_runs(client):
    client.post("/api/benchmarks", json={"kind": "pipeline", "workload": "large", "iterations": 4, "seed": 1})
    start = time.time()
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/files", files={"file": ("a.bin", make_data(70, 20_000))}).status_code == 201
    assert time.time() - start < 5
    job = client.get("/api/benchmarks").json()["running"]
    if job:
        client.post(f"/api/benchmarks/{job['job_id']}/cancel")


# ---- Windows file-replace race found by a real benchmark run ---------------------------------------

def test_atomic_write_retries_when_the_reader_holds_the_target_open(tmp_path, monkeypatch):
    import os

    from app.benchmarking import worker

    real, calls = os.replace, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(worker.os, "replace", flaky)
    target = tmp_path / "progress.json"
    worker._write_json(target, {"a": 1})
    assert calls["n"] == 4 and target.read_text() == '{"a": 1}'
    assert not (tmp_path / "progress.json.tmp").exists()


def test_atomic_write_gives_up_with_the_original_error(tmp_path, monkeypatch):
    from app.benchmarking import worker

    monkeypatch.setattr(worker.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError(5, "denied")))
    with pytest.raises(PermissionError):
        worker._write_json(tmp_path / "x.json", {}, attempts=3)


def test_progress_reporting_failures_never_fail_the_benchmark(tmp_path, monkeypatch):
    import json

    from app.benchmarking import worker

    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"job_id": "abc", "kind": "pipeline", "workload": "small", "size_bytes": 16384, "iterations": 1,
                                "seed": 1, "cdc": {"min_chunk_size": 4096, "avg_chunk_size": 16384, "max_chunk_size": 131072, "window_size": 64}}))
    real = worker._write_json

    def fail_for_progress(path, payload, **kw):
        if path.name.endswith("progress.json"):
            raise PermissionError(5, "denied")
        return real(path, payload, **kw)

    monkeypatch.setattr(worker, "_write_json", fail_for_progress)
    assert worker.main(["w", str(spec), str(tmp_path / "result.json"), str(tmp_path / "x.progress.json")]) == 0
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "done"
