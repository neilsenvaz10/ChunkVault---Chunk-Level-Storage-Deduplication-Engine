"""Session settings must be applied with SQL, not libpq startup options (found against the real Supabase session pooler in C5A:
the pooler ACCEPTS the 'options' startup parameter but IGNORES it, leaving statement_timeout at the role default of 2 minutes).

A small TCP proxy in front of the disposable PostgreSQL reproduces that behaviour: it forwards connections but removes 'options'
(and 'application_name') from the startup packet. Everything the application then does must still end up correctly configured.
"""

import socket
import struct
import threading
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from app.core.errors import DatabaseError
from app.database.postgres import APPLICATION_NAME, PostgresDatabase, tls_kwargs
from tests.pg_fixtures import cloud_settings, pg_url, requires_postgres, schema_conn, schema_name, stamp_version  # noqa: F401

pytestmark = [pytest.mark.postgres, requires_postgres]
SSL_REQUEST = 80877103


def _recvn(sock, n):
    buf = b""
    while len(buf) < n:
        part = sock.recv(n - len(buf))
        if not part:
            raise ConnectionError("closed")
        buf += part
    return buf


class OptionIgnoringProxy:
    """Forwards PostgreSQL connections to the real server, dropping the startup parameters a pooler would ignore."""

    DROPPED = {b"options", b"application_name"}

    def __init__(self, host, port):
        self.upstream = (host, port)
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(16)
        self.port = self.srv.getsockname()[1]
        self.startups = []  # the startup keys each client sent (for assertions)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                client, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client):
        up = None
        try:
            length, code = struct.unpack("!II", _recvn(client, 8))
            if code == SSL_REQUEST:
                client.sendall(b"N")  # no TLS on this local hop
                length, code = struct.unpack("!II", _recvn(client, 8))
            body = _recvn(client, length - 8)
            parts = body.split(b"\0")[:-2]
            pairs = list(zip(parts[0::2], parts[1::2]))
            self.startups.append({k.decode() for k, _ in pairs})
            kept = b"".join(k + b"\0" + v + b"\0" for k, v in pairs if k not in self.DROPPED) + b"\0"
            up = socket.create_connection(self.upstream)
            up.sendall(struct.pack("!II", len(kept) + 8, code) + kept)
            threading.Thread(target=self._pipe, args=(up, client), daemon=True).start()
            self._pipe(client, up)
        except (OSError, ConnectionError, struct.error):
            pass
        finally:
            for s in (client, up):
                try:
                    if s:
                        s.close()
                except OSError:
                    pass

    @staticmethod
    def _pipe(src, dst):
        try:
            while data := src.recv(65536):
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self):
        self.srv.close()


@pytest.fixture
def proxy(pg_url):
    u = urlsplit(pg_url)
    p = OptionIgnoringProxy(u.hostname, u.port)
    p.url = urlunsplit((u.scheme, f"{u.username}@127.0.0.1:{p.port}", u.path, "", ""))
    yield p
    p.close()


@pytest.fixture
def pooled_db(proxy, schema_conn, schema_name, tmp_path):
    stamp_version(schema_conn)
    db = PostgresDatabase(cloud_settings(proxy.url, schema_name, tmp_path, db_statement_timeout_ms=30000, db_lock_timeout_ms=5000))
    db.POOL_WAIT_SECONDS = 4.0
    yield db
    db.close()


def settings_of(conn):
    return {n: conn.execute(f"SHOW {n}").fetchone()[0] for n in ("statement_timeout", "lock_timeout", "application_name")}


# ----------------------------------------------------------------------------------------------- the simulation itself

def test_the_proxy_reproduces_a_pooler_that_accepts_but_ignores_startup_options(proxy):
    with psycopg.connect(proxy.url, options="-c statement_timeout=1234 -c lock_timeout=777", application_name="mine", autocommit=True) as c:
        got = settings_of(c)
    assert got["statement_timeout"] == "0" and got["lock_timeout"] == "0" and got["application_name"] != "mine"  # silently dropped
    assert {"options", "application_name"} & proxy.startups[-1] == {"options", "application_name"}  # the client DID send them


