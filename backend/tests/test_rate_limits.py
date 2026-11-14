"""Abuse limits (C5E-2): failed-authentication lockout, per-address throttles, concurrency gates, and proxy-header handling."""

import ipaddress
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.ratelimit import MAX_KEYS, Gate, SlidingWindow, client_address
from tests.conftest import make_data
from tests.pg_fixtures import pg_url, requires_postgres, schema_conn, schema_name  # noqa: F401
from tests.test_cloud_workflows import A, D, DEMO, KIB, launch  # noqa: F401  (launch is a fixture)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ============================================================================================ pure logic (no database)

def test_sliding_window_allows_the_limit_then_refuses_until_the_oldest_event_expires():
    clock = FakeClock()
    w = SlidingWindow(3, 60.0, clock)
    assert [w.hit("a") for _ in range(3)] == [0.0, 0.0, 0.0]
    assert 0 < w.hit("a") <= 60.0  # refused, with the time to wait
    assert w.hit("b") == 0.0  # other keys are independent
    clock.t += 59
    assert w.hit("a") > 0
    clock.t += 2
    assert w.hit("a") == 0.0  # the window slid


def test_a_refused_hit_is_not_recorded_so_waiting_it_out_works():
    clock = FakeClock()
    w = SlidingWindow(1, 10.0, clock)
    assert w.hit("k") == 0.0
    for _ in range(50):
        assert w.hit("k") > 0  # hammering does not extend the penalty
    clock.t += 10
    assert w.hit("k") == 0.0


def test_the_number_of_tracked_addresses_is_bounded():
    w = SlidingWindow(1, 60.0, FakeClock())
    for i in range(MAX_KEYS + 500):
        w.hit(f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}")
    assert len(w._events) <= MAX_KEYS


def test_gate_never_waits_and_counts_exactly():
    g = Gate("x", 2)
    assert g.enter() and g.enter() and not g.enter() and g.in_flight == 2
    g.leave()
    assert g.enter()
    g.leave(), g.leave(), g.leave()
    assert g.in_flight == 0  # never negative


NETS = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("100.64.0.0/10"))


def request(peer, xff=None, headers=()):
    raw = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    raw += [(k.encode(), v.encode()) for k, v in headers]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "client": (peer, 5000) if peer else None, "query_string": b""})


@pytest.mark.parametrize("peer,xff,hops,expected", [
    ("10.0.0.5", "203.0.113.7", 1, "203.0.113.7"),                       # the platform proxy reports the client
    ("10.0.0.5", "6.6.6.6, 203.0.113.7", 1, "203.0.113.7"),              # entries to the LEFT are the client's claims: ignored
    ("10.0.0.5", "6.6.6.6, 7.7.7.7, 203.0.113.7", 1, "203.0.113.7"),
    ("10.0.0.5", "203.0.113.7, 198.51.100.1", 2, "203.0.113.7"),         # two trusted proxies: the second from the right
    ("10.0.0.5", "203.0.113.7", 0, "10.0.0.5"),                          # zero hops: the header is never read
    ("8.8.8.8", "203.0.113.7", 1, "8.8.8.8"),                            # a direct public client cannot choose its address
    ("100.64.0.9", "203.0.113.7", 1, "203.0.113.7"),                     # shared (carrier-grade) space is inside the platform
    ("10.0.0.5", None, 1, "10.0.0.5"),                                   # no header
    ("10.0.0.5", "", 1, "10.0.0.5"),
    ("10.0.0.5", "203.0.113.7", 2, "10.0.0.5"),                          # fewer entries than trusted proxies: not the chain we trust
    ("10.0.0.5", "not-an-ip", 1, "10.0.0.5"),
    ("10.0.0.5", "6.6.6.6, not-an-ip", 1, "10.0.0.5"),                   # any malformed entry discards the whole header
    ("10.0.0.5", "203.0.113.7, ", 1, "10.0.0.5"),
    ("10.0.0.5", ",".join(["1.2.3.4"] * 21), 1, "10.0.0.5"),             # absurdly long chains
    ("10.0.0.5", "1.2.3.4," + "x" * 2000, 1, "10.0.0.5"),
    ("10.0.0.5", "2001:db8::7", 1, "2001:db8::7"),
    ("10.0.0.5", "[2001:db8::7]", 1, "2001:db8::7"),
    ("testclient", "203.0.113.7", 1, "testclient"),                      # a peer that is not an IP address: header ignored
    (None, "203.0.113.7", 1, "unknown"),
])
def test_client_address_uses_the_proxy_header_only_where_it_can_be_trusted(peer, xff, hops, expected):
    assert client_address(request(peer, xff), hops, NETS) == expected


