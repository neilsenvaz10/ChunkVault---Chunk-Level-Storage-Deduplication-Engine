"""PostgresDatabase: start-up verification, explicit transactions, connection hygiene and the advisory writer lock.

Needs a throw-away PostgreSQL (TEST_DATABASE_URL); skipped otherwise.
"""

import threading
import time
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from app.core.errors import DatabaseError
from app.core.locking import WriterBusyError, WriterLockLostError
from app.database.postgres import EXPECTED_SCHEMA_VERSION, PostgresDatabase, lock_key
from app.database.postgres_repository import PostgresMetadataRepository
from app.database.states import FileStatus
from tests.pg_fixtures import (  # noqa: F401  (fixtures are used by name)
    H, cloud_settings, pg_url, pgdb, requires_postgres, schema_conn, schema_name, stamp_version,
)

pytestmark = [pytest.mark.postgres, requires_postgres]
repo = PostgresMetadataRepository()


def advisory_holders(owner, key):
    return [r[0] for r in owner.execute(
        "SELECT pid FROM pg_locks WHERE locktype='advisory' AND granted AND ((classid::bigint << 32) | objid::bigint) = %s", (key,))]


# ------------------------------------------------------------------------------------------------------- start-up

def test_prepare_for_startup_verifies_the_version_and_changes_nothing(pgdb, schema_conn):
    before = schema_conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                 "WHERE n.nspname = current_schema()").fetchone()[0]
    assert pgdb.prepare_for_startup() == EXPECTED_SCHEMA_VERSION
    after = schema_conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                "WHERE n.nspname = current_schema()").fetchone()[0]
    assert before == after


def test_prepare_for_startup_refuses_a_wrong_or_missing_schema(pgdb, schema_conn, pg_url, schema_name, tmp_path):
    schema_conn.execute("UPDATE alembic_version SET version_num = '0000'")
    with pytest.raises(DatabaseError, match="schema is at version 0000"):
        pgdb.prepare_for_startup()
    schema_conn.execute("DROP TABLE alembic_version")
    with pytest.raises(DatabaseError, match="schema is missing"):
        pgdb.prepare_for_startup()
    empty = PostgresDatabase(cloud_settings(pg_url, schema_name + "_none", tmp_path))  # a schema that does not exist
    try:
        with pytest.raises(DatabaseError):
            empty.prepare_for_startup()
    finally:
        empty.close()
    schema_conn.execute("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")  # restore for fixture teardown


def test_unreachable_database_fails_with_a_safe_message_and_no_secret(tmp_path):
    s = cloud_settings("postgresql://u:SuperSecret123@127.0.0.1:1/none", "x", tmp_path, db_connect_timeout_seconds=1)
    db = PostgresDatabase(s)
    try:
        t0 = time.monotonic()
        with pytest.raises(DatabaseError) as e:
            db._ensure_pool().timeout = 2.0
            db.prepare_for_startup()
        assert time.monotonic() - t0 < 20
        assert "SuperSecret123" not in str(e.value) and "SuperSecret123" not in repr(e.value)
        assert db.ping().connected is False  # never raises
    finally:
        db.close()


def test_construction_never_connects(tmp_path):
    s = cloud_settings("postgresql://u:p@nonexistent.invalid:5432/db", "x", tmp_path)
    db = PostgresDatabase(s)  # would hang or raise if it connected
    assert db._pool is None
    db.close()


# ---------------------------------------------------------------------------------------- connection configuration

def test_connections_are_configured_per_session(pgdb, schema_name):
    with pgdb.connection() as c:
        assert c.execute("SHOW statement_timeout").fetchone()[0] == "30s"
        assert c.execute("SHOW lock_timeout").fetchone()[0] == "5s"
        assert c.execute("SHOW application_name").fetchone()[0] == "chunkvault"
        assert c.execute("SELECT current_schema()").fetchone()[0] == schema_name
        assert c.autocommit is True
        row = c.execute("SELECT 1 AS one, 2 AS two").fetchone()
        assert (row[0], row["two"], tuple(row), row.keys()) == (1, 2, (1, 2), ["one", "two"])


# ------------------------------------------------------------------------------------------------ transactions

def test_transactions_commit_and_roll_back_explicitly(pgdb):
    with pgdb.connection() as c:
        with pgdb.transaction(c):
            repo.create_file(c, "f1", "a", 1)
        with pytest.raises(ZeroDivisionError):
            with pgdb.transaction(c):
                repo.create_file(c, "f2", "b", 1)
                1 / 0
        assert c.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    with pgdb.connection() as c:
        assert [f.file_id for f in repo.list_files(c, 10, 0)] == ["f1"]


def test_a_transaction_left_open_is_rolled_back_when_the_connection_is_returned(pgdb):
    with pgdb.connection() as c:
        c.execute("BEGIN")
        repo.create_file(c, "leaked", "a", 1)
        # the block ends without COMMIT, as after a bug: nothing may be committed implicitly
    with pgdb.connection() as c:
        assert repo.get_file(c, "leaked") is None
        assert c.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


