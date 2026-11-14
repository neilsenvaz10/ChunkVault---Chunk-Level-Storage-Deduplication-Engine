"""API metadata per deployment mode. No database or storage is contacted: the lifespan is never started, only the route table
and the generated OpenAPI document are inspected."""

from fastapi.testclient import TestClient

from app.core.config import CloudSettings, Settings
from app.main import create_app


def _cloud_app(tmp_path):
    cloud = CloudSettings(database_url="postgresql://unused.invalid/db", supabase_url="https://unused.invalid", supabase_service_key="k" * 40,
                          demo_token="d" * 30, admin_token="a" * 30)
    return create_app(Settings(data_dir=tmp_path / "d", db_path=tmp_path / "d" / "x.db", cors_origins=("https://app.example",),
                               backend="cloud", cloud=cloud, allowed_hosts=("testserver", "localhost")))


def test_local_mode_keeps_the_interactive_docs_and_the_local_description(app):
    client = TestClient(app, base_url="http://localhost")
    assert client.get("/api/docs").status_code == 200
    info = client.get("/api/openapi.json").json()["info"]
    assert "local use only" in info["description"]


def test_cloud_mode_has_no_swagger_page_but_keeps_openapi_for_the_in_app_explorer(tmp_path):
    client = TestClient(_cloud_app(tmp_path), base_url="http://localhost")
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/redoc").status_code == 404
    r = client.get("/api/openapi.json")
    assert r.status_code == 200  # the Architecture page's API explorer reads it
    text = r.json()["info"]["description"]
    assert "No authentication" not in text and "local use only" not in text
    assert "demo token" in text and "admin token" in text


def test_the_cloud_description_never_contains_a_secret(tmp_path):
    r = TestClient(_cloud_app(tmp_path), base_url="http://localhost").get("/api/openapi.json")
    for secret in ("d" * 30, "a" * 30, "k" * 40, "unused.invalid"):
        assert secret not in r.text


# ------------------------------------------------------------------------------------------------------------- README accuracy
def _readme() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")


def test_readme_documents_both_modes_the_roles_and_the_limits():
    text = _readme()
    for needle in ("SQLite", "Supabase PostgreSQL", "private Supabase Storage bucket", "Vercel Hobby", "Render Free",
                   "CHUNKVAULT_DEMO_TOKEN", "CHUNKVAULT_ADMIN_TOKEN", "10 MiB", "300 MiB", "OWNER_DATABASE_URL"):
        assert needle in text, needle


def test_readme_no_longer_claims_the_whole_application_has_no_authentication():
    text = _readme()
    assert "there is no authentication" not in text.lower()
    assert "local mode has no authentication" in text and "cloud mode is authenticated" in text


def test_readme_contains_variable_names_only_never_values():
    import re
    text = _readme()
    assert not re.search(r"postgres(ql)?://[^\s`<>]+:[^\s`<>@]+@", text)
    assert not re.search(r"eyJ[A-Za-z0-9_-]{15,}\.|sb_secret_|sb_publishable_", text)
    assert not re.search(r"(TOKEN|SERVICE_KEY|DATABASE_URL)\s*=\s*\S", text)
