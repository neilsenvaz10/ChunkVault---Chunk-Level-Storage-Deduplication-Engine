"""Capacity model: formulas, boundaries, growth, retention and large inputs."""

import math

import pytest

from app.simulation.model import SimulationInput, simulate
from tests.test_storage_api import err


def inp(**kw):
    base = dict(file_count=1000, avg_file_size_bytes=1_000_000, avg_chunks_per_file=10.0, duplicate_percent=0.0,
                metadata_bytes_per_file=100, metadata_bytes_per_unique_chunk=50, metadata_bytes_per_manifest_row=20,
                monthly_growth_percent=0.0, retention_months=0, horizon_months=12)
    base.update(kw)
    return SimulationInput(**base)


def out(**kw):
    return simulate(inp(**kw))["outputs"]


# ---- formulas ---------------------------------------------------------------------------

def test_formulas_with_hand_computed_values():
    o = out(duplicate_percent=25.0)
    assert o["chunk_references"] == 10_000          # 1000 files x 10 chunks
    assert o["unique_chunks"] == 7_500              # x (1 - 0.25)
    assert o["avg_chunk_bytes"] == 100_000          # 1,000,000 / 10
    assert o["logical_bytes"] == 1_000_000_000
    assert o["physical_chunk_bytes"] == 750_000_000  # 7,500 x 100,000
    assert o["savings_percent"] == pytest.approx(25.0)
    assert o["dedup_ratio"] == pytest.approx(1 / 0.75)
    assert o["metadata_breakdown"] == {"files": 100_000, "manifest_rows": 200_000, "unique_chunks": 375_000}
    assert o["metadata_bytes"] == 675_000
    assert o["total_disk_bytes"] == 750_000_000 + 675_000
    assert o["metadata_percent_of_total"] == pytest.approx(100 * 675_000 / (750_000_000 + 675_000))


def test_default_chunks_per_file_comes_from_the_16_kib_target():
    r = simulate(inp(avg_chunks_per_file=None, avg_file_size_bytes=1_000_000))
    assert r["inputs"]["avg_chunks_per_file_used"] == math.ceil(1_000_000 / 16384)
    tiny = simulate(inp(avg_chunks_per_file=None, avg_file_size_bytes=100))
    assert tiny["inputs"]["avg_chunks_per_file_used"] == 1


# ---- boundary conditions ------------------------------------------------------------------

def test_zero_duplication_means_no_savings_and_equal_storage():
    o = out(duplicate_percent=0.0)
    assert o["physical_chunk_bytes"] == o["logical_bytes"] and o["savings_percent"] == 0 and o["dedup_ratio"] == 1.0
    assert o["unique_chunks"] == o["chunk_references"]


def test_high_duplication():
    o = out(duplicate_percent=99.9)
    assert o["savings_percent"] == pytest.approx(99.9)
    assert o["dedup_ratio"] == pytest.approx(1000.0, rel=1e-6)
    assert o["physical_chunk_bytes"] == pytest.approx(1_000_000, rel=1e-6)


def test_total_duplication_has_no_physical_chunks_and_an_undefined_ratio():
    o = out(duplicate_percent=100.0)
    assert o["physical_chunk_bytes"] == 0 and o["unique_chunks"] == 0 and o["savings_percent"] == 100.0
    assert o["dedup_ratio"] is None
    assert o["total_disk_bytes"] == o["metadata_bytes"]  # only the manifests remain


def test_single_file_single_chunk_minimum():
    o = out(file_count=1, avg_file_size_bytes=1, avg_chunks_per_file=1.0)
    assert o["logical_bytes"] == 1 and o["unique_chunks"] == 1 and o["metadata_bytes"] == 100 + 20 + 50


def test_zero_metadata_estimates_are_allowed():
    o = out(metadata_bytes_per_file=0, metadata_bytes_per_unique_chunk=0, metadata_bytes_per_manifest_row=0)
    assert o["metadata_bytes"] == 0 and o["metadata_percent_of_total"] == 0


def test_large_inputs_stay_finite_and_exact_enough():
    r = simulate(inp(file_count=10**12, avg_file_size_bytes=10**12, avg_chunks_per_file=6.1e7, duplicate_percent=50.0,
                     monthly_growth_percent=50.0, horizon_months=120))
    assert r["outputs"]["logical_bytes"] == 1e24
    assert r["outputs"]["physical_chunk_bytes"] == pytest.approx(5e23)
    assert all(math.isfinite(v) for g in r["growth"] for v in g.values() if v is not None)
    assert math.isfinite(r["growth_summary"]["final_total_bytes"])


# ---- series -------------------------------------------------------------------------------------

