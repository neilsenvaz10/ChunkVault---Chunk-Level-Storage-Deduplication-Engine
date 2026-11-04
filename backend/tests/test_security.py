"""Security regression tests for the local-prototype threat model (see docs/verification/security-review.md)."""

import asyncio
import io
import json

import pytest
from fastapi.testclient import TestClient

from app.api.guard import LocalAccessGuard, host_name
from app.benchmarking import worker
from app.core.config import Settings
from app.core.errors import ConfigurationError, scrub_paths
from app.main import create_app
from tests.conftest import make_data
from tests.test_storage_api import err

EVIL = {"origin": "http://evil.example"}
GOOD = {"origin": "http://localhost:5173"}


def upload(client, name="a.bin", data=b"x" * 100, headers=None):
    return client.post("/api/files", files={"file": (name, data)}, headers=headers or {})


# ---- cross-site request forgery (simple requests do not need a CORS preflight) ---------------------

def test_cross_site_upload_is_rejected_and_stores_nothing(client, settings):
    err(upload(client, headers=EVIL), 403, "origin_not_allowed")
    assert client.get("/api/files").json()["total"] == 0
    assert not list(settings.chunks_dir.rglob("*.*"))


@pytest.mark.parametrize("method,path", [("post", "/api/integrity/reconcile"), ("post", "/api/integrity/audit"),
                                         ("delete", "/api/files/" + "0" * 32), ("post", "/api/simulations"),
                                         ("post", "/api/benchmarks"), ("post", "/api/reliability/experiments"),
                                         ("post", "/api/benchmarks/" + "0" * 12 + "/cancel")])
def test_every_mutating_or_costly_endpoint_rejects_foreign_origins(client, method, path):
    err(getattr(client, method)(path, headers=EVIL), 403, "origin_not_allowed")


def test_literal_null_origin_is_rejected(client):
    err(client.post("/api/integrity/reconcile", headers={"origin": "null"}), 403, "origin_not_allowed")


def test_configured_origin_and_clients_without_origin_still_work(client):
    assert upload(client, headers=GOOD).status_code == 201
    assert upload(client).status_code == 201  # curl / scripts send no Origin
    assert client.post("/api/integrity/audit", headers={"origin": "HTTP://LOCALHOST:5173/"}).status_code == 200  # normalised


def test_reads_are_not_blocked_but_the_browser_is_not_granted_access(client):
    r = client.get("/api/health", headers=EVIL)
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
    pre = client.options("/api/files", headers={**EVIL, "access-control-request-method": "POST"})
    assert "access-control-allow-origin" not in pre.headers


def test_json_endpoints_cannot_be_triggered_with_a_simple_content_type(client):
    for path, body in (("/api/benchmarks", {"kind": "pipeline", "workload": "small"}), ("/api/simulations", {}),
                       ("/api/reliability/experiments", {"scenario_id": "cdc_failure"}), ("/api/integrity/audit", {})):
        r = client.post(path, content=json.dumps(body), headers={"content-type": "text/plain"})
        assert r.status_code == 422, path
    assert client.get("/api/benchmarks").json()["running"] is None  # nothing started


# ---- DNS rebinding ------------------------------------------------------------------------------------

@pytest.mark.parametrize("host", ["evil.example", "attacker.test:8000", "localhost.evil.example", "127.0.0.1.evil.example", "192.168.1.5:8000", ""])
def test_foreign_host_headers_are_rejected(client, host):
    r = client.get("/api/health", headers={"host": host})
    err(r, 400, "host_not_allowed")


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "LOCALHOST:5173", "127.0.0.1:8000", "[::1]:8000", "[::1]"])
def test_local_host_headers_are_accepted(client, host):
    assert client.get("/api/health", headers={"host": host}).status_code == 200


def test_host_name_normalisation():
    assert [host_name(h) for h in ("LocalHost:8000", "[::1]:9", "[::1]", "127.0.0.1", "a.b:1")] == [
        "localhost", "[::1]", "[::1]", "127.0.0.1", "a.b"]


def test_allowed_hosts_are_configurable_and_never_a_wildcard(tmp_path):
    assert Settings.from_env({}).allowed_hosts == ("localhost", "127.0.0.1", "[::1]")
    assert Settings.from_env({"CHUNKVAULT_ALLOWED_HOSTS": "vault.local, localhost"}).allowed_hosts == ("vault.local", "localhost")
    with pytest.raises(ConfigurationError):
        Settings.from_env({"CHUNKVAULT_ALLOWED_HOSTS": "*"})
    with pytest.raises(ConfigurationError):
        Settings.from_env({"CHUNKVAULT_ALLOWED_HOSTS": " , "})
    s = Settings(data_dir=tmp_path / "d", db_path=tmp_path / "d" / "x.db", cors_origins=("http://localhost:5173",), allowed_hosts=("vault.local",))
    with TestClient(create_app(s), base_url="http://vault.local") as c:
        assert c.get("/api/health").status_code == 200
    with TestClient(create_app(s), base_url="http://localhost", raise_server_exceptions=False) as c:
        assert c.get("/api/health").status_code == 400