def test_an_exception_inside_a_connection_block_never_commits(pgdb):
    with pytest.raises(RuntimeError):
        with pgdb.connection() as c:
            c.execute("BEGIN")
            repo.create_file(c, "x", "a", 1)
            raise RuntimeError("fail")
    with pgdb.connection() as c:
        assert repo.get_file(c, "x") is None


def test_pool_survives_a_terminated_work_connection(pgdb, schema_conn):
    with pgdb.connection() as c:
        pid = c.info.backend_pid
        repo.get_file(c, "warm")
        schema_conn.execute("SELECT pg_terminate_backend(%s)", (pid,))
        with pytest.raises(psycopg.OperationalError):
            repo.get_file(c, "x")
    with pgdb.connection() as c:  # the broken connection was discarded; a fresh one works
        assert repo.get_file(c, "x") is None


def test_termination_in_the_middle_of_a_transaction_commits_nothing(pgdb, schema_conn):
    with pytest.raises(psycopg.OperationalError):
        with pgdb.connection() as c:
            with pgdb.transaction(c):
                repo.create_file(c, "half", "a", 1)
                schema_conn.execute("SELECT pg_terminate_backend(%s)", (c.info.backend_pid,))
    with pgdb.connection() as c:
        assert repo.get_file(c, "half") is None


# ------------------------------------------------------------------------------------------------ writer lock

def test_lock_is_held_on_a_dedicated_session_and_released_afterwards(pgdb, schema_conn):
    key = pgdb.lock.key
    assert advisory_holders(schema_conn, key) == []
    with pgdb.writer_lock(2):
        holders = advisory_holders(schema_conn, key)
        assert len(holders) == 1
        with pgdb.connection() as c:  # the lock session is not one the pool can hand out
            assert c.info.backend_pid != holders[0]
            assert pgdb.lock.is_free() is False
    assert advisory_holders(schema_conn, key) == []
    assert pgdb.lock.is_free() is True


def test_second_acquirer_times_out_without_disturbing_the_holder(pgdb, schema_conn):
    key = pgdb.lock.key
    outcome = []
    with pgdb.writer_lock(2):
        def other():
            try:
                with pgdb.writer_lock(0.3):
                    outcome.append("acquired")
            except WriterBusyError:
                outcome.append("busy")

        t = threading.Thread(target=other)
        t.start()
        t.join()
        assert len(advisory_holders(schema_conn, key)) == 1
    assert outcome == ["busy"]


def test_conflicting_operations_are_serialised_across_sessions(pgdb):
    active = peak = 0
    guard = threading.Lock()
    done = []

    def op(i):
        nonlocal active, peak
        with pgdb.writer_lock(10):
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            done.append(i)
            with guard:
                active -= 1

    threads = [threading.Thread(target=op, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak == 1 and sorted(done) == list(range(6))


def test_another_process_or_session_holding_the_lock_blocks_us(pgdb, pg_url, schema_name):
    """A raw second session stands in for a second server process (the key is the same server-side object)."""
    key = pgdb.lock.key
    other = psycopg.connect(pg_url, autocommit=True)
    try:
        assert other.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]
        with pytest.raises(WriterBusyError):
            with pgdb.writer_lock(0.3):
                pytest.fail("must not run while another session holds the lock")
        other.execute("SELECT pg_advisory_unlock(%s)", (key,))
        with pgdb.writer_lock(1):
            pass
    finally:
        other.close()


def test_lock_is_released_after_a_failure_in_the_protected_operation(pgdb, schema_conn):
    with pytest.raises(RuntimeError, match="upload exploded"):
        with pgdb.writer_lock(1):
            with pgdb.connection() as c, pgdb.transaction(c):
                repo.create_file(c, "doomed", "a", 1)
                raise RuntimeError("upload exploded")
    assert advisory_holders(schema_conn, pgdb.lock.key) == []
    with pgdb.writer_lock(1):  # immediately re-acquirable
        with pgdb.connection() as c:
            assert repo.get_file(c, "doomed") is None  # and the failed transaction left nothing behind


def test_lock_is_released_when_the_body_raises_a_base_exception(pgdb, schema_conn):
    class Crash(BaseException):
        pass

    with pytest.raises(Crash):
        with pgdb.writer_lock(1):
            raise Crash()
    assert advisory_holders(schema_conn, pgdb.lock.key) == []
    assert pgdb.lock.is_free()


def test_acquire_timeout_leaves_no_session_behind(pgdb, schema_conn):
    with pgdb.writer_lock(1):
        with pytest.raises(WriterBusyError):
            with pgdb.writer_lock(0.2):
                pass
    with schema_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name = 'chunkvault' AND state IS NOT NULL "
                    "AND pid <> pg_backend_pid() AND query LIKE '%pg_try_advisory_lock%'")
        # the only sessions that ever ran the lock query are closed again (pool sessions never run it)
        assert cur.fetchone()[0] == 0