def test_file_count_sweep_is_linear_and_ends_at_the_input():
    r = simulate(inp(duplicate_percent=20.0))
    sw = r["file_count_sweep"]
    assert [p["files"] for p in sw] == [100 * k for k in range(1, 11)]
    assert sw[-1]["total_bytes"] == r["outputs"]["total_disk_bytes"]
    assert sw[4]["logical_bytes"] * 2 == pytest.approx(sw[9]["logical_bytes"])
    assert all(b["metadata_bytes"] > a["metadata_bytes"] for a, b in zip(sw, sw[1:]))


def test_dedup_sweep_savings_equal_the_duplicate_rate():
    r = simulate(inp())
    sw = r["dedup_sweep"]
    assert [p["duplicate_percent"] for p in sw] == list(range(0, 101, 10))
    for p in sw:
        assert p["savings_percent"] == pytest.approx(p["duplicate_percent"])
    assert sw[0]["physical_bytes"] == sw[0]["logical_bytes"] and sw[-1]["physical_bytes"] == 0 and sw[-1]["dedup_ratio"] is None
    assert all(a["physical_bytes"] >= b["physical_bytes"] for a, b in zip(sw, sw[1:]))


def test_no_growth_keeps_the_storage_flat():
    g = simulate(inp(monthly_growth_percent=0.0, horizon_months=6))["growth"]
    assert len(g) == 7 and {p["total_bytes"] for p in g} == {g[0]["total_bytes"]}


def test_compound_growth():
    g = simulate(inp(monthly_growth_percent=10.0, horizon_months=3))["growth"]
    assert [p["files"] for p in g] == pytest.approx([1000, 1100, 1210, 1331])
    assert g[3]["logical_bytes"] == pytest.approx(1331 * 1_000_000)


def test_retention_expires_old_files():
    r = simulate(inp(monthly_growth_percent=10.0, retention_months=2, horizon_months=4))
    g = r["growth"]
    c = [1000 * 1.1 ** t for t in range(5)]
    assert g[0]["files"] == pytest.approx(c[0]) and g[1]["files"] == pytest.approx(c[1])  # nothing expired yet
    assert g[2]["files"] == pytest.approx(c[2] - c[0])                                      # month-0 files expired
    assert g[4]["files"] == pytest.approx(c[4] - c[2])
    assert r["growth_summary"]["retention_months"] == 2


def test_retention_with_no_growth_drops_the_initial_files_at_the_retention_age():
    g = simulate(inp(monthly_growth_percent=0.0, retention_months=3, horizon_months=5))["growth"]
    assert [p["files"] for p in g][:3] == [1000, 1000, 1000] and g[3]["files"] == 0  # cum(3) - cum(0) = 0


def test_outputs_state_the_model_formulas_and_assumptions():
    r = simulate(inp())
    assert "not a measurement" in r["model"] and len(r["formulas"]) >= 8 and len(r["assumptions"]) >= 5
    assert any("constant" in a for a in r["assumptions"])


def test_model_is_pure_and_deterministic():
    assert simulate(inp(duplicate_percent=33.0)) == simulate(inp(duplicate_percent=33.0))


# ---- API ------------------------------------------------------------------------------------------

def test_api_returns_the_model_with_defaults(client):
    r = client.post("/api/simulations", json={})
    assert r.status_code == 200
    b = r.json()
    assert b["outputs"]["logical_bytes"] == 100_000 * 5 * 1024 * 1024 and b["growth"][-1]["month"] == 24
    assert b["inputs"]["avg_chunks_per_file_used"] == math.ceil(5 * 1024 * 1024 / 16384)


def test_api_matches_the_model_function(client):
    body = {"file_count": 2500, "avg_file_size_bytes": 3_000_000, "avg_chunks_per_file": 12, "duplicate_percent": 40,
            "metadata_bytes_per_file": 250, "metadata_bytes_per_unique_chunk": 120, "metadata_bytes_per_manifest_row": 60,
            "monthly_growth_percent": 7.5, "retention_months": 6, "horizon_months": 18}
    api = client.post("/api/simulations", json=body).json()
    direct = simulate(SimulationInput(**body))
    assert api["outputs"] == direct["outputs"] and api["growth"] == direct["growth"]


@pytest.mark.parametrize("bad", [
    {"file_count": 0}, {"file_count": 10**13}, {"avg_file_size_bytes": 0}, {"duplicate_percent": -1}, {"duplicate_percent": 100.5},
    {"avg_chunks_per_file": 0}, {"monthly_growth_percent": 51}, {"retention_months": 121}, {"horizon_months": 0},
    {"horizon_months": 121}, {"metadata_bytes_per_file": -1}, {"file_count": "many"}])
def test_api_rejects_invalid_assumptions(client, bad):
    err(client.post("/api/simulations", json=bad), 422, "validation_error")


def test_simulation_touches_no_storage(client, settings):
    client.post("/api/simulations", json={"file_count": 10**9})
    assert client.get("/api/files").json()["total"] == 0 and client.get("/api/chunks").json()["total"] == 0
    assert not list(settings.chunks_dir.rglob("*.*"))
