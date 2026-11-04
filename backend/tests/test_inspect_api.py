import pytest

from app.core.config import KIB
from tests.conftest import make_data
from tests.test_storage_api import err, upload


def test_schema_describes_tables_keys_and_invariant(client):
    s = client.get("/api/inspect/schema").json()
    assert s["schema_version"] == 1
    tables = {t["name"]: t for t in s["tables"]}
    assert list(tables) == ["files", "chunks", "file_chunks", "operations"]
    fc = {c["name"]: c for c in tables["file_chunks"]["columns"]}
    assert (fc["file_id"]["primary_key_position"], fc["chunk_index"]["primary_key_position"]) == (1, 2)
    assert fc["file_id"]["foreign_key"] == {"table": "files", "column": "file_id", "on_delete": "CASCADE"}
    assert fc["chunk_hash"]["foreign_key"]["on_delete"] == "RESTRICT"
    assert tables["operations"]["columns"][1]["foreign_key"]["table"] == "files"
    assert all(c["description"] for t in tables.values() for c in t["columns"])
    assert s["ref_count_invariant"] == {"statement": s["ref_count_invariant"]["statement"],
                                         "chunks_checked": 0, "mismatches": 0}
    assert "COUNT" in s["ref_count_invariant"]["statement"]


def test_schema_row_counts_and_invariant_reflect_real_data(client):
    upload(client, make_data(1, 100 * KIB))
    s = client.get("/api/inspect/schema").json()
    counts = {t["name"]: t["row_count"] for t in s["tables"]}
    assert counts["files"] == 1 and counts["operations"] == 1
    assert counts["chunks"] == s["ref_count_invariant"]["chunks_checked"] > 0
    assert counts["file_chunks"] >= counts["chunks"]
    assert s["ref_count_invariant"]["mismatches"] == 0


def test_table_rows_paginate_and_match_other_endpoints(client):
    f = upload(client, make_data(2, 120 * KIB)).json()
    page = client.get("/api/inspect/tables/file_chunks/rows?limit=3&offset=1").json()
    assert page["total"] == f["chunk_count"] and len(page["rows"]) == 3
    assert page["columns"] == ["file_id", "chunk_index", "chunk_hash", "chunk_size"]
    assert [r["chunk_index"] for r in page["rows"]] == [1, 2, 3]
    files = client.get("/api/inspect/tables/files/rows").json()
    assert files["rows"][0]["file_id"] == f["file_id"] and files["rows"][0]["status"] == "complete"
    assert client.get("/api/inspect/tables/operations/rows").json()["rows"][0]["operation_type"] == "upload"
    assert client.get("/api/inspect/tables/chunks/rows").json()["total"] == client.get("/api/chunks").json()["total"]


def test_only_whitelisted_tables_and_validated_parameters(client):
    for bad in ("sqlite_master", "files;DROP TABLE files", "..", "FILES", "v_ref_count_mismatch"):
        assert client.get(f"/api/inspect/tables/{bad}/rows").status_code in (404, 422)
    err(client.get("/api/inspect/tables/files/rows?limit=0"), 422, "validation_error")
    err(client.get("/api/inspect/tables/files/rows?limit=201"), 422, "validation_error")
    err(client.get("/api/inspect/tables/files/rows?offset=-1"), 422, "validation_error")
    err(client.get("/api/inspect/tables/files/rows?limit=abc"), 422, "validation_error")


def test_inspection_is_read_only(client):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/api/inspect/tables/files/rows").status_code in (404, 405)
        assert getattr(client, method)("/api/inspect/schema").status_code in (404, 405)


def test_invariant_mismatch_is_surfaced(client, settings):
    upload(client, make_data(3, 60 * KIB))
    from app.database.connection import connect

    conn = connect(settings.db_path)
    conn.execute("UPDATE chunks SET ref_count = ref_count + 5")
    conn.close()
    assert client.get("/api/inspect/schema").json()["ref_count_invariant"]["mismatches"] > 0


def test_chunk_detail_lists_referencing_files(client):
    data = make_data(4, 100 * KIB)
    a = upload(client, data, "a.bin").json()
    b = upload(client, data, "b.bin").json()
    h = client.get(f"/api/files/{a['file_id']}").json()["chunks"][0]["chunk_hash"]
    d = client.get(f"/api/chunks/{h}").json()
    assert d["ref_count"] == d["actual_references"] == 2 and d["state"] == "stored"
    assert {(r["file_id"], r["filename"], r["chunk_index"]) for r in d["references"]} == {
        (a["file_id"], "a.bin", 0), (b["file_id"], "b.bin", 0)}
    assert d["references_truncated"] is False


def test_chunk_detail_errors(client):
    err(client.get("/api/chunks/" + "0" * 64), 404, "not_found")
    for bad in ("short", "../x", "G" * 64, "a" * 63):
        assert client.get(f"/api/chunks/{bad}").status_code in (404, 422)
    err(client.get("/api/chunks/not-a-hash"), 422, "validation_error")
