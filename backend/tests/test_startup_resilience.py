"""Database outage at start-up and at run time (C5E-2): the process stays alive, liveness stays green, readiness recovers by itself.

A controllable TCP proxy stands in for "the database is unreachable / has come back": it forwards to the disposable PostgreSQL while
running and refuses (and drops live sessions) while stopped. Nothing here runs a migration; a test asserts the schema is untouched.
"""

import socket
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import pytest

from app.core.errors import DatabaseError, DatabaseUnavailableError
from app.core.readiness import Readiness
from app.database.postgres import PostgresDatabase
from tests.conftest import make_data
from tests.pg_fixtures import pg_url, requires_postgres, schema_conn, schema_name  # noqa: F401
from tests.test_cloud_workflows import D, KIB, launch  # noqa: F401  (launch is a fixture)


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# ============================================================================================ readiness logic (no database)

def test_readiness_is_checked_at_most_once_per_retry_interval_and_never_concurrently():
    clock, calls = FakeClock(), []
    gate = threading.Event()

    def check():
        calls.append(1)
        gate.wait(5)
        raise DatabaseUnavailableError("down")

    r = Readiness(check, retry_seconds=5.0, clock=clock)
    r.mark_unavailable("start")
    with pytest.raises(DatabaseUnavailableError):
        r.ensure_ready()  # too soon after the failure: refused without probing
    assert calls == []
    clock.t += 5.1
    results = []

    def attempt():
        try:
            r.ensure_ready()
        except DatabaseUnavailableError:
            results.append("down")

    ts = [threading.Thread(target=attempt) for _ in range(8)]
    [t.start() for t in ts]
    time.sleep(0.3)
    gate.set()
    [t.join(10) for t in ts]
    assert len(calls) == 1 and results == ["down"] * 8  # single flight: one probe, everyone else told "not ready" immediately
    with pytest.raises(DatabaseUnavailableError):
        r.ensure_ready()
    assert len(calls) == 1  # and the next probe waits another interval


def test_readiness_flips_to_ready_when_the_check_passes_and_a_non_outage_error_keeps_it_not_ready():
    clock, state = FakeClock(), {"mode": "down"}

    def check():
        if state["mode"] == "down":
            raise DatabaseUnavailableError("down")
        if state["mode"] == "schema":
            raise DatabaseError("schema is at version 0000")

    r = Readiness(check, retry_seconds=1.0, clock=clock)
    r.mark_unavailable("start")
    clock.t += 2
    state["mode"] = "schema"
    with pytest.raises(DatabaseError):  # a wrong schema is NOT an outage: it is reported as what it is, and the service stays not ready
        r.ensure_ready()
    assert not r.ready
    clock.t += 2
    state["mode"] = "up"
    r.ensure_ready()
    assert r.ready
    r.ensure_ready()  # ready: no further checks


# ============================================================================================ outage and recovery (PostgreSQL)

class ControllableProxy:
    def __init__(self, upstream_host, upstream_port):
        self.upstream = (upstream_host, upstream_port)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self._listener = None
        self._socks = []
        self._lock = threading.Lock()

    def start(self):
        ls = socket.socket()
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind(("127.0.0.1", self.port))
        ls.listen(32)
        self._listener = ls
        threading.Thread(target=self._accept, args=(ls,), daemon=True).start()

    def _accept(self, ls):
        while True:
            try:
                client, _ = ls.accept()
                up = socket.create_connection(self.upstream)
            except OSError:
                return
            with self._lock:
                self._socks += [client, up]
            for a, b in ((client, up), (up, client)):
                threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

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

    def stop(self):
        """The database 'goes away': stop accepting AND cut every live session."""
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        with self._lock:
            socks, self._socks = self._socks, []
        for s in socks:
            for fn in (lambda: s.shutdown(socket.SHUT_RDWR), s.close):
                try:
                    fn()
                except OSError:
                    pass

    def url(self, pg_url):
        u = urlsplit(pg_url)
        return urlunsplit((u.scheme, f"{u.username}@127.0.0.1:{self.port}", u.path, "", ""))


@pytest.fixture
def proxy(pg_url):
    u = urlsplit(pg_url)
    p = ControllableProxy(u.hostname, u.port)
    yield p
    p.stop()


@pytest.fixture
def quick_pool(monkeypatch):
    monkeypatch.setattr(PostgresDatabase, "POOL_WAIT_SECONDS", 2.0)


def relations(c):
    return c.q("SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = current_schema() ORDER BY 1")


def timed_get(c, path, **kw):
    t0 = time.perf_counter()
    r = c.client.get(path, **kw)
    return r, time.perf_counter() - t0


