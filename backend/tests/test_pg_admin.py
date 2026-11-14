"""Role setup: rerun safety for a non-superuser owner, password handling (verifier only, never cleartext) and the owner-URL rules.

Found by the C5B-1 rehearsal: a CREATEROLE owner that is not a superuser (like Supabase's `postgres` role in other projects) could not
rerun the script because every run issued a blanket ALTER ROLE touching attributes the owner does not hold itself.
"""

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit

import psycopg
import pytest

from app.database.pg_admin import SCRAM_ITERATIONS, scram_verifier, setup_app_role
from tests.pg_fixtures import BACKEND, apply_migrations, pg_url, requires_postgres, schema_conn, schema_name, stamp_version  # noqa: F401

ROOT = BACKEND.parent
pytestmark = [pytest.mark.postgres, requires_postgres]


# ---------------------------------------------------------------------------------------------------- the verifier itself

def test_scram_verifier_matches_libpq_for_the_same_salt_and_iterations(schema_conn):
    pw = "correct horse battery staple"
    ours = scram_verifier(pw)
    theirs = schema_conn.pgconn.encrypt_password(pw.encode(), b"someone", b"scram-sha-256").decode()  # libpq's own implementation
    assert ours.startswith("SCRAM-SHA-256$4096:") and theirs.startswith("SCRAM-SHA-256$4096:")
    head, rest = theirs.split("$", 1)
    iters, salt_b64 = rest.split("$", 1)[0].split(":")
    import base64
    again = scram_verifier(pw, salt=base64.b64decode(salt_b64), iterations=int(iters))
    assert again == theirs  # bit-for-bit what libpq/PostgreSQL would produce
    assert scram_verifier(pw) != scram_verifier(pw) and SCRAM_ITERATIONS == 4096  # random salt each time


# ---------------------------------------------------------------------------------------------------- statements and storage

class Spy:
    """A connection wrapper that records every statement text it is asked to run."""

    def __init__(self, conn):
        self._c, self.statements = conn, []

    def execute(self, query, *a, **k):
        self.statements.append(query.as_string(self._c) if hasattr(query, "as_string") else str(query))
        return self._c.execute(query, *a, **k)

    def __getattr__(self, name):
        return getattr(self._c, name)


def test_the_cleartext_password_never_appears_in_any_statement_and_the_server_stores_a_verifier(schema_conn, schema_name):
    role, pw = "cv_pw_" + uuid.uuid4().hex[:8], "S3cret-" + uuid.uuid4().hex
    spy = Spy(schema_conn)
    try:
        setup_app_role(spy, role, schema_name, pw)
        setup_app_role(spy, role, schema_name, pw + "-rotated")
        assert spy.statements and not any(pw in s for s in spy.statements)  # not in CREATE ROLE, not in ALTER ROLE
        stored = schema_conn.execute("SELECT rolpassword FROM pg_authid WHERE rolname = %s", (role,)).fetchone()[0]
        assert stored.startswith("SCRAM-SHA-256$4096:") and pw not in stored  # stored unchanged: still a verifier, never cleartext
    finally:
        schema_conn.execute("RESET search_path")
        schema_conn.execute(f'DROP OWNED BY "{role}"')
        schema_conn.execute(f'DROP ROLE "{role}"')


def test_an_already_correct_role_issues_no_alter_role_at_all_and_drift_is_fixed_minimally(schema_conn, schema_name):
    role, pw = "cv_rr_" + uuid.uuid4().hex[:8], "pw-" + uuid.uuid4().hex
    try:
        setup_app_role(schema_conn, role, schema_name, pw)
        spy = Spy(schema_conn)
        setup_app_role(spy, role, schema_name)  # rerun, no password
        assert not [s for s in spy.statements if s.startswith("ALTER ROLE") or s.startswith("CREATE ROLE")]
        schema_conn.execute(f'ALTER ROLE "{role}" CREATEDB')  # someone widened the role
        spy = Spy(schema_conn)
        setup_app_role(spy, role, schema_name)
        alters = [s for s in spy.statements if s.startswith("ALTER ROLE")]
        assert alters == [f'ALTER ROLE "{role}" NOCREATEDB']  # exactly the attribute that drifted
        assert schema_conn.execute("SELECT rolcreatedb FROM pg_roles WHERE rolname=%s", (role,)).fetchone()[0] is False
    finally:
        schema_conn.execute("RESET search_path")
        schema_conn.execute(f'DROP OWNED BY "{role}"')
        schema_conn.execute(f'DROP ROLE "{role}"')


