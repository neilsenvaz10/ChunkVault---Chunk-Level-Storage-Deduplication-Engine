"""Exclusive-time stage profiler.

Stages nest (for example the SHA-256 re-hash that happens inside the chunk store's verify call).
When a section starts inside another, the outer section is paused, so every nanosecond is
attributed to exactly one stage and the stages can never sum to more than the wall-clock time.
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Callable, Iterator


class Profiler:
    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self.totals: dict[str, float] = defaultdict(float)
        self._stack: list[str] = []
        self._mark = 0.0

    @contextmanager
    def section(self, name: str) -> Iterator[None]:
        now = self._clock()
        if self._stack:
            self.totals[self._stack[-1]] += now - self._mark
        self._stack.append(name)
        self._mark = now
        try:
            yield
        finally:
            now = self._clock()
            self.totals[name] += now - self._mark
            self._stack.pop()
            self._mark = now

    def current(self) -> str | None:
        return self._stack[-1] if self._stack else None

    def reset(self) -> None:
        self.totals.clear()

    def snapshot(self) -> dict[str, float]:
        return dict(self.totals)


def summarize(values: list[float]) -> dict:
    """mean / median / min / max / stdev (None for fewer than two values) of measured values."""
    if not values:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None, "stdev": None}
    return {"n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "min": min(values), "max": max(values), "stdev": statistics.stdev(values) if len(values) > 1 else None}