def test_guard_ignores_non_http_scopes():
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    asyncio.run(LocalAccessGuard(app, ("localhost",), ())({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]


# ---- input validation --------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [("../../etc/passwd", "passwd"), ("..\\..\\win.ini", "win.ini"), ("a/b/c.txt", "c.txt")])
def test_path_components_in_filenames_are_discarded(client, settings, name, expected):
    r = upload(client, name)
    assert r.status_code == 201 and r.json()["filename"] == expected
    outside = [p for p in settings.data_dir.parent.rglob("*") if p.is_file() and settings.data_dir not in p.parents]
    assert outside == []  # nothing was written outside the data directory


@pytest.mark.parametrize("name", ["x" * 256, "   ", ".."])
def test_unsafe_filenames_are_rejected(client, name):
    r = client.post("/api/files", files={"file": (name, b"data")})
    assert r.status_code in (422, 400)
    assert client.get("/api/files").json()["total"] == 0


@pytest.mark.parametrize("name", ["a\x00b", "a\nb", "a\rb", "tab\there", "del\x7f"])
def test_control_characters_in_filenames_are_rejected_by_the_sanitiser(name):
    # (HTTP clients percent-encode these in multipart headers, so the rule is tested at its source)
    from app.deduplication.interfaces import EmptyFilenameError
    from app.deduplication.upload import sanitize_filename

    with pytest.raises(EmptyFilenameError):
        sanitize_filename(name)


def test_uploaded_content_is_never_interpreted(client):
    html = b"<script>alert(1)</script>"
    f = upload(client, "x.html", html).json()
    r = client.get(f"/api/files/{f['file_id']}/download")
    assert r.content == html and r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")


@pytest.mark.parametrize("bad", ["1;DROP TABLE files", "' OR '1'='1", "%00", "..", "A" * 32, "0" * 31])
def test_malicious_identifiers_never_reach_sql_or_the_filesystem(client, bad):
    assert client.get(f"/api/files/{bad}").status_code in (404, 422)
    assert client.get(f"/api/chunks/{bad}").status_code in (404, 422)
    assert client.delete(f"/api/files/{bad}").status_code in (404, 422)


def test_database_inspection_is_whitelisted_and_read_only(client):
    for bad in ("sqlite_master", "files;DROP TABLE files", "v_ref_count_mismatch", "operations--"):
        assert client.get(f"/api/inspect/tables/{bad}/rows").status_code in (404, 422)
    paths = client.get("/api/openapi.json").json()["paths"]
    inspect = {p: set(v) for p, v in paths.items() if p.startswith("/api/inspect")}
    assert inspect and all(methods == {"get"} for methods in inspect.values())
    assert client.get("/api/files").json()["total"] == 0  # still intact


def test_no_endpoint_accepts_sql_or_raw_paths(client):
    """Only INPUT names are checked (query/path parameters and request-body fields)."""
    spec = client.get("/api/openapi.json").json()
    schemas = spec["components"]["schemas"]
    names = set()
    for item in spec["paths"].values():
        for op in item.values():
            names |= {p["name"].lower() for p in op.get("parameters", [])}
            for content in op.get("requestBody", {}).get("content", {}).values():
                ref = content.get("schema", {}).get("$ref")
                if ref:
                    names |= {k.lower() for k in schemas[ref.split("/")[-1]].get("properties", {})}
    assert names, "expected some inputs"
    assert names.isdisjoint({"sql", "statement", "filepath", "file_path", "path", "directory", "dir", "command", "cmd", "where", "expression"})


# ---- information disclosure ---------------------------------------------------------------------------------

def test_scrub_paths_removes_filesystem_paths_only():
    leaked = r"PermissionError: [WinError 5] Access is denied: 'C:\Users\DELL\AppData\Local\Temp\cv\bench\x.progress.json.tmp' -> '/var/data/x.json'"
    out = scrub_paths(leaked)
    assert "Users" not in out and "AppData" not in out and "/var/data" not in out and "<path>" in out
    for harmless in ("workload must be one of small, medium, large", "size_bytes must be between 16384 and 4194304", "ratio 2/3 reused"):
        assert scrub_paths(harmless) == harmless


def test_worker_failures_do_not_leak_paths(tmp_path, monkeypatch):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"job_id": "abc", "kind": "pipeline", "workload": "small", "size_bytes": 16384, "iterations": 1, "seed": 1,
                                "cdc": {"min_chunk_size": 4096, "avg_chunk_size": 16384, "max_chunk_size": 131072, "window_size": 64}}))

    def boom(*a, **k):
        raise PermissionError(5, r"Access is denied: C:\Users\DELL\Secret\store\db.sqlite")

    monkeypatch.setattr(worker, "run_benchmark", boom)
    assert worker.main(["w", str(spec), str(tmp_path / "r.json"), str(tmp_path / "p.json")]) == 1
    error = json.loads((tmp_path / "r.json").read_text())["error"]
    assert "Secret" not in error and "DELL" not in error and "<path>" in error


def test_unexpected_errors_return_a_generic_message(app, client, settings):
    @app.get("/api/_boom")
    def boom():
        raise RuntimeError(f"secret at {settings.data_dir}")

    r = client.get("/api/_boom")
    assert r.status_code == 500 and str(settings.data_dir) not in r.text and "secret" not in r.text


# ---- experiments and maintenance are not inadvertently exposed -----------------------------------------------

def test_fault_lab_and_maintenance_defaults_are_safe(client):
    assert Settings.from_env({}).fault_lab_enabled is False
    err(client.post("/api/reliability/experiments", json={"scenario_id": "cdc_failure"}), 403, "fault_lab_disabled")
    assert Settings.from_env({}).cors_origins == ("http://localhost:5173", "http://127.0.0.1:5173")
    with pytest.raises(ConfigurationError):
        Settings.from_env({"CHUNKVAULT_CORS_ORIGINS": "*"})


def test_upload_bodies_are_bounded_before_parsing(client, settings):
    big = io.BytesIO(b"x" * (settings.max_upload_bytes + 2 * 1024 * 1024))
    err(client.post("/api/files", files={"file": ("big.bin", big)}), 413, "upload_too_large")
    assert make_data(1, 10)  # fixture sanity


def test_guard_is_the_outermost_middleware(app):
    names = [m.cls.__name__ for m in app.user_middleware]
    assert names[0] == "LocalAccessGuard"  # first in the list = outermost
