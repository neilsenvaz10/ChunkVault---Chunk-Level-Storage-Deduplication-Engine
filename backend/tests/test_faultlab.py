"""Fault-injection lab: every scenario, recovery correctness, isolation, safety switches."""

import io
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.reliability import faultlab
from app.reliability.faultlab import SCENARIOS, run_scenario
from tests.conftest import make_data
from tests.test_storage_api import err


@pytest.fixture
def lab_settings(tmp_path):
    return Settings(data_dir=tmp_path / "real", db_path=tmp_path / "real" / "app.db",
                    cors_origins=("http://localhost:5173",), fault_lab_enabled=True)


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    """Run every scenario once (about 2 s in total) and share the observations."""
    base = tmp_path_factory.mktemp("lab")
    s = Settings(data_dir=base / "real", db_path=base / "real" / "app.db",
                 cors_origins=("http://localhost:5173",), fault_lab_enabled=True)
    return {sid: run_scenario(sid, s) for sid in SCENARIOS}


def leftovers():
    return set(Path(tempfile.gettempdir()).glob("chunkvault-faultlab-*"))


# ---- every scenario -----------------------------------------------------------

def test_nine_scenarios_cover_the_required_failure_points():
    assert set(SCENARIOS) == {
        "upload_stream_crash", "cdc_failure", "crash_before_metadata_commit", "metadata_failure", "crash_after_commit",
        "restore_failure", "delete_failure_rollback", "delete_crash_after_commit", "recovery_interrupted"}


@pytest.mark.parametrize("sid", list(SCENARIOS))
def test_declared_expectations_match_observed_behaviour(results, sid):
    r = results[sid]
    unmet = [(e["label"], e["expected"], e["observed"]) for e in r["expectations"] if not e["matches"]]
    assert not unmet, unmet
    assert r["all_expectations_met"] and r["fault"]["fault_raised"] is True
    assert r["within_time_bound"] and r["duration_ms"] < 30_000
    assert r["isolation"] == {"temporary_store_used": True, "application_data_touched": False,
                              "temporary_store_removed": True}
    assert [s["phase"] for s in r["steps"]].count("fault") == 1


@pytest.mark.parametrize("sid", [s for s in SCENARIOS if s != "restore_failure"])
def test_recovery_is_verified_clean_and_every_invariant_holds(results, sid):
    r = results[sid]
    assert r["recovery_status"] == "verified_clean"
    assert all(i["holds"] for i in r["invariants"]), [i for i in r["invariants"] if not i["holds"]]
    assert r["after_recovery"]["audit"]["healthy"] is True


def test_repeated_recovery_changes_nothing(results):
    for r in results.values():
        assert r["second_recovery_actions"] == 0
        assert next(i for i in r["invariants"] if i["id"] == "I7")["holds"]


def test_committed_control_file_always_survives_and_restores_exactly(results):
    for r in results.values():
        ctl = [x for x in r["restores"] if x["filename"] == "control.bin"][0]
        assert ctl == {"filename": "control.bin", "outcome": "restored", "matches_original": True}
        assert next(i for i in r["invariants"] if i["id"] == "I6")["holds"]  # recovery deleted nothing it needed


# ---- scenario specifics ---------------------------------------------------------

def test_crash_during_upload_leaves_only_staging_data(results):
    r = results["upload_stream_crash"]
    b = r["before_recovery"]
    assert not b["victim_visible"] and b["staging_files"] >= 1 and b["orphan_chunk_files"] == 0
    assert [a["action"] for a in r["recovery_actions"]] == ["delete_staging_file"]


def test_cdc_failure_is_handled_by_the_engine_without_recovery(results):
    r = results["cdc_failure"]
    assert r["fault"]["code"] == "chunking_error"
    assert r["before_recovery"]["victim_status"] == "failed"
    assert r["before_recovery"]["staging_files"] == 0 and r["before_recovery"]["orphan_chunk_files"] == 0
    assert r["recovery_actions"] == []


def test_crash_before_commit_leaves_orphans_and_an_uploading_file_never_a_complete_one(results):
    r = results["crash_before_metadata_commit"]
    b = r["before_recovery"]
    assert b["victim_status"] == "uploading" and b["victim_manifest_rows"] == 0
    assert b["orphan_chunk_files"] >= 1 and b["running_operations"] == 1
    assert {"orphan_chunk_file", "incomplete_file", "stuck_operation", "orphan_staging_file"} <= set(b["audit"]["by_kind"])
    assert {a["action"] for a in r["recovery_actions"]} == {
        "delete_staging_file", "abort_operation", "fail_incomplete_file", "delete_orphan_chunk"}


