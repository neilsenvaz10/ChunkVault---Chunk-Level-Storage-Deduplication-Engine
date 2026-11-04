import pytest
from pydantic import BaseModel, Field

from app.core.errors import ConflictError
from app.schemas import files, operations
from app.api.planned import PLANNED_ENDPOINTS


def assert_error_envelope(body, code):
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


# ---- health ---------------------------------------------------------------

def test_health_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "ChunkVault"
    assert body["database"] == {"connected": True, "foreign_keys_enforced": True, "schema_version": 1}


def test_startup_creates_directories_and_schema(client, settings):
    assert settings.chunks_dir.is_dir() and settings.staging_dir.is_dir()
    assert settings.db_path.exists()


def test_health_degraded_when_database_fails(client, app):
    import sqlite3
    from app.api.deps import get_db

    class Broken:
        def execute(self, *a):
            raise sqlite3.OperationalError("boom")

    app.dependency_overrides[get_db] = lambda: Broken()
    r = client.get("/api/health")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert r.json()["database"]["connected"] is False


def test_cors_allows_dev_origin_only(client):
    ok = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_openapi_documented(client):
    spec = client.get("/openapi.json").json()
    assert "/api/health" in spec["paths"]
    assert client.get("/docs").status_code == 200


# ---- error format ----------------------------------------------------------

def test_unknown_route_uses_error_envelope(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert_error_envelope(r.json(), "not_found")


def test_wrong_method_uses_error_envelope(client):
    r = client.post("/api/health")
    assert r.status_code == 405
    assert_error_envelope(r.json(), "method_not_allowed")


def test_validation_error_envelope(app, client):
    class Body(BaseModel):
        n: int = Field(gt=0)

    @app.post("/api/_test/validate")
    def validate(body: Body):
        return {}

    r = client.post("/api/_test/validate", json={"n": -1})
    assert r.status_code == 422
    assert_error_envelope(r.json(), "validation_error")
    assert r.json()["error"]["details"][0]["loc"] == ["body", "n"]


def test_domain_error_envelope(app, client):
    @app.get("/api/_test/conflict")
    def conflict():
        raise ConflictError("already there", details={"id": 1})

    r = client.get("/api/_test/conflict")
    assert r.status_code == 409
    assert_error_envelope(r.json(), "conflict")
    assert r.json()["error"]["details"] == {"id": 1}


def test_unhandled_error_hides_internals(app, client):
    @app.get("/api/_test/crash")
    def crash():
        raise RuntimeError("secret internals")

    r = client.get("/api/_test/crash")
    assert r.status_code == 500
    assert_error_envelope(r.json(), "internal_error")
    assert "secret" not in r.text


# ---- planned endpoints are not functional ---------------------------------

def test_planned_listing_is_marked_planned(client):
    body = client.get("/api/planned-endpoints").json()
    assert len(body["endpoints"]) == len(PLANNED_ENDPOINTS) > 0
    assert all(e["status"] == "planned" for e in body["endpoints"])


def test_planned_endpoints_are_not_registered(client, app):
    registered = {(m, r.path) for r in app.routes for m in getattr(r, "methods", ())}
    for e in PLANNED_ENDPOINTS:
        assert (e.method, e.path) not in registered
    assert client.post("/api/benchmarks").status_code == 404
    assert client.post("/api/simulations").status_code == 404


def test_planned_schemas_exist_and_validate():
    for e in PLANNED_ENDPOINTS:
        for name in (e.request_schema, e.response_schema):
            if name and "/" not in name:
                assert hasattr(files, name) or hasattr(operations, name), name
    ok = operations.SimulationRequest(
        logical_data_tb=1, assumed_dedup_ratio=2, avg_chunk_size_bytes=16384
    )
    assert ok.months == 12
    with pytest.raises(ValueError):
        operations.SimulationRequest(logical_data_tb=1, assumed_dedup_ratio=0.5, avg_chunk_size_bytes=1)
    with pytest.raises(ValueError):
        files.FileChunkInfo(chunk_index=0, chunk_hash="zz", chunk_size=1, ref_count=0)


def test_health_exposes_upload_limit(client, settings):
    assert client.get("/api/health").json()["max_upload_bytes"] == settings.max_upload_bytes