@pytest.mark.parametrize("peer", ["10.0.0.5", "192.168.1.5", "172.16.0.9", "127.0.0.1", "169.254.1.1", "100.64.0.9", "8.8.8.8", "::1", "fd00::5"])
def test_without_explicitly_configured_proxy_networks_the_header_is_never_believed(peer):
    """Being private (or loopback, link-local, shared space) is not evidence of being a proxy: no configuration, no trust."""
    assert client_address(request(peer, "203.0.113.7"), 1, ()) == peer
    assert client_address(request(peer, "203.0.113.7"), 1) == peer


@pytest.mark.parametrize("peer", ["192.168.1.5", "172.16.0.9", "127.0.0.1", "11.0.0.1", "fd00::5", "10.0.0.5%eth0"])
def test_a_private_peer_outside_the_configured_networks_is_not_a_proxy(peer):
    assert client_address(request(peer, "203.0.113.7"), 1, NETS) == peer


def test_a_configured_network_is_matched_by_address_family_and_boundary():
    nets = (ipaddress.ip_network("10.1.0.0/16"), ipaddress.ip_network("2001:db8::/32"))
    assert client_address(request("10.1.255.255", "203.0.113.7"), 1, nets) == "203.0.113.7"
    assert client_address(request("10.2.0.1", "203.0.113.7"), 1, nets) == "10.2.0.1"
    assert client_address(request("2001:db8::1", "203.0.113.7"), 1, nets) == "203.0.113.7"
    assert client_address(request("::ffff:10.1.0.1", "203.0.113.7"), 1, nets) == "::ffff:10.1.0.1"  # a mapped address is not a v4 match


@pytest.mark.parametrize("bad", ["0.0.0.0/0", "::/0", "10.0.0.0/7", "not-a-network", "10.0.0.1/8", "2001::/16"])
def test_overbroad_or_malformed_proxy_networks_are_refused_at_configuration(bad):
    from app.core.errors import ConfigurationError
    from tests.test_config_cloud import cloud

    with pytest.raises(ConfigurationError, match="trusted"):
        cloud(trusted_proxy_cidrs=(bad,))
    assert cloud(trusted_proxy_cidrs=("10.0.0.0/8", "2001:db8::/32")).trusted_proxy_cidrs == ("10.0.0.0/8", "2001:db8::/32")
    assert cloud().trusted_proxy_cidrs == ()  # the default trusts nothing


def test_the_environment_variable_lists_networks_and_defaults_to_none():
    from app.core.config import Settings
    from tests.test_config_cloud import env

    assert Settings.from_env(env()).cloud.trusted_proxy_cidrs == ()
    got = Settings.from_env(env(CHUNKVAULT_TRUSTED_PROXY_CIDRS=" 10.0.0.0/8 , 2001:db8::/32 ,")).cloud.trusted_proxy_cidrs
    assert got == ("10.0.0.0/8", "2001:db8::/32")


def test_forged_forwarding_headers_cannot_pick_an_address():
    seen = {client_address(request("10.0.0.5", f"{fake}, 203.0.113.7"), 1, NETS) for fake in ("1.1.1.1", "2.2.2.2", "9.9.9.9", "10.0.0.5", "127.0.0.1")}
    assert seen == {"203.0.113.7"}