def test_the_application_never_relies_on_startup_options(pooled_db):
    assert "options" not in pooled_db._conn_kwargs()


# ----------------------------------------------------------------------------------------------- pooled connections

def test_pooled_connections_get_their_settings_even_when_the_pooler_ignores_startup_options(pooled_db, schema_name):
    with pooled_db.connection() as c:
        assert settings_of(c) == {"statement_timeout": "30s", "lock_timeout": "5s", "application_name": APPLICATION_NAME}
        assert c.execute("SELECT current_schema()").fetchone()[0] == schema_name  # search_path too (also never sent as an option)
        assert c.info.transaction_status == psycopg.pq.TransactionStatus.IDLE and c.autocommit  # initialised outside any transaction


def test_configured_values_follow_the_configuration(proxy, schema_conn, schema_name, tmp_path):
    stamp_version(schema_conn)
    db = PostgresDatabase(cloud_settings(proxy.url, schema_name, tmp_path, db_statement_timeout_ms=45000, db_lock_timeout_ms=7000))
    try:
        with db.connection() as c:
            got = settings_of(c)
        assert (got["statement_timeout"], got["lock_timeout"]) == ("45s", "7s")
        assert db.session_settings()["statement_timeout"] == "45000"
    finally:
        db.close()


def test_the_statement_timeout_is_really_enforced_on_pooled_connections(proxy, schema_conn, schema_name, tmp_path):
    stamp_version(schema_conn)
    db = PostgresDatabase(cloud_settings(proxy.url, schema_name, tmp_path, db_statement_timeout_ms=1000))
    try:
        with db.connection() as c:
            with pytest.raises(psycopg.errors.QueryCanceled):
                c.execute("SELECT pg_sleep(5)")
    finally:
        db.close()


def test_settings_survive_pool_reuse(pooled_db):
    pids = set()
    for _ in range(4):
        with pooled_db.connection() as c:
            pids.add(c.info.backend_pid)
            assert settings_of(c)["statement_timeout"] == "30s"
            c.execute("BEGIN")
            c.execute("SELECT 1")  # leaves a transaction open: the pool rolls it back, the settings stay
    assert len(pids) < 4  # the same connection(s) were reused, not rebuilt each time


def test_a_reconnected_pool_connection_is_initialised_again(pooled_db, schema_conn):
    with pooled_db.connection() as c:
        old = c.info.backend_pid
    schema_conn.execute("SELECT pg_terminate_backend(%s)", (old,))  # the server drops it (restart, failover, idle timeout)
    with pooled_db.connection() as c:
        assert c.info.backend_pid != old
        assert settings_of(c) == {"statement_timeout": "30s", "lock_timeout": "5s", "application_name": APPLICATION_NAME}


def test_a_connection_that_lost_its_settings_is_discarded_at_checkout(pooled_db):
    with pooled_db.connection() as c:
        old = c.info.backend_pid
        c.execute("RESET statement_timeout")  # what DISCARD ALL / a transaction-mode pooler would do behind our back
        assert settings_of(c)["statement_timeout"] == "0"
    with pooled_db.connection() as c:
        assert c.info.backend_pid != old and settings_of(c)["statement_timeout"] == "30s"


# ----------------------------------------------------------------------------------------------- dedicated lock connection

def test_the_dedicated_lock_connection_is_configured_separately_and_identically(pooled_db, schema_name):
    with pooled_db.connection() as pooled:
        pooled_pid = pooled.info.backend_pid
        with pooled_db.writer_lock(2):
            held = pooled_db.lock._held.get().conn
            assert held.info.backend_pid != pooled_pid  # a different session from any pooled one
            assert settings_of(held) == {"statement_timeout": "30s", "lock_timeout": "5s", "application_name": APPLICATION_NAME}
            assert held.execute("SELECT current_schema()").fetchone()[0] == schema_name
            assert held.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