@pytest.mark.postgres
@requires_postgres
def test_the_process_starts_without_a_database_stays_alive_and_says_why_it_is_not_ready(launch, pg_url, proxy, quick_pool):
    c = launch({"database_url": proxy.url(pg_url), "startup_retry_seconds": 0.3, "db_connect_timeout_seconds": 1})  # proxy NOT started
    r, took = timed_get(c, "/api/health/live")
    assert r.status_code == 200 and r.json()["status"] == "ok" and took < 1.0
    r, took = timed_get(c, "/api/health")
    assert r.status_code == 503 and r.json()["status"] == "degraded" and r.json()["database"]["connected"] is False and took < 3.0
    r, took = timed_get(c, "/api/files")
    assert r.status_code == 503 and r.json()["error"]["code"] == "database_unavailable" and took < 3.0  # fast failure, not a 15 s wait
    r = c.upload(make_data(1, 20 * KIB))
    assert r.status_code == 503 and r.json()["error"]["code"] == "database_unavailable"
    assert not c.client.app.state.readiness.ready


@pytest.mark.postgres
@requires_postgres
def test_readiness_recovers_by_itself_when_the_database_returns_without_touching_the_schema(launch, pg_url, proxy, quick_pool):
    before = relations(c := launch({"database_url": proxy.url(pg_url), "startup_retry_seconds": 0.3, "db_connect_timeout_seconds": 1}))
    version_before = c.one("SELECT version_num FROM alembic_version")
    assert c.client.get("/api/health").status_code == 503
    proxy.start()  # the database comes back
    time.sleep(0.4)  # one retry interval
    r = c.client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["database"]["schema_version"] == int(version_before)
    up = c.upload(make_data(2, 60 * KIB))
    assert up.status_code == 201  # fully working again, with no restart
    assert relations(c) == before + [] or set(relations(c)) == set(before)  # no table, view, function or sequence was created
    assert c.one("SELECT version_num FROM alembic_version") == version_before  # and no migration ran


@pytest.mark.postgres
@requires_postgres
def test_an_unavailable_database_is_probed_at_most_once_per_interval_no_retry_storm(launch, pg_url, proxy, quick_pool, monkeypatch):
    probes = []
    real = PostgresDatabase.reachable
    monkeypatch.setattr(PostgresDatabase, "reachable", lambda self, t=3.0: (probes.append(1), real(self, t))[1])
    c = launch({"database_url": proxy.url(pg_url), "startup_retry_seconds": 1.5, "db_connect_timeout_seconds": 1})
    start_probes = len(probes)
    for _ in range(60):  # a burst of requests (and health checks) while the database is down
        c.client.get("/api/health")
        c.client.get("/api/files")
    assert len(probes) - start_probes <= 2  # a couple of probes at most, not one per request


@pytest.mark.postgres
@requires_postgres
def test_an_outage_in_the_middle_of_service_fails_fast_then_recovers_with_data_intact(launch, pg_url, proxy, quick_pool):
    proxy.start()
    c = launch({"database_url": proxy.url(pg_url), "startup_retry_seconds": 0.5, "db_connect_timeout_seconds": 1})
    data = make_data(3, 80 * KIB)
    fid = c.upload(data).json()["file_id"]
    assert c.client.get("/api/health").status_code == 200

    proxy.stop()  # the database disappears while the service is running (existing sessions are cut)
    r = c.client.get("/api/files")  # the first request may need to discover it (bounded wait)
    assert r.status_code == 503 and r.json()["error"]["code"] == "database_unavailable"
    for path in ("/api/files", "/api/health", f"/api/files/{fid}"):  # afterwards: immediate, honest answers
        r, took = timed_get(c, path)
        assert r.status_code == 503 and took < 1.0, (path, took)
    assert c.client.get("/api/health/live").status_code == 200

    proxy.start()
    time.sleep(0.7)
    assert c.client.get("/api/health").status_code == 200
    assert c.client.get(f"/api/files/{fid}/download", headers=D).content == data  # nothing was lost or half-written
    assert c.q("SELECT count(*) FROM v_ref_count_mismatch") == [(0,)]


@pytest.mark.postgres
@requires_postgres
def test_a_wrong_schema_is_still_a_start_up_failure_not_a_degraded_start(launch, schema_conn):
    schema_conn.execute("UPDATE alembic_version SET version_num = '0000'")
    with pytest.raises(DatabaseError, match="schema is at version 0000"):
        launch()


def test_the_local_backend_still_fails_fast_when_its_database_cannot_be_prepared(tmp_path):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    s = Settings(data_dir=blocker / "data", db_path=blocker / "data" / "x.db", cors_origins=("http://localhost:5173",))
    with pytest.raises(Exception):
        with TestClient(create_app(s)):
            pass
