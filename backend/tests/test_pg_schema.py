"""The PostgreSQL schema and triggers keep every invariant of the SQLite schema (tests/test_database_schema.py).

Needs a throw-away PostgreSQL (TEST_DATABASE_URL); skipped otherwise. A pass here is the only evidence for the claim
"the schema works on PostgreSQL" and it is evidence about the server version the tests ran on, nothing more.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import errors as pgerr

from app.database.pg_admin import TABLES, setup_app_role
from app.database.postgres import EXPECTED_SCHEMA_VERSION
from tests.pg_fixtures import (  # noqa: F401  (fixtures are used by name)
    BACKEND, H, add_chunk, add_file, apply_migrations, map_chunk, pg_url, ref, requires_postgres, schema_conn, schema_name, stamp_version,
)

pytestmark = [pytest.mark.postgres, requires_postgres]
SHA = "f" * 64


def test_alembic_upgrade_and_downgrade_in_a_throwaway_schema(pg_url, schema_name):
    env = dict(os.environ, DATABASE_URL=pg_url, CHUNKVAULT_PG_SCHEMA=schema_name)

    def alembic(*args):
        r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-800:]
        assert pg_url not in r.stdout + r.stderr  # the URL is never printed
        return r

    def objects(c):
        return {
            "tables": {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (schema_name,))},
            "functions": {r[0] for r in c.execute("SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s", (schema_name,))},
        }

    with psycopg.connect(pg_url, autocommit=True) as c:
        try:
            alembic("upgrade", "head")
            o = objects(c)
            assert {"files", "chunks", "file_chunks", "operations", "benchmark_results", "v_ref_count_mismatch", "alembic_version"} <= o["tables"]
            assert {"cv_fc_ref_inc", "cv_fc_ref_dec", "cv_fc_check_size", "cv_fc_immutable", "cv_chunks_identity"} <= o["functions"]
            assert c.execute(f'SELECT version_num FROM "{schema_name}".alembic_version').fetchone()[0] == EXPECTED_SCHEMA_VERSION
            alembic("upgrade", "head")  # idempotent: already at head
            alembic("downgrade", "base")
            o = objects(c)
            assert not ({"files", "chunks", "file_chunks", "operations", "benchmark_results", "v_ref_count_mismatch"} & o["tables"])
            assert not o["functions"]
        finally:
            c.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')


def test_alembic_requires_database_url_and_never_runs_without_it(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "OWNER_DATABASE_URL")}
    r = subprocess.run([sys.executable, "-m", "alembic", "current"], cwd=BACKEND, env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "is required" in (r.stderr + r.stdout)


# ------------------------------------------------------------------------------------------------ constraints

@pytest.mark.parametrize("bad", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, ("a" * 63) + "\n"])
def test_chunk_hash_must_be_64_lowercase_hex(schema_conn, bad):
    with pytest.raises(pgerr.CheckViolation):
        schema_conn.execute("INSERT INTO chunks(hash, size_bytes) VALUES (%s, 1)", (bad,))


def test_file_sha256_must_be_null_or_64_hex(schema_conn):
    with pytest.raises(pgerr.CheckViolation):
        add_file(schema_conn, "x", sha="XYZ")
    add_file(schema_conn, "y", sha=None)
    add_file(schema_conn, "z", status="complete", sha=SHA)


def test_a_complete_file_requires_its_sha256_and_status_is_restricted(schema_conn):
    with pytest.raises(pgerr.CheckViolation):
        add_file(schema_conn, "a", status="complete", sha=None)
    with pytest.raises(pgerr.CheckViolation):
        add_file(schema_conn, "b", status="weird")
    for i, st in enumerate(("uploading", "failed", "deleting")):
        add_file(schema_conn, f"ok{i}", status=st)


@pytest.mark.parametrize("sql", [
    "INSERT INTO files(file_id, filename, original_size) VALUES ('', 'a', 1)",
    "INSERT INTO files(file_id, filename, original_size) VALUES ('i', '', 1)",
    "INSERT INTO files(file_id, filename, original_size) VALUES ('i', repeat('x', 256), 1)",
    "INSERT INTO files(file_id, filename, original_size) VALUES ('i', 'a', -1)",
    f"INSERT INTO chunks(hash, size_bytes) VALUES ('{'b' * 64}', 0)",
    f"INSERT INTO chunks(hash, size_bytes, ref_count) VALUES ('{'b' * 64}', 5, -1)",
    f"INSERT INTO chunks(hash, size_bytes, state) VALUES ('{'b' * 64}', 5, 'bogus')",
])
def test_scalar_check_constraints(schema_conn, sql):
    with pytest.raises(pgerr.CheckViolation):
        schema_conn.execute(sql)


def test_manifest_and_operation_checks(schema_conn):
    add_file(schema_conn)
    add_chunk(schema_conn)
    with pytest.raises(pgerr.CheckViolation):
        map_chunk(schema_conn, "f1", -1)
    with pytest.raises(pgerr.CheckViolation):
        schema_conn.execute("INSERT INTO file_chunks VALUES ('f1', 0, %s, 0)", (H("a"),))
    # operations: status/completed_at pairing, failed needs a message, types restricted
    with pytest.raises(pgerr.CheckViolation):
        schema_conn.execute("INSERT INTO operations(operation_type, status) VALUES ('upload', 'succeeded')")
    with pytest.raises(pgerr.CheckViolation):
        schema_conn.execute("INSERT INTO operations(operation_type, status, completed_at) VALUES ('upload', 'running', now())")
    with pytest.raises(pgerr.CheckViolation):
        schema_conn.execute("INSERT INTO operations(operation_type, status, completed_at) VALUES ('upload', 'failed', now())")
    with pytest.raises(pgerr.CheckViolation):
        schema_conn.execute("INSERT INTO operations(operation_type) VALUES ('mystery')")
    schema_conn.execute("INSERT INTO operations(operation_type) VALUES ('upload')")
    schema_conn.execute("INSERT INTO operations(operation_type, status, completed_at, error_message) VALUES ('upload','failed',now(),'boom')")


def test_operation_ids_are_generated_and_cannot_be_forced(schema_conn):
    a = schema_conn.execute("INSERT INTO operations(operation_type) VALUES ('upload') RETURNING operation_id").fetchone()[0]
    b = schema_conn.execute("INSERT INTO operations(operation_type) VALUES ('upload') RETURNING operation_id").fetchone()[0]
    assert b > a
    with pytest.raises(pgerr.GeneratedAlways):
        schema_conn.execute("INSERT INTO operations(operation_id, operation_type) VALUES (99, 'upload')")


# ------------------------------------------------------------------------------------------------ foreign keys

def test_foreign_keys_restrict_cascade_and_set_null(schema_conn):
    add_file(schema_conn)
    add_chunk(schema_conn)
    with pytest.raises(pgerr.ForeignKeyViolation):
        map_chunk(schema_conn, "nofile", 0)
    with pytest.raises(pgerr.ForeignKeyViolation):
        map_chunk(schema_conn, "f1", 0, h=H("c"))
    map_chunk(schema_conn, "f1", 0)
    schema_conn.execute("INSERT INTO operations(file_id, operation_type) VALUES ('f1', 'upload')")
    with pytest.raises(pgerr.IntegrityError) as e:  # a referenced chunk row can never be removed
        schema_conn.execute("DELETE FROM chunks WHERE hash=%s", (H("a"),))
    assert e.value.sqlstate in ("23001", "23503")
    schema_conn.execute("DELETE FROM files WHERE file_id='f1'")
    assert schema_conn.execute("SELECT count(*) FROM file_chunks").fetchone()[0] == 0  # manifest cascades
    assert schema_conn.execute("SELECT file_id FROM operations").fetchone()[0] is None  # history kept, link cleared
    assert ref(schema_conn, H("a")) == 0


def test_duplicate_manifest_position_is_rejected(schema_conn):
    add_file(schema_conn)
    add_chunk(schema_conn)
    map_chunk(schema_conn, "f1", 0)
    with pytest.raises(pgerr.UniqueViolation):
        map_chunk(schema_conn, "f1", 0)


# ------------------------------------------------------------------------------------------------ triggers

def test_ref_count_follows_manifest_rows_including_repeats_and_cascade(schema_conn):
    add_chunk(schema_conn, H("a"))
    add_chunk(schema_conn, H("b"))
    add_file(schema_conn, "f1")
    add_file(schema_conn, "f2")
    for i, h in enumerate((H("a"), H("a"), H("b"))):  # one hash twice inside one file counts twice
        map_chunk(schema_conn, "f1", i, h=h)
    map_chunk(schema_conn, "f2", 0, h=H("a"))
    assert (ref(schema_conn, H("a")), ref(schema_conn, H("b"))) == (3, 1)
    schema_conn.execute("DELETE FROM files WHERE file_id='f1'")  # cascade fires the decrement trigger per removed row
    assert (ref(schema_conn, H("a")), ref(schema_conn, H("b"))) == (1, 0)
    schema_conn.execute("DELETE FROM files WHERE file_id='f2'")
    assert ref(schema_conn, H("a")) == 0


def test_set_based_manifest_insert_fires_the_row_triggers(schema_conn):
    """The production statement will add a whole manifest in one round trip with unnest(); counts must still be exact."""
    add_chunk(schema_conn, H("a"))
    add_chunk(schema_conn, H("b"), size=7)
    add_file(schema_conn, "f1")
    schema_conn.execute(
        "INSERT INTO file_chunks(file_id, chunk_index, chunk_hash, chunk_size) "
        "SELECT 'f1', i, h, s FROM unnest(%s::int[], %s::text[], %s::int[]) AS t(i, h, s)",
        ([0, 1, 2], [H("a"), H("b"), H("a")], [100, 7, 100]))
    assert (ref(schema_conn, H("a")), ref(schema_conn, H("b"))) == (2, 1)


def test_chunk_size_must_match_the_chunk_row(schema_conn):
    add_file(schema_conn)
    add_chunk(schema_conn, size=100)
    with pytest.raises(pgerr.CheckViolation, match="chunk_size must equal"):
        map_chunk(schema_conn, "f1", 0, size=99)
    assert ref(schema_conn, H("a")) == 0


def test_a_failed_batch_leaves_no_partial_manifest_or_count(schema_conn):
    add_chunk(schema_conn, H("a"))
    add_file(schema_conn, "f1")
    with pytest.raises(pgerr.CheckViolation):
        with schema_conn.transaction():
            map_chunk(schema_conn, "f1", 0, h=H("a"))
            map_chunk(schema_conn, "f1", 1, h=H("a"), size=1)  # wrong size: the whole transaction must roll back
    assert schema_conn.execute("SELECT count(*) FROM file_chunks").fetchone()[0] == 0
    assert ref(schema_conn, H("a")) == 0


def test_manifest_rows_are_immutable(schema_conn):
    add_file(schema_conn)
    add_chunk(schema_conn)
    add_chunk(schema_conn, H("b"))
    map_chunk(schema_conn, "f1", 0)
    for stmt in (f"UPDATE file_chunks SET chunk_hash='{H('b')}'", "UPDATE file_chunks SET chunk_index=5",
                 "UPDATE file_chunks SET chunk_size=100"):
        with pytest.raises(pgerr.IntegrityError, match="immutable"):
            schema_conn.execute(stmt)


def test_chunk_identity_is_immutable_but_state_and_counts_can_change(schema_conn):
    add_chunk(schema_conn)
    with pytest.raises(pgerr.IntegrityError, match="immutable"):
        schema_conn.execute("UPDATE chunks SET size_bytes = 5")
    with pytest.raises(pgerr.IntegrityError, match="immutable"):
        schema_conn.execute("UPDATE chunks SET hash = %s", (H("b"),))
    with pytest.raises(pgerr.IntegrityError, match="immutable"):
        schema_conn.execute("UPDATE chunks SET size_bytes = size_bytes")  # naming the column is enough, as in SQLite
    schema_conn.execute("UPDATE chunks SET state = 'corrupt'")
    schema_conn.execute("UPDATE chunks SET ref_count = ref_count")
    assert schema_conn.execute("SELECT state FROM chunks").fetchone()[0] == "corrupt"


def test_audit_view_is_empty_when_consistent_and_reports_tampering(schema_conn):
    add_chunk(schema_conn)
    add_file(schema_conn)
    map_chunk(schema_conn, "f1", 0)
    assert schema_conn.execute("SELECT count(*) FROM v_ref_count_mismatch").fetchone()[0] == 0
    schema_conn.execute("UPDATE chunks SET ref_count = 7")
    assert schema_conn.execute("SELECT hash, recorded_ref_count, actual_ref_count FROM v_ref_count_mismatch").fetchall() == [(H("a"), 7, 1)]


def test_benchmark_results_hold_json(schema_conn):
    schema_conn.execute("INSERT INTO benchmark_results(job_id, kind, spec, result) VALUES ('j1','pipeline',%s::jsonb,%s::jsonb)",
                        ('{"size_bytes": 1}', '{"summary": {"n": 3}}'))
    assert schema_conn.execute("SELECT result->'summary'->>'n' FROM benchmark_results").fetchone()[0] == "3"


# ------------------------------------------------------------------------------------------------ exposure control

def _ensure_supabase_api_roles(pg_url):
    with psycopg.connect(pg_url, autocommit=True) as c:
        for r in ("anon", "authenticated", "service_role"):
            c.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '%s') THEN CREATE ROLE %s NOLOGIN; END IF; END $$" % (r, r))


def test_row_level_security_is_on_and_api_roles_have_no_access(pg_url, schema_name):
    _ensure_supabase_api_roles(pg_url)  # the roles Supabase's public REST API uses; harmless NOLOGIN roles on a throw-away cluster
    with psycopg.connect(pg_url, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{schema_name}"')
        c.execute(f'SET search_path TO "{schema_name}"')
        try:
            # Simulate Supabase's default privileges (new tables are granted to the API roles), then apply the migration.
            c.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema_name}" GRANT ALL ON TABLES TO anon, authenticated, service_role')
            apply_migrations(c)
            for t in TABLES:
                assert c.execute("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(%s)", (f'"{schema_name}"."{t}"',)).fetchone()[0], t
                for role in ("anon", "authenticated", "service_role"):
                    for priv in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                        assert not c.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f'"{schema_name}"."{t}"', priv)).fetchone()[0], (t, role, priv)
            for role in ("anon", "authenticated", "service_role"):
                assert not c.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, f'"{schema_name}".v_ref_count_mismatch')).fetchone()[0]
                assert not c.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, f'"{schema_name}".cv_fc_ref_inc()')).fetchone()[0]
        finally:
            c.execute("RESET search_path")
            c.execute(f'DROP SCHEMA "{schema_name}" CASCADE')


# ------------------------------------------------------------------------------------------------ least-privilege role (E3)

@pytest.fixture
def app_role(pg_url, schema_conn, schema_name):
    role = "cv_app_" + uuid.uuid4().hex[:8]
    password = "pw-" + uuid.uuid4().hex
    # what `alembic upgrade` would have created (owner-owned); setup_app_role gives the app role read-only access
    stamp_version(schema_conn)
    setup_app_role(schema_conn, role, schema_name, password)
    yield role, password
    schema_conn.execute("RESET search_path")
    schema_conn.execute(f'DROP OWNED BY "{role}"')
    schema_conn.execute(f'DROP ROLE "{role}"')


def _as_role(pg_url, role, password, schema):
    c = psycopg.connect(pg_url, user=role, password=password, autocommit=True, options=f"-c search_path={schema}")
    return c


def test_application_role_can_do_exactly_the_engine_workflow(pg_url, schema_name, app_role):
    role, pw = app_role
    with _as_role(pg_url, role, pw, schema_name) as c:
        add_file(c, "f1")
        add_chunk(c, H("a"))
        map_chunk(c, "f1", 0)
        map_chunk(c, "f1", 1)
        assert ref(c, H("a")) == 2  # triggers run for this role
        c.execute("UPDATE files SET status='complete', sha256=%s WHERE file_id='f1'", (SHA,))
        assert c.execute("SELECT count(*) FROM v_ref_count_mismatch").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM files").fetchone()[0] == 1  # RLS policy lets it see its rows
        c.execute("INSERT INTO operations(operation_type) VALUES ('upload')")
        c.execute("INSERT INTO benchmark_results(job_id, kind, spec, result) VALUES ('j','pipeline','{}'::jsonb,'{}'::jsonb)")
        c.execute("DELETE FROM files WHERE file_id='f1'")
        assert ref(c, H("a")) == 0
        c.execute("DELETE FROM chunks")


@pytest.mark.parametrize("stmt", [
    "CREATE TABLE evil(x int)",
    "DROP TABLE files",
    "TRUNCATE chunks",
    "ALTER TABLE files ADD COLUMN x int",
    "ALTER TABLE file_chunks DISABLE TRIGGER ALL",
    "DROP TRIGGER trg_file_chunks_ref_inc ON file_chunks",
    "DROP FUNCTION cv_fc_ref_inc() CASCADE",
    "CREATE FUNCTION evil() RETURNS int LANGUAGE sql AS 'select 1'",
    "DELETE FROM alembic_version",
    "UPDATE alembic_version SET version_num = 'x'",
    "INSERT INTO alembic_version VALUES ('x')",
    "ALTER TABLE files DISABLE ROW LEVEL SECURITY",
    "CREATE ROLE evil_role",
    "CREATE DATABASE evil_db",
])
def test_application_role_cannot_change_structure_or_see_migration_state(pg_url, schema_name, app_role, stmt):
    role, pw = app_role
    with _as_role(pg_url, role, pw, schema_name) as c:
        with pytest.raises(pgerr.InsufficientPrivilege):
            c.execute(stmt)


def test_application_role_can_read_but_never_change_the_schema_version(pg_url, schema_name, app_role):
    role, pw = app_role
    with _as_role(pg_url, role, pw, schema_name) as c:
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == EXPECTED_SCHEMA_VERSION


def test_application_role_has_no_dangerous_attributes(schema_conn, app_role):
    role, _ = app_role
    row = schema_conn.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
    assert row == (False, False, False, False, False, True)
    assert not schema_conn.execute("SELECT has_schema_privilege(%s, current_schema(), 'CREATE')", (role,)).fetchone()[0]


def test_setup_app_role_is_idempotent_and_refuses_unsafe_input(pg_url, schema_conn, schema_name, app_role):
    role, pw = app_role
    setup_app_role(schema_conn, role, schema_name)  # no password: grants only, role untouched
    setup_app_role(schema_conn, role, schema_name, pw)
    with _as_role(pg_url, role, pw, schema_name) as c:
        assert c.execute("SELECT count(*) FROM files").fetchone()[0] == 0
    with pytest.raises(ValueError, match="password is required"):
        setup_app_role(schema_conn, "cv_app_new_" + uuid.uuid4().hex[:6], schema_name)
    for bad in ('x"; DROP TABLE files; --', "Upper", "", "a" * 64, "with space"):
        with pytest.raises(ValueError):
            setup_app_role(schema_conn, bad, schema_name, "pw")
        with pytest.raises(ValueError):
            setup_app_role(schema_conn, role, bad, "pw")
    assert schema_conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0  # nothing executed from the injection attempt
