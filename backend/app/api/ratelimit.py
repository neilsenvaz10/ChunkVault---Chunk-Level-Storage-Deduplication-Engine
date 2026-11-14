"""Bounded abuse limits for the cloud backend (in memory, one process: the service runs a single worker).

What is limited
    * failed authentications, per client address (``auth_failure_limit`` within ``auth_failure_window_seconds``): once reached, every
      authentication attempt from that address is refused with 429 until the window passes. Success never resets the counter, or a
      holder of the (public) demo token could interleave one valid request to reset an attack on the admin token.
    * expensive requests, per client address (``expensive_requests_per_minute``): uploads, restores, audits, GC, simulations and the
      public chunking demo.
    * concurrency, process-wide: uploads (``max_concurrent_uploads``), restores (``max_concurrent_restores``), maintenance (1). A request
      over the limit is refused at once with 429 + Retry-After instead of queueing: queued requests would hold threads and connections.

Client address behind a reverse proxy
    ``X-Forwarded-For`` is believed ONLY when the direct peer lies inside ``trusted_proxy_cidrs`` (explicit configuration; empty by
    default). A peer merely being private or non-public proves nothing: another tenant, a sidecar or a misrouted internal client is not
    a proxy. Without configured networks the header is ignored and the direct peer's address is the client (behind a platform proxy
    that means one shared bucket for everybody: the limiter then protects the service but cannot tell visitors apart). When the peer
    is trusted, ``trusted_proxy_hops`` = the number of proxies that APPEND to the header: the entry that many places from the RIGHT is
    used; entries to its left are client claims and never used. The header must have at most 20 well-formed IP entries and at most
    1 KiB, or it is discarded.

Memory is bounded: at most ``MAX_KEYS`` addresses are tracked (oldest evicted first).
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import OrderedDict, deque

from fastapi import Request

from app.core.config import CloudSettings
from app.core.errors import TooManyRequestsError

MAX_KEYS = 4096
_MAX_XFF_BYTES = 1024
_MAX_XFF_ENTRIES = 20


def client_address(request: Request, trusted_hops: int, trusted_networks=()) -> str:
    peer = request.client.host if request.client else "unknown"
    if trusted_hops <= 0 or not trusted_networks:
        return peer
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer  # not an IP address (e.g. a test client): no basis to trust a forwarding header
    if not any(peer_ip in net for net in trusted_networks if net.version == peer_ip.version):
        return peer  # not a configured proxy: the header is just a claim by whoever connected
    raw = request.headers.get("x-forwarded-for", "")
    if not raw or len(raw.encode("latin-1", "ignore")) > _MAX_XFF_BYTES:
        return peer
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) > _MAX_XFF_ENTRIES or len(parts) < trusted_hops:
        return peer
    try:
        for p in parts:
            ipaddress.ip_address(p.strip("[]"))  # every entry must be well formed, or the header is not trusted at all
        return str(ipaddress.ip_address(parts[-trusted_hops].strip("[]")))
    except ValueError:
        return peer


class SlidingWindow:
    """At most ``limit`` events per ``window`` seconds per key."""

    def __init__(self, limit: int, window: float, clock=time.monotonic) -> None:
        self.limit, self.window, self._clock = limit, window, clock
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> deque[float]:
        q = self._events.get(key)
        if q is None:
            q = self._events[key] = deque()
            while len(self._events) > MAX_KEYS:
                self._events.popitem(last=False)
        else:
            self._events.move_to_end(key)
        while q and now - q[0] >= self.window:
            q.popleft()
        return q

    def retry_after(self, key: str) -> float:
        """0 if another event is allowed now, else the seconds until the oldest one expires."""
        with self._lock:
            now = self._clock()
            q = self._trim(key, now)
            return 0.0 if len(q) < self.limit else max(0.0, self.window - (now - q[0]))

    def record(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            self._trim(key, now).append(now)

    def hit(self, key: str) -> float:
        """Record an event if allowed; returns 0 when allowed, else the seconds to wait (nothing is recorded then)."""
        with self._lock:
            now = self._clock()
            q = self._trim(key, now)
            if len(q) >= self.limit:
                return max(0.0, self.window - (now - q[0]))
            q.append(now)
            return 0.0


class Gate:
    """A process-wide concurrency limit. ``enter`` never waits."""

    def __init__(self, name: str, limit: int) -> None:
        self.name, self.limit, self._n, self._lock = name, limit, 0, threading.Lock()

    def enter(self) -> bool:
        with self._lock:
            if self._n >= self.limit:
                return False
            self._n += 1
            return True

    def leave(self) -> None:
        with self._lock:
            self._n = max(0, self._n - 1)

    @property
    def in_flight(self) -> int:
        return self._n


class Limits:
    def __init__(self, cloud: CloudSettings, clock=time.monotonic) -> None:
        self.hops = cloud.trusted_proxy_hops
        self.proxies = tuple(ipaddress.ip_network(c) for c in cloud.trusted_proxy_cidrs)
        self.auth_failures = SlidingWindow(cloud.auth_failure_limit, cloud.auth_failure_window_seconds, clock)
        self.expensive = SlidingWindow(cloud.expensive_requests_per_minute, 60.0, clock)
        self.upload_gate = Gate("uploads", cloud.max_concurrent_uploads)
        self.restore_gate = Gate("restores", cloud.max_concurrent_restores)
        self.admin_gate = Gate("maintenance", 1)

    def address(self, request: Request) -> str:
        return client_address(request, self.hops, self.proxies)


def limits_of(request: Request) -> Limits | None:
    return getattr(request.app.state, "limits", None)  # None: the local backend (no limits, behaviour unchanged)


# ------------------------------------------------------------------------------------------------ dependencies
def _throttle(request: Request, lim: Limits) -> None:
    wait = lim.expensive.hit(lim.address(request))
    if wait:
        raise TooManyRequestsError("too many requests from this address; slow down", retry_after=wait)


def throttle_expensive(request: Request) -> None:
    """For routes that cost real CPU or bandwidth even though they are public (the chunking demo)."""
    lim = limits_of(request)
    if lim is not None:
        _throttle(request, lim)


def _gated(request: Request, gate_of, busy_message: str, retry: float):
    lim = limits_of(request)
    if lim is None:
        yield
        return
    _throttle(request, lim)
    gate: Gate = gate_of(lim)
    if not gate.enter():
        raise TooManyRequestsError(busy_message, retry_after=retry)
    try:
        yield
    finally:
        gate.leave()


def files_limits(request: Request):
    """Files router: uploads and restores are throttled and gated; deletes are throttled; reads are free."""
    path, method = request.url.path, request.method
    if method == "POST" and path.rstrip("/") == "/api/files":
        yield from _gated(request, lambda lim: lim.upload_gate, "the server is busy with other uploads; retry shortly", 5)
    elif method == "GET" and path.endswith("/download"):
        yield from _gated(request, lambda lim: lim.restore_gate, "the server is busy with other downloads; retry shortly", 3)
    elif method == "DELETE":
        throttle_expensive(request)
        yield
    else:
        yield


def maintenance_limits(request: Request):
    """Audits, reconciliation, GC, benchmarks, fault lab: one at a time, and throttled. Reads of results stay free."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        yield
        return
    yield from _gated(request, lambda lim: lim.admin_gate, "another maintenance operation is running; retry shortly", 10)


def simulation_limits(request: Request) -> None:
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        throttle_expensive(request)
