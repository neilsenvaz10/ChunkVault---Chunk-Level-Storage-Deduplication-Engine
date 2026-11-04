from app.chunking.rabin import POLY
from app.core.config import KIB
from tests.test_storage_api import err


def test_config_reports_the_values_in_effect(client, settings):
    c = client.get("/api/system/config").json()
    assert c["cdc"] == {"min_chunk_size": 4 * KIB, "avg_chunk_size": 16 * KIB, "max_chunk_size": 128 * KIB,
                        "window_size": 64, "boundary_divisor": 12288, "boundary_remainder": 12287,
                        "polynomial": hex(POLY), "polynomial_degree": 53}
    assert c["max_upload_bytes"] == settings.max_upload_bytes
    assert "/" in c["chunk_path_layout"] and str(settings.data_dir) not in str(c)


def test_demo_runs_the_real_chunker_and_is_deterministic(client):
    a = client.get("/api/system/chunking-demo?seed=3&size=196608&insert_at=100&insert_len=37").json()
    b = client.get("/api/system/chunking-demo?seed=3&size=196608&insert_at=100&insert_len=37").json()
    assert a == b
    assert a["modified_size"] == a["original_size"] + 37
    for scheme in ("cdc", "fixed"):
        s = a[scheme]
        assert sum(c["size"] for c in s["original_chunks"]) == a["original_size"]
        assert sum(c["size"] for c in s["modified_chunks"]) == a["modified_size"]
        assert s["reused_chunks"] == sum(1 for c in s["modified_chunks"] if c["reused"])
        assert all(c["reused"] is None for c in s["original_chunks"])
    # insertion near the start: CDC resynchronises, fixed-size does not
    assert a["cdc"]["reuse_percent"] > 80
    assert a["fixed"]["reuse_percent"] == 0
    assert all(c["size"] == 16 * KIB for c in a["fixed"]["original_chunks"][:-1])
    assert "does not guarantee" in a["note"]


def test_demo_matches_the_production_chunker(client, app):
    import io, random
    d = client.get("/api/system/chunking-demo?seed=11&size=65536").json()
    data = random.Random(11).randbytes(65536)
    real = [c.size for c in app.state.services.chunker.chunk_stream(io.BytesIO(data))]
    assert [c["size"] for c in d["cdc"]["original_chunks"]] == real


def test_demo_is_bounded_and_validated(client):
    for q in ("size=1000000", "size=100", "seed=-1", "insert_len=0", "insert_len=5000", "fixed_size=10",
              "fixed_size=999999", "size=abc"):
        err(client.get(f"/api/system/chunking-demo?{q}"), 422, "validation_error")
    err(client.get("/api/system/chunking-demo?size=20000&insert_at=30000"), 422, "invalid_parameters")


def test_demo_has_no_side_effects(client, settings):
    client.get("/api/system/chunking-demo")
    assert client.get("/api/files").json()["total"] == 0
    assert client.get("/api/chunks").json()["total"] == 0
    assert not list(settings.chunks_dir.rglob("*.*")) and not list(settings.staging_dir.iterdir())
    for method in ("post", "put", "delete"):
        assert getattr(client, method)("/api/system/chunking-demo").status_code in (404, 405)
