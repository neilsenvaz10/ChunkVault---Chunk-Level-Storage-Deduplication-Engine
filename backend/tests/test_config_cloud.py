"""Cloud settings: validation, env parsing, and the guarantee that secrets never reach repr/describe/error messages."""

import pytest

from app.core.config import CloudSettings, Settings
from app.core.errors import ConfigurationError

DB = "postgresql://cv_app.projref:p4ssw0rd-SECRET@aws-0-eu.pooler.supabase.com:5432/postgres"
KEY = "service-key-" + "k" * 30
DEMO = "demo-token-" + "d" * 30
ADMIN = "admin-token-" + "a" * 30
SECRETS = (DB, "p4ssw0rd-SECRET", KEY, DEMO, ADMIN)


def cloud(**kw):
    base = dict(database_url=DB, supabase_url="https://abc.supabase.co", supabase_service_key=KEY, demo_token=DEMO, admin_token=ADMIN)
    base.update(kw)
    return CloudSettings(**base)


def env(**kw):
    e = {"CHUNKVAULT_BACKEND": "cloud", "DATABASE_URL": DB, "SUPABASE_URL": "https://abc.supabase.co",
         "SUPABASE_SERVICE_KEY": KEY, "CHUNKVAULT_DEMO_TOKEN": DEMO, "CHUNKVAULT_ADMIN_TOKEN": ADMIN,
         "CHUNKVAULT_CORS_ORIGINS": "https://chunkvault.vercel.app"}
    e.update(kw)
    return {k: v for k, v in e.items() if v is not None}


def test_default_backend_is_local_and_needs_no_cloud_values():
    s = Settings.from_env({})
    assert s.backend == "local" and s.cloud is None


def test_cloud_env_parses_with_conservative_defaults():
    s = Settings.from_env(env())
    assert s.backend == "cloud" and s.cloud.bucket == "chunkvault-chunks"
    assert s.max_upload_bytes == 10 * 1024 * 1024
    assert (s.cloud.io_workers, s.cloud.batch_chunks, s.cloud.verify_on_reuse) == (4, 64, "exists")
    assert s.cloud.stored_bytes_cap == 300 * 1024 * 1024