def test_rerun_works_for_a_non_superuser_owner_without_replication_or_bypassrls(pg_url, schema_conn, schema_name):
    """The regression: an owner with CREATEROLE only. Before the fix, the second run failed with 'permission denied to alter role'."""
    suffix = uuid.uuid4().hex[:8]
    owner, role, schema = f"cv_own_{suffix}", f"cv_app_{suffix}", f"o_{suffix}"
    db = urlsplit(pg_url).path.lstrip("/")
    schema_conn.execute(f'CREATE ROLE "{owner}" LOGIN CREATEROLE')  # no SUPERUSER, REPLICATION, BYPASSRLS, CREATEDB
    schema_conn.execute(f'GRANT CREATE ON DATABASE "{db}" TO "{owner}"')
    oc = psycopg.connect(pg_url, user=owner, autocommit=True)
    try:
        oc.execute(f'CREATE SCHEMA "{schema}"')
        oc.execute(f'SET search_path TO "{schema}"')
        apply_migrations(oc)
        stamp_version(oc)
        setup_app_role(oc, role, schema, "first-" + uuid.uuid4().hex)
        setup_app_role(oc, role, schema)  # rerun, grants only
        setup_app_role(oc, role, schema, "rotated-" + uuid.uuid4().hex)  # rerun with a new password
        row = schema_conn.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
        assert row == (False, False, False, False, False, True)
        assert schema_conn.execute("SELECT count(*) FROM pg_policies WHERE schemaname=%s", (schema,)).fetchone()[0] == 6  # no duplicates after reruns
    finally:
        oc.close()
        schema_conn.execute("RESET search_path")
        schema_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        for r in (role, owner):
            schema_conn.execute(f'DROP OWNED BY "{r}"')
            schema_conn.execute(f'DROP ROLE "{r}"')


# ---------------------------------------------------------------------------------------------------- the script

def run_setup(args, env_extra):
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "OWNER_DATABASE_URL", "APP_DB_PASSWORD")}
    env.update(CHUNKVAULT_NO_DOTENV="1", **env_extra)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "setup_app_role.py"), *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_script_generates_a_password_file_never_prints_it_and_never_overwrites(pg_url, schema_conn, schema_name, tmp_path):
    role = "cv_gen_" + uuid.uuid4().hex[:8]
    pwfile = tmp_path / "pw.txt"
    try:
        r = run_setup(["--role", role, "--schema", schema_name, "--write-password-file", str(pwfile)], {"OWNER_DATABASE_URL": pg_url, "DATABASE_URL": "postgresql://nobody:x@127.0.0.1:1/none"})
        assert r.returncode == 0, r.stderr[-300:]  # OWNER_DATABASE_URL wins over DATABASE_URL
        pw = pwfile.read_text(encoding="utf-8").strip()
        assert len(pw) >= 40 and pw.replace("-", "").replace("_", "").isalnum()  # strong and URL-safe
        assert pw not in r.stdout + r.stderr and pg_url not in r.stdout + r.stderr
        again = run_setup(["--role", role, "--schema", schema_name, "--write-password-file", str(pwfile)], {"OWNER_DATABASE_URL": pg_url})
        assert again.returncode == 2 and "refusing to overwrite" in again.stderr and pwfile.read_text(encoding="utf-8").strip() == pw
        stored = schema_conn.execute("SELECT rolpassword FROM pg_authid WHERE rolname=%s", (role,)).fetchone()[0]
        assert stored.startswith("SCRAM-SHA-256$") and pw not in stored
    finally:
        schema_conn.execute("RESET search_path")
        if schema_conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
            schema_conn.execute(f'DROP OWNED BY "{role}"')
            schema_conn.execute(f'DROP ROLE "{role}"')


def test_script_falls_back_to_the_environment_password_and_database_url(pg_url, schema_conn, schema_name):
    role, pw = "cv_env_" + uuid.uuid4().hex[:8], "env-" + uuid.uuid4().hex
    try:
        r = run_setup(["--role", role, "--schema", schema_name], {"DATABASE_URL": pg_url, "APP_DB_PASSWORD": pw})
        assert r.returncode == 0 and pw not in r.stdout + r.stderr
        assert run_setup(["--role", role, "--schema", schema_name], {"DATABASE_URL": pg_url}).returncode == 0  # rerun without a password
        none = run_setup(["--role", "cv_never_" + uuid.uuid4().hex[:6], "--schema", schema_name], {})
        assert none.returncode == 2 and "is required" in none.stderr
    finally:
        schema_conn.execute("RESET search_path")
        if schema_conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
            schema_conn.execute(f'DROP OWNED BY "{role}"')
            schema_conn.execute(f'DROP ROLE "{role}"')


def test_alembic_prefers_the_owner_url_over_the_application_url(pg_url, schema_name):
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "OWNER_DATABASE_URL")}
    env.update(CHUNKVAULT_NO_DOTENV="1", CHUNKVAULT_PG_SCHEMA=schema_name, OWNER_DATABASE_URL=pg_url, DATABASE_URL="postgresql://app:x@127.0.0.1:1/none")
    try:
        r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        assert pg_url not in r.stdout + r.stderr
    finally:
        with psycopg.connect(pg_url, autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