def test_metadata_failure_rolls_back_including_reference_counts(results):
    r = results["metadata_failure"]
    b = r["before_recovery"]
    assert b["victim_status"] == "failed" and b["victim_manifest_rows"] == 0
    assert b["ref_count_mismatches"] == 0 and b["orphan_chunk_files"] == 0
    assert b["manifest_rows"] == 4  # only the control file's manifest


def test_crash_after_commit_keeps_the_file_but_a_retry_duplicates_the_record(results):
    r = results["crash_after_commit"]
    assert r["before_recovery"]["victim_status"] == "complete"
    assert r["retry"] == {"visible_victim_files": 2, "new_chunks": 0, "bytes_written": 0}
    assert [x for x in r["restores"] if x["filename"] == "victim.bin"][0]["matches_original"] is True


def test_restore_failure_is_refused_detected_and_reported_as_not_guaranteed(results):
    r = results["restore_failure"]
    assert r["fault"]["code"] == "chunk_corrupt" and r["fault"]["partial_output_left"] is False
    assert r["recovery_actions"] == []  # nothing is silently repaired
    assert r["recovery_status"] == "verified_with_unrepairable_damage"
    inv = {i["id"]: i for i in r["invariants"]}
    assert inv["I1"]["holds"] is False and inv["I2"]["holds"] is False  # reported, not hidden
    assert inv["I5"]["holds"] is True  # restoration did not silently return wrong content
    assert r["after_recovery"]["audit"]["by_kind"] == {"corrupt_chunk": 1}
    assert [x for x in r["restores"] if x["filename"] == "victim.bin"][0]["outcome"] == "refused"


def test_delete_rollback_leaves_the_file_intact(results):
    r = results["delete_failure_rollback"]
    assert r["fault"]["exception"] == "RuntimeError"
    assert r["before_recovery"]["victim_status"] == "complete" and r["before_recovery"]["manifest_rows"] == 8
    assert r["recovery_actions"] == []
    assert [x for x in r["restores"] if x["filename"] == "victim.bin"][0]["matches_original"] is True


def test_delete_crash_leaves_consistent_metadata_and_only_orphan_files(results):
    r = results["delete_crash_after_commit"]
    b = r["before_recovery"]
    assert not b["victim_visible"] and b["ref_count_mismatches"] == 0 and b["orphan_chunk_files"] >= 1
    assert set(b["audit"]["by_kind"]) == {"orphan_chunk_file"}
    assert {a["action"] for a in r["recovery_actions"]} == {"delete_orphan_chunk"}


def test_interrupted_recovery_makes_progress_and_converges(results):
    r = results["recovery_interrupted"]
    f = r["fault"]
    assert f["orphans_created"] > 1 and 0 < f["orphans_remaining"] < f["orphans_created"]
    assert len(r["recovery_actions"]) == f["orphans_remaining"]  # the re-run finished exactly the rest
    assert r["after_recovery"]["orphan_chunk_files"] == 0 and r["second_recovery_actions"] == 0


# ---- isolation and safety --------------------------------------------------------

def test_experiments_never_touch_the_application_data_directory(lab_settings):
    before = leftovers()
    for sid in SCENARIOS:
        run_scenario(sid, lab_settings)
    assert not lab_settings.data_dir.exists()  # never even created
    assert not lab_settings.db_path.exists()
    assert leftovers() == before  # every temporary store was removed


def test_application_store_is_untouched_while_experiments_run(tmp_path):
    s = Settings(data_dir=tmp_path / "d", db_path=tmp_path / "d" / "app.db",
                 cors_origins=("http://localhost:5173",), fault_lab_enabled=True)
    with TestClient(create_app(s), raise_server_exceptions=False, base_url="http://localhost") as c:
        data = make_data(50, 100_000)
        f = c.post("/api/files", files={"file": ("real.bin", data)}).json()
        snap = (s.db_path.read_bytes(), sorted(p.name for p in s.chunks_dir.rglob("*") if p.is_file()))
        for sid in ("crash_before_metadata_commit", "restore_failure", "delete_crash_after_commit"):
            assert c.post("/api/reliability/experiments", json={"scenario_id": sid}).status_code == 200
        assert c.get(f"/api/files/{f['file_id']}/download").content == data
        assert sorted(p.name for p in s.chunks_dir.rglob("*") if p.is_file()) == snap[1]
        assert c.get("/api/files").json()["total"] == 1
        assert c.post("/api/integrity/audit").json()["healthy"] is True


