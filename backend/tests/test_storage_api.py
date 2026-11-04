import hashlib

import pytest
from fastapi.testclient import TestClient

from app.core.config import KIB, Settings
from app.main import create_app
from tests.conftest import make_data


def upload(client, data, name="a.bin"):
    return client.post("/api/files", files={"file": (name, data, "application/octet-stream")})


def err(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    return body["error"]


def test_valid_upload(client):
    data = make_data(1, 200 * KIB)
    r = upload(client, data)
    assert r.status_code == 201
    b = r.json()
    assert b["original_size"] == len(data) and b["sha256"] == hashlib.sha256(data).hexdigest()
    assert b["chunk_count"] == b["new_chunks"] + b["duplicate_chunks"] and b["bytes_written"] == len(data)
    assert len(b["file_id"]) == 32


def test_empty_file_upload_is_accepted(client):
    r = upload(client, b"", "empty.txt")
    assert r.status_code == 201 and r.json()["chunk_count"] == 0
    d = client.get(f"/api/files/{r.json()['file_id']}/download")
    assert d.status_code == 200 and d.content == b""


def test_upload_without_a_file_is_a_validation_error(client):
    err(client.post("/api/files"), 422, "validation_error")
    err(client.post("/api/files", data={"x": "y"}), 422, "validation_error")


def test_oversized_upload_rejected(tmp_path):
    s = Settings(data_dir=tmp_path / "d", db_path=tmp_path / "d" / "x.db",
                 cors_origins=("http://localhost:5173",), max_upload_bytes=5000)
    with TestClient(create_app(s), raise_server_exceptions=False) as c:
        err(upload(c, b"x" * 2_000_000), 413, "upload_too_large")  # caught by Content-Length
        assert upload(c, b"x" * 5000).status_code == 201
        assert c.get("/api/files").json()["total"] == 1
        assert not list(s.staging_dir.iterdir())


def test_list_and_detail(client):
    data = make_data(2, 150 * KIB)
    a = upload(client, data, "one.bin").json()
    b = upload(client, data, "two.bin").json()
    listing = client.get("/api/files").json()
    assert listing["total"] == 2 and {i["filename"] for i in listing["items"]} == {"one.bin", "two.bin"}
    assert len(client.get("/api/files?limit=1&offset=1").json()["items"]) == 1
    detail = client.get(f"/api/files/{a['file_id']}").json()
    assert detail["status"] == "complete" and detail["chunk_count"] == a["chunk_count"]
    assert [c["chunk_index"] for c in detail["chunks"]] == list(range(a["chunk_count"]))
    assert all(c["ref_count"] == 2 for c in detail["chunks"])  # shared with the duplicate upload
    assert b["file_id"] != a["file_id"]
    err(client.get("/api/files?limit=0"), 422, "validation_error")


def test_invalid_file_id_is_rejected_before_any_lookup(client):
    for bad in ("nope", "../../etc/passwd", "A" * 32, "g" * 32):
        for path in (f"/api/files/{bad}", f"/api/files/{bad}/download"):
            assert client.get(path).status_code in (404, 422)
    err(client.get("/api/files/not-an-id"), 422, "validation_error")
    err(client.delete("/api/files/not-an-id"), 422, "validation_error")


def test_missing_file(client):
    fid = "0" * 32
    err(client.get(f"/api/files/{fid}"), 404, "file_not_found")
    err(client.get(f"/api/files/{fid}/download"), 404, "file_not_restorable")
    err(client.delete(f"/api/files/{fid}"), 404, "file_not_found")


def test_download_is_byte_exact_with_hash_header(client):
    data = make_data(3, 250 * KIB)
    f = upload(client, data, "my report.bin").json()
    r = client.get(f"/api/files/{f['file_id']}/download")
    assert r.status_code == 200 and r.content == data
    assert r.headers["x-chunkvault-sha256"] == f["sha256"]
    assert "my%20report.bin" in r.headers["content-disposition"]  # RFC 5987 encoded


def test_download_leaves_no_temp_files(client, settings):
    f = upload(client, make_data(4, 50 * KIB)).json()
    client.get(f"/api/files/{f['file_id']}/download")
    assert list(settings.staging_dir.iterdir()) == []


def test_integrity_failure_on_download(client, settings):
    f = upload(client, make_data(5, 200 * KIB)).json()
    first = client.get(f"/api/files/{f['file_id']}").json()["chunks"][0]["chunk_hash"]
    p = settings.chunks_dir / first[:2] / first
    raw = bytearray(p.read_bytes())
    raw[0] ^= 0xFF
    p.write_bytes(bytes(raw))
    e = err(client.get(f"/api/files/{f['file_id']}/download"), 500, "chunk_corrupt")
    assert str(settings.data_dir) not in e["message"]
    assert list(settings.staging_dir.iterdir()) == []  # no partial output left behind
    p.unlink()
    err(client.get(f"/api/files/{f['file_id']}/download"), 500, "chunk_missing")


def test_delete_and_repeated_delete(client):
    data = make_data(6, 150 * KIB)
    a = upload(client, data, "a").json()
    b = upload(client, data, "b").json()
    r = client.delete(f"/api/files/{a['file_id']}")
    assert r.status_code == 200 and r.json()["chunks_reclaimed"] == 0  # all shared with b
    assert r.json()["chunks_released"] == a["chunk_count"]
    assert client.get(f"/api/files/{b['file_id']}/download").content == data
    err(client.delete(f"/api/files/{a['file_id']}"), 404, "file_not_found")  # predictable repeat
    r = client.delete(f"/api/files/{b['file_id']}")
    assert r.json()["chunks_reclaimed"] == b["chunk_count"] and r.json()["bytes_reclaimed"] == len(data)
    assert client.get("/api/chunks").json()["total"] == 0


def test_chunks_endpoint(client):
    upload(client, make_data(7, 120 * KIB))
    body = client.get("/api/chunks?limit=3").json()
    assert body["total"] > 3 and len(body["items"]) == 3
    c = body["items"][0]
    assert set(c) == {"hash", "size_bytes", "ref_count", "state", "created_at"}  # no paths
    assert client.get("/api/chunks?state=corrupt").json()["total"] == 0
    err(client.get("/api/chunks?state=bogus"), 422, "validation_error")


def test_storage_stats(client):
    s = client.get("/api/storage/stats").json()
    assert s["total_files"] == 0 and s["storage_savings_percent"] is None and s["deduplication_ratio"] is None
    data = make_data(8, 100 * KIB)
    upload(client, data)
    upload(client, data)
    s = client.get("/api/storage/stats").json()
    assert s["total_files"] == 2 and s["logical_bytes"] == 2 * len(data)
    assert s["unique_chunk_bytes"] == len(data)
    assert s["storage_savings_percent"] == pytest.approx(50.0) and s["deduplication_ratio"] == pytest.approx(2.0)
    assert s["chunk_store_bytes"] == len(data) and s["database_bytes"] > 0


def test_audit_endpoint_detects_and_is_read_only(client, settings):
    f = upload(client, make_data(9, 150 * KIB)).json()
    assert client.post("/api/integrity/audit").json()["healthy"] is True
    first = client.get(f"/api/files/{f['file_id']}").json()["chunks"][0]["chunk_hash"]
    p = settings.chunks_dir / first[:2] / first
    p.write_bytes(b"damaged")
    (settings.staging_dir / "leftover.tmp").write_bytes(b"x")
    r = client.post("/api/integrity/audit", json={}).json()
    assert r["healthy"] is False and r["counts_by_kind"]["corrupt_chunk"] == 1
    assert r["counts_by_kind"]["orphan_staging_file"] == 1
    assert p.read_bytes() == b"damaged" and (settings.staging_dir / "leftover.tmp").exists()  # nothing repaired
    one = client.post("/api/integrity/audit", json={"file_id": f["file_id"]}).json()
    assert one["files_checked"] == 1 and one["counts_by_kind"]["corrupt_chunk"] == 1
    err(client.post("/api/integrity/audit", json={"file_id": "0" * 32}), 404, "not_found")
    err(client.post("/api/integrity/audit", json={"file_id": "bad"}), 422, "validation_error")


def test_reconcile_endpoint_reports_actions(client, settings):
    (settings.staging_dir / "leftover.tmp").write_bytes(b"x")
    r = client.post("/api/integrity/reconcile").json()
    assert [a["action"] for a in r["actions"]] == ["delete_staging_file"]
    assert r["remaining"]["healthy"] is True


def test_no_internal_paths_in_responses(client, settings):
    f = upload(client, make_data(10, 60 * KIB)).json()
    texts = [client.get("/api/files").text, client.get(f"/api/files/{f['file_id']}").text,
             client.get("/api/chunks").text, client.get("/api/storage/stats").text,
             client.post("/api/integrity/audit").text]
    for t in texts:
        assert str(settings.data_dir) not in t and "chunks" + "\\" not in t


def test_openapi_lists_storage_endpoints_and_registry_only_has_the_unbuilt(client):
    paths = client.get("/api/openapi.json").json()["paths"]
    for p in ("/api/files", "/api/files/{file_id}", "/api/files/{file_id}/download", "/api/chunks",
              "/api/storage/stats", "/api/integrity/audit"):
        assert p in paths
    planned = {e["path"] for e in client.get("/api/planned-endpoints").json()["endpoints"]}
    assert planned == {"/api/benchmarks", "/api/simulations"}


def test_upload_response_reports_per_chunk_decisions(client):
    data = make_data(30, 200 * KIB)
    first = upload(client, data).json()
    assert [o["chunk_index"] for o in first["chunk_outcomes"]] == list(range(first["chunk_count"]))
    assert {o["decision"] for o in first["chunk_outcomes"]} == {"new"}
    assert sum(o["chunk_size"] for o in first["chunk_outcomes"]) == len(data)
    second = upload(client, data).json()
    assert {o["decision"] for o in second["chunk_outcomes"]} == {"reused"}
    mod = upload(client, data[:300] + b"inserted" + data[300:]).json()
    decisions = [o["decision"] for o in mod["chunk_outcomes"]]
    assert decisions.count("new") == mod["new_chunks"] and decisions.count("reused") == mod["duplicate_chunks"]
    zeros = upload(client, bytes(300 * KIB)).json()
    assert [o["decision"] for o in zeros["chunk_outcomes"]] == ["new", "repeated_in_file", "reused"][:0] or \
        zeros["chunk_outcomes"][1]["decision"] == "repeated_in_file"


def test_file_summaries_carry_chunk_counts(client):
    a = upload(client, make_data(31, 120 * KIB)).json()
    e = upload(client, b"", "empty").json()
    items = {i["file_id"]: i for i in client.get("/api/files").json()["items"]}
    assert items[a["file_id"]]["chunk_count"] == a["chunk_count"] > 0
    assert items[e["file_id"]]["chunk_count"] == 0
    assert client.get(f"/api/files/{a['file_id']}").json()["chunk_count"] == a["chunk_count"]
