"""Rabin-style content-defined chunker.

Fingerprint
-----------
The fingerprint of a byte window ``w[0..W-1]`` is the polynomial

    F(x) = sum_i  w[i](x) * x^(8*(W-1-i))   mod P(x)        over GF(2)

where each byte ``w[i]`` is read as a degree<8 polynomial and ``P(x)`` is the
degree-53 irreducible polynomial ``POLY`` below (the same constant used by
restic's chunker; irreducibility is checked in tests). Because it is a
polynomial residue it can be *rolled* in O(1) per byte: removing the oldest
byte subtracts ``w[0] * x^(8*(W-1)) mod P`` (table ``OUT``), and appending a
byte computes ``F*x^8 + b mod P`` using a 256-entry reduction table ``MOD``
indexed by the 8 bits that overflow degree 53.

Boundaries
----------
A chunk starts empty (the window is reset). No cut is allowed before ``min``
bytes. At every length ``L >= min`` the fingerprint of the last ``W`` bytes is
tested and the chunk is cut after byte ``L`` when ``F mod D == D - 1`` with
``D = avg - min`` (``CDCConfig.boundary_divisor``). A cut is forced at ``max``.
The final chunk is whatever remains and may be shorter than ``min``.
Bytes before ``min - W`` can never influence a cut, so they are skipped.

This is pure Python (~1-2 MB/s); throughput is measured in Phase 6.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import BinaryIO

from app.chunking.interfaces import Chunk, ChunkingError
from app.core.config import CDCConfig

POLY = 0x3DA3358B4DC173  # irreducible over GF(2), degree 53
POLY_DEGREE = POLY.bit_length() - 1
DEFAULT_READ_SIZE = 64 * 1024


def polymod(value: int, poly: int = POLY) -> int:
    """Reduce polynomial ``value`` (bits = GF(2) coefficients) modulo ``poly``."""
    degree = poly.bit_length() - 1
    while value.bit_length() > degree:
        value ^= poly << (value.bit_length() - 1 - degree)
    return value


def reference_fingerprint(window: bytes) -> int:
    """Direct (non-rolling) fingerprint of a window; used to validate the rolling one."""
    return polymod(int.from_bytes(window, "big"))


@lru_cache(maxsize=None)
def _tables(window_size: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    shift = 8 * (window_size - 1)
    out = tuple(polymod(b << shift) for b in range(256))
    mod = tuple((t << POLY_DEGREE) ^ polymod(t << POLY_DEGREE) for t in range(256))
    return out, mod


class RabinChunker:
    def __init__(self, config: CDCConfig | None = None, read_size: int = DEFAULT_READ_SIZE) -> None:
        if read_size <= 0:
            raise ValueError("read_size must be positive")
        self.config = config or CDCConfig()
        self.read_size = read_size
        self._out, self._mod = _tables(self.config.window_size)

    def _scan(self, buf: bytearray, i: int, fp: int) -> tuple[int, int, int]:
        """Resume scanning ``buf`` at position ``i`` with fingerprint ``fp``.
        Returns (cut_length or 0, next_position, fingerprint)."""
        cfg = self.config
        mn, mx, w, d = cfg.min_chunk_size, cfg.max_chunk_size, cfg.window_size, cfg.boundary_divisor
        out, mod = self._out, self._mod
        n = len(buf)
        limit = min(n, mx)
        p = max(i, mn - w)  # bytes before mn - w cannot affect the fingerprint at L >= mn
        top = POLY_DEGREE
        target = d - 1

        if p < mn:  # priming: fill the window, no removal, no test yet
            end = min(limit, mn)
            for q in range(p, end):
                fp = (fp << 8) | buf[q]
                fp ^= mod[fp >> top]
            p = max(p, end)
            if p == mn and fp % d == target:  # first test at L == min
                return mn, p, fp
        for q in range(p, limit):
            fp ^= out[buf[q - w]]
            fp = (fp << 8) | buf[q]
            fp ^= mod[fp >> top]
            if fp % d == target:
                return q + 1, q + 1, fp
        if n >= mx:
            return mx, mx, fp
        return 0, max(p, limit), fp

    def chunk_stream(self, stream: BinaryIO) -> Iterator[Chunk]:
        buf = bytearray()
        offset = index = 0
        pos = fp = 0
        while True:
            cut, pos, fp = self._scan(buf, pos, fp)
            if cut:
                yield Chunk(index, offset, bytes(buf[:cut]))
                del buf[:cut]
                offset += cut
                index += 1
                pos = fp = 0
                continue
            try:
                data = stream.read(self.read_size)
            except OSError as exc:
                raise ChunkingError(f"cannot read input stream: {exc}") from exc
            if not data:
                break
            buf += data
        if buf:
            yield Chunk(index, offset, bytes(buf))