@pytest.mark.parametrize("missing", ["DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "CHUNKVAULT_DEMO_TOKEN", "CHUNKVAULT_ADMIN_TOKEN"])
def test_each_required_cloud_value_is_enforced_by_name(missing):
    with pytest.raises(ConfigurationError, match=missing):
        Settings.from_env(env(**{missing: None}))


def test_unknown_backend_is_rejected():
    with pytest.raises(ConfigurationError):
        Settings.from_env({"CHUNKVAULT_BACKEND": "mongo"})


@pytest.mark.parametrize("kw", [
    dict(database_url="mysql://u:p@h/db"), dict(database_url="postgresql:///nohost"),
    dict(supabase_url="ftp://x"), dict(supabase_url="http://abc.supabase.co"),
    dict(bucket="Bad Bucket!"), dict(demo_token="short"), dict(admin_token="x" * 10),
    dict(demo_token=ADMIN), dict(admin_token=KEY), dict(supabase_service_key="has space " + "k" * 30),
    dict(verify_on_reuse="sometimes"), dict(io_workers=0), dict(io_workers=99), dict(batch_chunks=0),
    dict(pool_min_size=5, pool_max_size=2), dict(stored_bytes_cap=0), dict(orphan_min_age_seconds=-1),
    dict(pg_schema="Bad-Schema"), dict(db_statement_timeout_ms=10),
])
def test_invalid_cloud_settings_are_rejected(kw):
    with pytest.raises(ConfigurationError):
        cloud(**kw)


def test_http_is_allowed_only_for_a_local_supabase_emulator():
    assert cloud(supabase_url="http://localhost:54321").supabase_url.startswith("http://localhost")


def test_cloud_backend_rules_on_upload_limit_and_cors(tmp_path):
    c = cloud()
    base = dict(data_dir=tmp_path, db_path=tmp_path / "x.db", backend="cloud", cloud=c)
    with pytest.raises(ConfigurationError, match="https"):
        Settings(cors_origins=("http://evil.example",), **base)
    Settings(cors_origins=("http://localhost:5173", "https://app.example"), **base)
    with pytest.raises(ConfigurationError, match="quota"):
        Settings(cors_origins=("https://a.example",), max_upload_bytes=c.stored_bytes_cap + 1, **base)
    with pytest.raises(ConfigurationError, match="requires cloud settings"):
        Settings(data_dir=tmp_path, db_path=tmp_path / "x.db", cors_origins=("https://a.example",), backend="cloud")


def test_secrets_never_appear_in_repr_describe_or_str():
    c = cloud()
    s = Settings.from_env(env())
    shown = [repr(c), str(c), repr(s), repr(s.cloud), str(c.describe()), str(s.cloud.describe())]
    for text in shown:
        for secret in SECRETS:
            assert secret not in text


def test_validation_messages_never_contain_secret_values():
    for kw in (dict(demo_token=ADMIN), dict(admin_token="short"), dict(database_url="mysql://u:p4ssw0rd-SECRET@h/db"),
               dict(supabase_service_key="has space " + "k" * 30)):
        with pytest.raises(ConfigurationError) as e:
            cloud(**kw)
        for secret in SECRETS:
            assert secret not in str(e.value)


def test_secret_values_lists_exact_strings_for_log_redaction():
    vals = cloud().secret_values()
    assert set(vals) == {KEY, DEMO, ADMIN, "p4ssw0rd-SECRET"}


def test_warnings_flag_risky_connection_strings_without_leaking():
    direct = cloud(database_url="postgresql://postgres:pw@db.abcdefgh.supabase.co:5432/postgres")
    assert any("pooler" in w for w in direct.warnings())
    txn = cloud(database_url="postgresql://u:pw@aws-0.pooler.supabase.com:6543/postgres")
    assert any("transaction pooler" in w for w in txn.warnings())
    assert cloud().warnings() == []
    assert any("verify_on_reuse" in w for w in cloud(verify_on_reuse="none").warnings())
    for w in direct.warnings() + txn.warnings():
        assert "pw" not in w.split()


def test_numeric_env_values_must_parse():
    with pytest.raises(ConfigurationError):
        Settings.from_env(env(CHUNKVAULT_CLOUD_IO_WORKERS="four"))
    with pytest.raises(ConfigurationError):
        Settings.from_env(env(CHUNKVAULT_ORPHAN_MIN_AGE_SECONDS="soon"))
    assert Settings.from_env(env(CHUNKVAULT_CLOUD_IO_WORKERS="2")).cloud.io_workers == 2


def test_storage_client_settings_have_safe_defaults_and_are_validated():
    c = cloud()
    assert (c.storage_connect_timeout_seconds, c.storage_timeout_seconds, c.storage_max_attempts) == (5.0, 20.0, 3)
    assert c.storage_deadline_seconds == 60.0 and c.storage_max_object_bytes == 8 * 1024 * 1024
    for kw in (dict(storage_timeout_seconds=0), dict(storage_timeout_seconds=9999), dict(storage_max_attempts=0),
               dict(storage_max_attempts=50), dict(storage_deadline_seconds=0), dict(storage_connect_timeout_seconds=-1),
               dict(storage_max_object_bytes=10), dict(storage_timeout_seconds=True)):
        with pytest.raises(ConfigurationError):
            cloud(**kw)


def test_storage_env_overrides_are_parsed():
    s = Settings.from_env(env(CHUNKVAULT_STORAGE_TIMEOUT_SECONDS="7.5", CHUNKVAULT_STORAGE_MAX_ATTEMPTS="2"))
    assert (s.cloud.storage_timeout_seconds, s.cloud.storage_max_attempts) == (7.5, 2)
    with pytest.raises(ConfigurationError):
        Settings.from_env(env(CHUNKVAULT_STORAGE_TIMEOUT_SECONDS="slow"))
