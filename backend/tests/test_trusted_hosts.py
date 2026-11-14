"""Host allow-list: the platform's own hostname is accepted, arbitrary Host headers never are, liveness is exempt (and only liveness)."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.main import create_app

CLOUD = {"CHUNKVAULT_BACKEND": "cloud", "DATABASE_URL": "postgresql://u:p@db.example:5432/d", "SUPABASE_URL": "https://x.supabase.co",
         "SUPABASE_SERVICE_KEY": "k" * 40, "CHUNKVAULT_DEMO_TOKEN": "d" * 40, "CHUNKVAULT_ADMIN_TOKEN": "a" * 40,
         "CHUNKVAULT_CORS_ORIGINS": "https://app.example"}


# ----------------------------------------------------------------------------------------------- configuration

def test_the_render_hostname_is_added_to_the_default_allow_list():
    s = Settings.from_env({**CLOUD, "RENDER_EXTERNAL_HOSTNAME": "chunkvault-api.onrender.com"})
    assert s.allowed_hosts == ("localhost", "127.0.0.1", "[::1]", "chunkvault-api.onrender.com")
    assert "*" not in s.allowed_hosts


def test_the_render_hostname_is_added_to_an_explicit_list_without_duplicates_and_lowercased():
    s = Settings.from_env({**CLOUD, "CHUNKVAULT_ALLOWED_HOSTS": "api.mydomain.example,chunkvault-api.onrender.com", "RENDER_EXTERNAL_HOSTNAME": "ChunkVault-API.onrender.com"})
    assert s.allowed_hosts == ("api.mydomain.example", "chunkvault-api.onrender.com")


def test_without_the_platform_variable_nothing_changes():
    assert Settings.from_env(CLOUD).allowed_hosts == ("localhost", "127.0.0.1", "[::1]")


def test_the_platform_variable_is_ignored_by_the_local_backend():
    assert Settings.from_env({"RENDER_EXTERNAL_HOSTNAME": "x.onrender.com"}).allowed_hosts == ("localhost", "127.0.0.1", "[::1]")


@pytest.mark.parametrize("bad", ["*", "*.onrender.com", "a b", "x.onrender.com/path", "x.onrender.com:443", "-bad.example", "bad-.example", "a..b",
                                 "x.onrender.com\nevil.example", "evil.example,x.onrender.com", "a" * 64 + ".example", ("a." * 130) + "example", "http://x.onrender.com"])
def test_malformed_platform_hostnames_are_refused_not_trusted(bad):
    with pytest.raises(ConfigurationError, match="RENDER_EXTERNAL_HOSTNAME"):
        Settings.from_env({**CLOUD, "RENDER_EXTERNAL_HOSTNAME": bad})


def test_a_wildcard_allow_list_is_still_refused():
    with pytest.raises(ConfigurationError):
        Settings.from_env({**CLOUD, "CHUNKVAULT_ALLOWED_HOSTS": "*"})


# ----------------------------------------------------------------------------------------------- behaviour of the running app

@pytest.fixture
def app_client(tmp_path):
    s = Settings(data_dir=tmp_path / "d", db_path=tmp_path / "d" / "t.db", cors_origins=("https://app.example",),
                 allowed_hosts=("localhost", "chunkvault-api.onrender.com"))
    with TestClient(create_app(s)) as c:
        yield c


@pytest.mark.parametrize("host,expected", [
    ("chunkvault-api.onrender.com", 200), ("CHUNKVAULT-API.ONRENDER.COM", 200), ("chunkvault-api.onrender.com:443", 200), ("localhost:8000", 200),
    ("evil.example", 400), ("chunkvault-api.onrender.com.evil.example", 400), ("evil.chunkvault-api.onrender.com", 400),  # no suffix/prefix matching
    ("onrender.com", 400), ("", 400), ("127.0.0.2", 400), ("chunkvault-api.onrender.com@evil.example", 400),
])
def test_only_the_exact_allowed_names_reach_the_api(app_client, host, expected):
    r = app_client.get("/api/system/config", headers={"Host": host})
    assert r.status_code == expected
    if expected == 400:
        assert r.json()["error"]["code"] == "host_not_allowed"


def test_liveness_is_reachable_under_any_host_name_but_nothing_else_is(app_client):
    for host in ("evil.example", "pod-7f9c.internal", ""):
        r = app_client.get("/api/health/live", headers={"Host": host})
        assert r.status_code == 200 and r.json()["status"] == "ok"
        assert app_client.get("/api/health", headers={"Host": host}).status_code == 400  # readiness stays behind the allow-list
        assert app_client.get("/api/health/ready", headers={"Host": host}).status_code == 400
        assert app_client.get("/api/health/live/", headers={"Host": host}).status_code == 400  # exact path only
        assert app_client.get("/api/health/liveness", headers={"Host": host}).status_code == 400
        assert app_client.get("/api/files", headers={"Host": host}).status_code == 400
    assert app_client.post("/api/health/live", headers={"Host": "evil.example"}).status_code == 400  # only GET/HEAD are exempt


def test_the_liveness_response_reveals_nothing_beyond_name_and_version(app_client):
    body = app_client.get("/api/health/live", headers={"Host": "evil.example"}).json()
    assert set(body) == {"status", "service", "version"}