def test_temporary_store_is_removed_even_if_the_experiment_itself_crashes(lab_settings, monkeypatch):
    before = leftovers()

    def broken(lab):
        raise RuntimeError("scenario body failed")

    monkeypatch.setitem(SCENARIOS, "cdc_failure", faultlab.Scenario(**{**SCENARIOS["cdc_failure"].__dict__, "run": broken}))
    with pytest.raises(RuntimeError):
        run_scenario("cdc_failure", lab_settings)
    assert leftovers() == before
    assert run_scenario("upload_stream_crash", lab_settings)["all_expectations_met"]  # lock was released


def test_disabled_by_default_and_by_environment(client, settings):
    assert Settings.from_env({}).fault_lab_enabled is False
    assert Settings.from_env({"CHUNKVAULT_ENABLE_FAULT_LAB": "1"}).fault_lab_enabled is True
    assert Settings.from_env({"CHUNKVAULT_ENABLE_FAULT_LAB": "0"}).fault_lab_enabled is False
    body = client.get("/api/reliability/scenarios").json()
    assert body["enabled"] is False and len(body["scenarios"]) == 9 and body["safety"]
    err(client.post("/api/reliability/experiments", json={"scenario_id": "cdc_failure"}), 403, "fault_lab_disabled")
    assert not list(settings.data_dir.glob("**/victim*"))


def test_api_runs_a_scenario_when_enabled_and_validates_input(tmp_path):
    s = Settings(data_dir=tmp_path / "d", db_path=tmp_path / "d" / "app.db",
                 cors_origins=("http://localhost:5173",), fault_lab_enabled=True)
    with TestClient(create_app(s), raise_server_exceptions=False, base_url="http://localhost") as c:
        assert c.get("/api/reliability/scenarios").json()["enabled"] is True
        ok = c.post("/api/reliability/experiments", json={"scenario_id": "metadata_failure"}).json()
        assert ok["recovery_status"] == "verified_clean" and ok["isolation"]["temporary_store_removed"] is True
        err(c.post("/api/reliability/experiments", json={"scenario_id": "no_such_scenario"}), 422, "unknown_scenario")
        for bad in ({}, {"scenario_id": "../etc"}, {"scenario_id": "A" * 5}, {"scenario_id": 5}, {"fault": "custom"}):
            err(c.post("/api/reliability/experiments", json=bad), 422, "validation_error")
        # no way to submit a custom fault or pick a path
        assert c.get("/api/reliability/experiments").status_code in (404, 405)


def test_only_one_experiment_runs_at_a_time(lab_settings):
    from app.core.errors import ConflictError

    assert faultlab._RUN_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(ConflictError):
            run_scenario("cdc_failure", lab_settings)
    finally:
        faultlab._RUN_LOCK.release()


def test_experiment_observations_are_deterministic(lab_settings):
    import json
    import re

    def stable(r):  # only timing and random identifiers (file ids, staging-file names) may differ
        r = {k: v for k, v in r.items() if k != "duration_ms"}
        r["recovery_actions"] = [dict(a, subject="X") if a["action"] in ("fail_incomplete_file", "delete_staging_file", "abort_operation") else a
                                 for a in r["recovery_actions"]]
        text = json.dumps(r, sort_keys=True)
        return re.sub(r"(upload|restore)-[0-9a-f]{4,32}(\.part|\.tmp)?", "FILE-X", text)

    a = stable(run_scenario("crash_before_metadata_commit", lab_settings))
    b = stable(run_scenario("crash_before_metadata_commit", lab_settings))
    assert a == b


def test_application_refuses_to_run_over_its_own_data(monkeypatch, tmp_path):
    """Guard: if the lab's store could ever overlap the application's data it refuses."""
    from app.core.errors import ConflictError

    real = tmp_path / "app"
    s = Settings(data_dir=real, db_path=real / "app.db", cors_origins=("http://localhost:5173",), fault_lab_enabled=True)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda prefix="": str(real / "inside"))
    (real / "inside").mkdir(parents=True)
    with pytest.raises(ConflictError):
        run_scenario("cdc_failure", s)


# ---- the checkpoint seam ---------------------------------------------------------

def test_checkpoints_fire_in_protocol_order_and_default_to_a_no_op(svc):
    seen = []
    svc.uploader.checkpoint = seen.append
    svc.uploader.upload("a.bin", io.BytesIO(make_data(60, 90_000)))
    names = list(dict.fromkeys(seen))
    assert names == ["staged", "t1_committed", "chunk_published", "chunks_ready", "in_t2", "after_commit"]
    del svc.uploader.__dict__["checkpoint"]
    assert svc.uploader.checkpoint("anything") is None  # production behaviour: nothing happens
