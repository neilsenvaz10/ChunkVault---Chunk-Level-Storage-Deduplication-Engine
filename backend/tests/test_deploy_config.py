"""Deployment foundations (C5E-1): pinned dependencies that really cover the code, and the Render blueprint.

These guard against the failure C5D found: ``psycopg_pool`` was imported by the cloud backend but missing from requirements.txt, so a
fresh Render build would have crashed at start-up.
"""

import importlib.metadata as md
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
REQ = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
DEV = (BACKEND / "requirements-dev.txt").read_text(encoding="utf-8")
RENDER = (ROOT / "render.yaml").read_text(encoding="utf-8")


def pins(text):
    out = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            m = re.fullmatch(r"([A-Za-z0-9_.\-]+)(\[[^\]]+\])?==([0-9][A-Za-z0-9.\-+]*)", line)
            assert m, f"not an exact pin: {raw!r}"
            out[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(3)
    return out


# ---------------------------------------------------------------------------------------------------- dependencies

def test_every_runtime_requirement_is_an_exact_pin():
    p = pins(REQ)
    assert len(p) >= 15
    assert not [n for n in ("pytest", "alembic", "sqlalchemy") if n in p], "test/migration tools do not belong on the web service"


def test_the_connection_pool_dependency_is_present_and_pinned():
    p = pins(REQ)
    assert "psycopg-pool" in p and "psycopg" in p and "psycopg-binary" in p
    assert "psycopg[binary,pool]==" in REQ.replace(" ", "")  # the extras the app needs, spelled out


def test_dev_requirements_extend_the_runtime_set_and_pin_the_tools():
    assert re.search(r"^-r requirements\.txt$", DEV, re.M)
    p = pins(DEV)
    assert {"pytest", "alembic", "sqlalchemy"} <= set(p)


def test_every_third_party_module_the_application_imports_is_covered_by_a_pinned_requirement():
    """The check that would have caught the missing psycopg-pool: read the real imports of backend/app and map them to packages."""
    pinned = set(pins(REQ))
    dist_of = md.packages_distributions()
    mods = set()
    for path in (BACKEND / "app").rglob("*.py"):
        for m in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", path.read_text(encoding="utf-8"), re.M):
            mods.add(m.group(1))
    third_party = {m for m in mods if m not in sys.stdlib_module_names and m not in ("app", "__future__")}
    missing = {}
    for m in sorted(third_party):
        dists = {re.sub(r"[-_.]+", "-", d).lower() for d in dist_of.get(m, [])}
        if not dists:
            continue  # not installed here (cannot happen for code the tests import)
        if not dists & pinned:
            missing[m] = sorted(dists)
    assert not missing, f"imported by app/ but not in requirements.txt: {missing}"
    assert {"psycopg_pool", "psycopg", "httpx", "fastapi", "pydantic"} <= third_party  # the scan really saw the cloud modules


# ---------------------------------------------------------------------------------------------------- the Render blueprint

def env_vars():
    block = RENDER.split("envVars:", 1)[1]
    out = {}
    for m in re.finditer(r"- key: (\S+)\n\s+(value|sync): (.+)", block):
        out[m.group(1)] = (m.group(2), m.group(3).strip())
    return out


def test_render_blueprint_runs_exactly_one_worker_bound_to_port_with_a_factory_start():
    start = re.search(r"startCommand: (.+)", RENDER).group(1)
    assert start.startswith("uvicorn app.main:create_app --factory ")
    assert "--port $PORT" in start and "--host 0.0.0.0" in start and re.search(r"--workers 1(\s|$)", start)
    assert re.search(r"^\s*plan: free$", RENDER, re.M) and re.search(r"^\s*rootDir: backend$", RENDER, re.M)
    assert re.search(r"^\s*autoDeploy: false$", RENDER, re.M)  # nothing deploys by itself


def test_render_health_check_is_the_database_free_liveness_route():
    assert re.search(r"^\s*healthCheckPath: /api/health/live$", RENDER, re.M)


def test_render_blueprint_pins_python_and_uses_a_temporary_data_directory():
    v = env_vars()
    assert v["PYTHON_VERSION"] == ("value", "3.12.12") and "3.12.12" in REQ  # the version the pins were verified on
    kind, path = v["CHUNKVAULT_DATA_DIR"]
    assert kind == "value" and path.startswith("/tmp/")
    assert v["CHUNKVAULT_BACKEND"] == ("value", "cloud")


def test_render_blueprint_contains_no_secret_and_never_the_owner_connection():
    v = env_vars()
    for name in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "CHUNKVAULT_DEMO_TOKEN", "CHUNKVAULT_ADMIN_TOKEN", "CHUNKVAULT_CORS_ORIGINS"):
        assert v[name] == ("sync", "false"), f"{name} must be entered in the dashboard, not written here"
    assert "OWNER_DATABASE_URL" not in v
    body = "\n".join(l for l in RENDER.splitlines() if not l.lstrip().startswith("#"))
    for needle in ("postgresql://", "postgres://", "sb_secret_", "eyJ", "Bearer "):
        assert needle not in body


def test_the_documented_start_command_really_starts_the_app_and_serves_liveness(tmp_path):
    """Run the blueprint's own command (host swapped to loopback) with the local backend and a temporary data directory."""
    start = re.search(r"startCommand: (.+)", RENDER).group(1)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    argv = start.replace("$PORT", str(port)).replace("--host 0.0.0.0", "--host 127.0.0.1").split()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CHUNKVAULT_", "DATABASE_URL", "OWNER_DATABASE_URL", "SUPABASE_"))}
    env.update(CHUNKVAULT_NO_DOTENV="1", CHUNKVAULT_DATA_DIR=str(tmp_path / "d"), CHUNKVAULT_BACKEND="local")
    proc = subprocess.Popen([sys.executable, "-m", *argv], cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 40
        r = None
        while time.time() < deadline:
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/api/health/live", timeout=2, headers={"Host": "chunkvault-api.onrender.com"})
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        assert r is not None and r.status_code == 200 and r.json()["status"] == "ok"  # exempt from the Host allow-list
        assert httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=5, headers={"Host": "chunkvault-api.onrender.com"}).status_code == 400
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
