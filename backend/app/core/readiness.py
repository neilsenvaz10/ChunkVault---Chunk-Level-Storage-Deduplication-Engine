"""Readiness of the database dependency, with recovery that needs no background worker and no migration.

At start-up the cloud backend may find the database unreachable (Supabase paused or restarting, a network blip). The process then stays
ALIVE (liveness answers) but NOT READY: every request that needs the database is refused with 503 ``database_unavailable`` immediately
(no 15-second waits for a pooled connection), and the first request after ``retry_seconds`` re-checks the database with a short,
single-flight probe. When it answers and the schema verification passes, the service is ready again by itself.

What it never does: run a migration, create anything, retry in a loop, or serve requests while the schema is unverified. A WRONG or
MISSING schema is a deployment error, not an outage, and still stops the process at start-up.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from app.core.errors import DatabaseUnavailableError

logger = logging.getLogger("chunkvault.readiness")


class Readiness:
    def __init__(self, check: Callable[[], None], retry_seconds: float, clock=time.monotonic) -> None:
        self._check, self._retry, self._clock = check, retry_seconds, clock
        self.ready = False
        self.reason: str | None = None
        self._last_attempt = float("-inf")
        self._single_flight = threading.Lock()

    def mark_ready(self) -> None:
        self.ready, self.reason = True, None

    def mark_unavailable(self, reason: str) -> None:
        """Fail fast from now on: called at start-up and whenever a request meets an unreachable database."""
        if self.ready:
            logger.warning("the database became unavailable (%s); refusing database requests until it answers again", reason)
        self.ready, self.reason = False, reason
        # the next probe is allowed ``retry_seconds`` after THIS moment, not immediately: no retry storm
        self._last_attempt = self._clock()

    def ensure_ready(self) -> None:
        if self.ready:
            return
        if self._clock() - self._last_attempt < self._retry or not self._single_flight.acquire(blocking=False):
            raise DatabaseUnavailableError("the database is not available right now; retry shortly")
        try:
            self._last_attempt = self._clock()
            try:
                self._check()
            except DatabaseUnavailableError as exc:
                self.reason = type(exc).__name__
                raise DatabaseUnavailableError("the database is not available right now; retry shortly") from None
            self.mark_ready()
            logger.info("the database is available again; the service is ready")
        finally:
            self._single_flight.release()