def test_every_connection_the_application_owns_has_the_settings(pooled_db):
    with pooled_db.connection() as a, pooled_db.connection() as b:  # two pooled sessions at once
        assert a.info.backend_pid != b.info.backend_pid
        assert settings_of(a) == settings_of(b)
    dedicated = pooled_db._connect_dedicated()
    try:
        assert settings_of(dedicated)["lock_timeout"] == "5s"
    finally:
        dedicated.close()


# ----------------------------------------------------------------------------------------------- failure handling

def test_unverifiable_settings_discard_the_dedicated_connection_with_a_sanitised_error(pooled_db, monkeypatch):
    made = []
    real_connect = psycopg.connect
    monkeypatch.setattr("app.database.postgres.psycopg.connect", lambda *a, **k: made.append(real_connect(*a, **k)) or made[-1])
    monkeypatch.setattr(PostgresDatabase, "_read_effective", staticmethod(lambda conn, names: {n: "0" for n in names}))  # the server "ignored" SET
    with pytest.raises(DatabaseError) as e:
        pooled_db._connect_dedicated()
    assert "could not be configured" in e.value.message and "@" not in e.value.message and "postgresql://" not in e.value.message
    assert e.value.__cause__ is None and made and made[0].closed  # closed, never handed out, nothing chained that could carry details


def test_writer_lock_fails_safely_when_its_session_cannot_be_configured(pooled_db, monkeypatch):
    monkeypatch.setattr(PostgresDatabase, "_read_effective", staticmethod(lambda conn, names: {}))
    with pytest.raises(DatabaseError, match="could not be configured"):
        with pooled_db.writer_lock(1):
            pytest.fail("the protected operation must not start without a correctly configured lock session")


def test_unverifiable_settings_make_pooled_checkout_fail_loudly_not_run_on_defaults(pooled_db, monkeypatch, caplog):
    pooled_db.POOL_WAIT_SECONDS = 2.0
    monkeypatch.setattr(PostgresDatabase, "_read_effective", staticmethod(lambda conn, names: {n: "0" for n in names}))
    with caplog.at_level("ERROR"):
        with pytest.raises(DatabaseError, match="could not be configured"):
            with pooled_db.connection():
                pytest.fail("a connection without its settings must never be handed out")
    assert "discarded" in caplog.text and "postgresql://" not in caplog.text and "@" not in caplog.text


def test_a_server_side_failure_while_applying_settings_is_sanitised_too(pooled_db, monkeypatch):
    def broken(self, conn):  # simulate the SQL itself failing
        conn.execute("SELECT this_function_does_not_exist()")

    real = PostgresDatabase._init_connection

    def patched(self, conn):
        try:
            broken(self, conn)
        except psycopg.Error:
            conn.close()
            raise DatabaseError("the database connection could not be configured: its session settings (timeouts, schema) were not applied, so it was discarded") from None

    monkeypatch.setattr(PostgresDatabase, "_init_connection", patched)
    with pytest.raises(DatabaseError, match="could not be configured"):
        pooled_db._connect_dedicated()
    assert real is not patched


# ----------------------------------------------------------------------------------------------- TLS preference

@pytest.mark.parametrize("url,expected", [
    ("postgresql://u:p@aws-0-eu-west-1.pooler.supabase.com:5432/postgres", {"sslmode": "require"}),
    ("postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres", {"sslmode": "require"}),
    ("postgresql://u:p@10.0.0.5:5432/db", {"sslmode": "require"}),
    ("postgresql://u:p@localhost:5432/db", {}),
    ("postgresql://u:p@127.0.0.1:55432/db", {}),
    ("postgresql://u:p@[::1]:5432/db", {}),
    ("postgresql://u:p@host.example:5432/db?sslmode=verify-full", {}),  # the operator's explicit choice is respected
    ("postgresql://u:p@host.example:5432/db?sslmode=disable", {}),
    ("postgresql://u@/db?host=/var/run/postgresql", {}),  # unix socket
])
def test_tls_is_required_for_remote_servers_only(url, expected):
    assert tls_kwargs(url) == expected