# ============================================================================================ integration (cloud app on PostgreSQL)
pg = [pytest.mark.postgres, requires_postgres]


def as_address(c, ip):
    """Another client of the same running application, connecting from ``ip``."""
    return TestClient(c.client.app, client=(ip, 40000))


def with_clock(c):
    clock = FakeClock()
    lim = c.client.app.state.limits
    lim.auth_failures._clock = lim.expensive._clock = clock
    return clock


def wrong(c, client=None, n=1, path="/api/files", method="DELETE", token="x" * 40):
    cl = client or c.client
    return [cl.request(method, path + ("/" + "0" * 32 if method == "DELETE" else ""), headers={"Authorization": f"Bearer {token}"}) for _ in range(n)]


@pytest.mark.postgres
@requires_postgres
def test_repeated_wrong_tokens_lock_the_address_out_with_429_and_retry_after(launch):
    c = launch({"auth_failure_limit": 3})
    clock = with_clock(c)
    assert [r.status_code for r in wrong(c, n=3)] == [401, 401, 401]
    r = wrong(c)[0]
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited" and int(r.headers["retry-after"]) >= 1
    ok = c.client.delete("/api/files/" + "0" * 32, headers=D)  # even the CORRECT token is refused while locked: no oracle, no bypass
    assert ok.status_code == 429
    assert c.client.get("/api/files").status_code == 200  # public reads are unaffected
    clock.t += c.svc.settings.cloud.auth_failure_window_seconds + 1
    assert c.client.delete("/api/files/" + "0" * 32, headers=D).status_code == 404  # window over: authenticated again (file unknown)


@pytest.mark.postgres
@requires_postgres
def test_missing_tokens_are_not_counted_and_success_never_resets_the_counter(launch):
    c = launch({"auth_failure_limit": 3})
    with_clock(c)
    for _ in range(20):
        assert c.client.delete("/api/files/" + "0" * 32).status_code == 401  # no token at all: just refused, never locked
    wrong(c, n=2)
    assert c.client.delete("/api/files/" + "0" * 32, headers=D).status_code == 404  # a valid request in between ...
    assert wrong(c)[0].status_code == 401  # ... the third failure ...
    assert wrong(c)[0].status_code == 429  # ... and the counter was NOT reset by the success: locked


@pytest.mark.postgres
@requires_postgres
def test_the_admin_token_cannot_be_guessed_from_one_address_either(launch):
    c = launch({"auth_failure_limit": 5})
    with_clock(c)
    codes = [c.client.post("/api/admin/gc", headers={"Authorization": f"Bearer guess-{i}-" + "z" * 30}).status_code for i in range(8)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429] * 3
    assert c.client.post("/api/admin/gc", headers=A).status_code == 429  # the real admin token from that address is refused too, for now


@pytest.mark.postgres
@requires_postgres
def test_lockout_is_per_address_behind_the_proxy_and_cannot_be_dodged_by_forging_headers(launch):
    c = launch({"auth_failure_limit": 2, "trusted_proxy_hops": 1, "trusted_proxy_cidrs": ("10.0.0.0/8",)})
    with_clock(c)
    inside = as_address(c, "10.0.0.5")  # the platform proxy
    for _ in range(2):
        inside.delete("/api/files/" + "0" * 32, headers={"Authorization": "Bearer " + "x" * 40, "X-Forwarded-For": "203.0.113.7"})
    for forged in ("1.1.1.1, 203.0.113.7", "9.9.9.9, 8.8.8.8, 203.0.113.7"):  # rotating the client-controlled left side changes nothing
        r = inside.delete("/api/files/" + "0" * 32, headers={"Authorization": f"Bearer {DEMO}", "X-Forwarded-For": forged})
        assert r.status_code == 429
    other = inside.delete("/api/files/" + "0" * 32, headers={"Authorization": f"Bearer {DEMO}", "X-Forwarded-For": "198.51.100.9"})
    assert other.status_code == 404  # a different real client is not locked out by someone else's guesses
    direct = as_address(c, "8.8.8.8")  # a direct public client: its X-Forwarded-For is ignored, so it cannot hide or frame anyone
    for _ in range(2):
        direct.delete("/api/files/" + "0" * 32, headers={"Authorization": "Bearer " + "x" * 40, "X-Forwarded-For": "198.51.100.77"})
    r = direct.delete("/api/files/" + "0" * 32, headers={"Authorization": f"Bearer {DEMO}", "X-Forwarded-For": "198.51.100.200"})
    assert r.status_code == 429
    assert inside.delete("/api/files/" + "0" * 32, headers={"Authorization": f"Bearer {DEMO}", "X-Forwarded-For": "198.51.100.77"}).status_code == 404