def test_the_lock_cannot_be_taken_twice_by_the_same_operation(pgdb):
    with pgdb.writer_lock(1):
        with pytest.raises(WriterBusyError, match="already held"):
            with pgdb.writer_lock(1):
                pass
    with pgdb.writer_lock(1):
        pass


def test_lock_keys_are_per_schema(pg_url, tmp_path):
    assert lock_key("a") != lock_key("b") and lock_key("a") == lock_key("a") and lock_key(None) != lock_key("a")
    assert 0 < lock_key("a") < 2 ** 63


# ------------------------------------------------------------------------------- unexpected connection loss

def test_losing_the_lock_session_fails_the_protected_operation_before_commit(pgdb, schema_conn):
    with pytest.raises(WriterLockLostError):
        with pgdb.writer_lock(2):
            (pid,) = advisory_holders(schema_conn, pgdb.lock.key)
            with pgdb.connection() as c:
                with pgdb.transaction(c):
                    repo.create_file(c, "must-not-commit", "a", 1)
                    schema_conn.execute("SELECT pg_terminate_backend(%s)", (pid,))  # the lock session dies mid-operation
    assert schema_conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0  # rolled back, never committed
    assert advisory_holders(schema_conn, pgdb.lock.key) == []
    with pgdb.writer_lock(1):  # the system recovers: the lock is simply free again
        pass


def test_losing_the_lock_session_is_reported_even_when_no_transaction_is_open(pgdb, schema_conn):
    with pytest.raises(WriterLockLostError):
        with pgdb.writer_lock(2):
            (pid,) = advisory_holders(schema_conn, pgdb.lock.key)
            schema_conn.execute("SELECT pg_terminate_backend(%s)", (pid,))
            pgdb.lock.ensure_held()
    assert advisory_holders(schema_conn, pgdb.lock.key) == []


def test_release_after_connection_loss_does_not_mask_the_original_outcome(pgdb, schema_conn):
    with pgdb.writer_lock(2):
        (pid,) = advisory_holders(schema_conn, pgdb.lock.key)
        schema_conn.execute("SELECT pg_terminate_backend(%s)", (pid,))
        time.sleep(0.1)
    # leaving the block raised nothing, and the lock is free
    assert pgdb.lock.is_free()


def test_ensure_held_is_a_noop_for_code_that_does_not_own_the_lock(pgdb):
    pgdb.lock.ensure_held()  # no owner in this context: nothing to verify
    with pgdb.connection() as c, pgdb.transaction(c):  # ordinary transactions (restore bookkeeping) commit without a lock
        repo.create_file(c, "plain", "a", 1)
    with pgdb.connection() as c:
        assert repo.get_file(c, "plain").status is FileStatus.UPLOADING


def test_lock_with_a_healthy_session_lets_the_transaction_commit(pgdb, schema_conn):
    with pgdb.writer_lock(2):
        with pgdb.connection() as c, pgdb.transaction(c):
            repo.create_file(c, "ok", "a", 1)
    assert schema_conn.execute("SELECT count(*) FROM files").fetchone()[0] == 1


# ---------------------------------------------------------------------------------- least privilege end to end

def test_everything_works_as_the_least_privilege_application_role(pg_url, schema_conn, schema_name, tmp_path):
    from app.database.pg_admin import setup_app_role

    stamp_version(schema_conn)
    role, pw = "cv_ct_" + schema_name[2:10], "pw-" + schema_name
    setup_app_role(schema_conn, role, schema_name, pw)
    u = urlsplit(pg_url)
    app_url = urlunsplit((u.scheme, f"{role}:{pw}@{u.hostname}:{u.port}", u.path, "", ""))
    db = PostgresDatabase(cloud_settings(app_url, schema_name, tmp_path))
    try:
        assert db.prepare_for_startup() == EXPECTED_SCHEMA_VERSION
        with db.writer_lock(2):
            with db.connection() as c:
                with db.transaction(c):
                    repo.create_file(c, "f1", "a", 100)
                    repo.register_chunk(c, H("a"), 100)
                    repo.append_file_chunk(c, "f1", 0, H("a"), 100)
                    repo.set_file_status(c, "f1", FileStatus.COMPLETE, sha256="f" * 64)
                assert repo.get_chunk(c, H("a")).ref_count == 1 and repo.ref_count_mismatches(c) == []
                assert {t.name for t in db.introspect()} == {"files", "chunks", "file_chunks", "operations"}
                assert db.footprint_bytes() > 0
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute("DROP TABLE files")
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute("ALTER TABLE file_chunks DISABLE TRIGGER ALL")
    finally:
        db.close()
        schema_conn.execute("RESET search_path")
        schema_conn.execute(f'DROP OWNED BY "{role}"')
        schema_conn.execute(f'DROP ROLE "{role}"')