@pytest.mark.postgres
@requires_postgres
def test_expensive_requests_are_throttled_per_address_and_recover(launch):
    c = launch({"expensive_requests_per_minute": 4})
    clock = with_clock(c)
    codes = [c.upload(make_data(300 + i, 8 * KIB)).status_code for i in range(6)]
    assert codes == [201, 201, 201, 201, 429, 429]
    r = c.upload(make_data(399, 8 * KIB))
    assert r.json()["error"]["code"] == "rate_limited" and int(r.headers["retry-after"]) >= 1
    assert c.client.get("/api/files").status_code == 200 and c.client.get("/api/storage/stats").status_code == 200  # cheap reads are free
    clock.t += 61
    assert c.upload(make_data(398, 8 * KIB)).status_code == 201
    assert c.q("SELECT count(*) FROM files WHERE status='complete'") == [(5,)]  # a refused upload did nothing at all


@pytest.mark.postgres
@requires_postgres
def test_the_public_chunking_demo_and_downloads_share_the_throttle(launch):
    c = launch({"expensive_requests_per_minute": 3})
    with_clock(c)
    fid = c.upload(make_data(310, 8 * KIB)).json()["file_id"]  # 1
    assert c.client.get(f"/api/files/{fid}/download", headers=D).status_code == 200  # 2
    assert c.client.get("/api/system/chunking-demo").status_code == 200  # 3 (public, but it costs CPU)
    r = c.client.get("/api/system/chunking-demo")
    assert r.status_code == 429 and "retry-after" in r.headers


@pytest.mark.postgres
@requires_postgres
def test_concurrent_uploads_are_limited_and_the_slot_is_always_returned(launch):
    c = launch({"max_concurrent_uploads": 1, "expensive_requests_per_minute": 1000})
    inside, release = threading.Event(), threading.Event()
    real = c.svc.uploader.checkpoint

    def hold(name):
        if name == "t1_committed":
            inside.set()
            release.wait(30)
        real(name)

    c.svc.uploader.checkpoint = hold
    first = {}
    t = threading.Thread(target=lambda: first.setdefault("r", c.upload(make_data(320, 30 * KIB))))
    t.start()
    assert inside.wait(20)
    t0 = time.perf_counter()
    r = c.upload(make_data(321, 30 * KIB))  # the only slot is taken
    assert r.status_code == 429 and time.perf_counter() - t0 < 2.0 and int(r.headers["retry-after"]) >= 1
    release.set()
    t.join(30)
    assert first["r"].status_code == 201
    c.svc.uploader.checkpoint = real
    assert c.upload(make_data(322, 30 * KIB)).status_code == 201  # the slot came back
    # a FAILING request must return its slot too
    c.fake.always["upload"] = 503
    for i in range(3):
        assert c.upload(make_data(330 + i, 30 * KIB)).status_code in (503, 409, 500)
    assert c.client.app.state.limits.upload_gate.in_flight == 0
    c.fake.always.clear()
    assert c.upload(make_data(340, 30 * KIB)).status_code == 201


@pytest.mark.postgres
@requires_postgres
def test_concurrent_restores_are_limited_to_a_safe_number(launch):
    c = launch({"max_concurrent_restores": 2, "expensive_requests_per_minute": 1000, "pool_max_size": 2})
    fid = c.upload(make_data(350, 300 * KIB)).json()["file_id"]
    c.fake.latency = 0.25
    out = []
    ts = [threading.Thread(target=lambda: out.append(c.client.get(f"/api/files/{fid}/download", headers=D).status_code)) for _ in range(5)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sorted(out).count(200) >= 2 and 429 in out and set(out) <= {200, 429}  # never more than 2 at once; the rest told to retry
    assert c.client.app.state.limits.restore_gate.in_flight == 0
    c.fake.latency = 0
    assert c.client.get(f"/api/files/{fid}/download", headers=D).status_code == 200


@pytest.mark.postgres
@requires_postgres
def test_only_one_maintenance_operation_runs_at_a_time(launch):
    c = launch({"expensive_requests_per_minute": 1000})
    inside, release = threading.Event(), threading.Event()
    real = c.svc.auditor.verify_store

    def slow(*a, **k):
        inside.set()
        release.wait(30)
        return real(*a, **k)

    c.svc.auditor.verify_store = slow
    first = {}
    t = threading.Thread(target=lambda: first.setdefault("r", c.client.post("/api/integrity/audit", headers=A, json={})))
    t.start()
    assert inside.wait(20)
    second = c.client.post("/api/integrity/audit", headers=A, json={})
    assert second.status_code == 429 and second.json()["error"]["code"] == "rate_limited"
    assert c.client.post("/api/admin/gc", headers=A).status_code == 429  # GC counts as maintenance too
    assert c.client.get("/api/benchmarks").status_code == 200  # reading results is never gated
    release.set()
    t.join(30)
    assert first["r"].status_code == 200


@pytest.mark.postgres
@requires_postgres
def test_the_limits_can_be_switched_off_explicitly_and_do_not_exist_locally(launch, client):
    c = launch({"rate_limiting": False, "expensive_requests_per_minute": 1, "auth_failure_limit": 1})
    assert not hasattr(c.client.app.state, "limits")
    assert [c.upload(make_data(360 + i, 4 * KIB)).status_code for i in range(4)] == [201] * 4
    assert all(r.status_code == 401 for r in wrong(c, n=4))
    assert not hasattr(client.app.state, "limits")  # the local backend: no limiter, behaviour unchanged


@pytest.mark.postgres
@requires_postgres
def test_a_429_is_readable_by_the_browser_through_cors(launch):
    c = launch({"expensive_requests_per_minute": 1})
    with_clock(c)
    c.upload(make_data(370, 4 * KIB))
    r = c.client.post("/api/files", files={"file": ("a.bin", b"x" * 100)}, headers={**D, "Origin": "https://app.example"})
    assert r.status_code == 429 and "retry-after" in r.headers["access-control-expose-headers"].lower()


@pytest.mark.postgres
@requires_postgres
def test_without_configured_proxy_networks_rotating_forwarded_for_cannot_evade_the_lockout(launch):
    """Default deployment (no trusted networks): even a private-address peer gets no say in its own address."""
    c = launch({"auth_failure_limit": 3})
    with_clock(c)
    peer = as_address(c, "10.0.0.5")
    for i in range(3):
        peer.delete("/api/files/" + "0" * 32, headers={"Authorization": "Bearer " + "x" * 40, "X-Forwarded-For": f"198.51.100.{i}"})
    for i in range(4):  # a fresh claimed client address each time does not reset or dodge the counter
        r = peer.delete("/api/files/" + "0" * 32, headers={"Authorization": f"Bearer {DEMO}", "X-Forwarded-For": f"203.0.113.{i}"})
        assert r.status_code == 429
