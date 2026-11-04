import hashlib
import io

import pytest

from app.chunking.rabin import POLY, POLY_DEGREE, RabinChunker, polymod, reference_fingerprint
from app.core.config import KIB, CDCConfig
from tests.conftest import make_data

DEFAULT = CDCConfig()


def chunks_of(data: bytes, config=DEFAULT, read_size=64 * KIB):
    return list(RabinChunker(config, read_size).chunk_stream(io.BytesIO(data)))


def hashes(chunks):
    return [hashlib.sha256(c.data).hexdigest() for c in chunks]


def fixed_chunks(data: bytes, size: int):
    return [data[i:i + size] for i in range(0, len(data), size)]


# ---- the fingerprint itself ----------------------------------------------

def test_polynomial_is_irreducible_of_degree_53():
    """x^(2^53) == x (mod P) and P has no roots; for prime degree 53 this proves irreducibility."""
    def mulmod(a, b):
        r = 0
        while b:
            if b & 1:
                r ^= a
            b >>= 1
            a <<= 1
            if a.bit_length() > POLY_DEGREE:
                a ^= POLY
        return r

    assert POLY_DEGREE == 53
    y = 2
    for _ in range(POLY_DEGREE):
        y = mulmod(y, y)
    assert y == 2
    assert POLY & 1 == 1  # no root at x = 0
    assert bin(POLY).count("1") % 2 == 1  # no root at x = 1


def naive_chunks(data: bytes, cfg: CDCConfig):
    """Reference chunker: recomputes the full window polynomial at every position."""
    out, start = [], 0
    while start < len(data):
        remaining = len(data) - start
        cut = None
        for length in range(cfg.min_chunk_size, min(cfg.max_chunk_size, remaining) + 1):
            if length == cfg.max_chunk_size:
                cut = length
                break
            window = data[start + length - cfg.window_size:start + length]
            if reference_fingerprint(window) % cfg.boundary_divisor == cfg.boundary_divisor - 1:
                cut = length
                break
        if cut is None:
            cut = remaining
        out.append(data[start:start + cut])
        start += cut
    return out


@pytest.mark.parametrize(
    "cfg,size",
    [
        (CDCConfig(min_chunk_size=64, avg_chunk_size=256, max_chunk_size=2048, window_size=16), 24_000),
        (CDCConfig(min_chunk_size=128, avg_chunk_size=640, max_chunk_size=4096, window_size=32), 24_000),
    ],
)
def test_rolling_fingerprint_matches_direct_polynomial_computation(cfg, size):
    data = make_data(11, size)
    rolled = [c.data for c in chunks_of(data, cfg)]
    assert rolled == naive_chunks(data, cfg)
    assert len(rolled) > 5  # the comparison actually exercised content-defined cuts
    assert any(len(c) < cfg.max_chunk_size for c in rolled[:-1])


def test_polymod_basic():
    assert polymod(0) == 0
    assert polymod(POLY) == 0
    assert polymod(0b101) == 0b101  # already reduced


# ---- structural properties -------------------------------------------------

def check_invariants(data, chunks, cfg=DEFAULT):
    assert b"".join(c.data for c in chunks) == data
    assert [c.index for c in chunks] == list(range(len(chunks)))
    off = 0
    for c in chunks:
        assert c.offset == off
        off += c.size
    for c in chunks[:-1]:
        assert cfg.min_chunk_size <= c.size <= cfg.max_chunk_size
    if chunks:
        assert 0 < chunks[-1].size <= cfg.max_chunk_size


def test_empty_input_yields_no_chunks():
    assert chunks_of(b"") == []


@pytest.mark.parametrize("n", [1, 100, 4095, 4096, 4097])
def test_small_inputs(n):
    data = make_data(2, n)
    chunks = chunks_of(data)
    check_invariants(data, chunks)
    if n <= DEFAULT.min_chunk_size:
        assert len(chunks) == 1  # cannot cut before the minimum


def test_random_binary_larger_than_max():
    data = make_data(3, 600 * KIB)
    chunks = chunks_of(data)
    check_invariants(data, chunks)
    assert len(chunks) > 5
    assert len({c.size for c in chunks}) > 3  # genuinely variable-sized


def test_all_byte_values():
    data = bytes(range(256)) * 2000
    check_invariants(data, chunks_of(data))


def test_repetitive_data_is_cut_at_max_size():
    data = bytes(400 * KIB)  # all zeros: fingerprint is constant, no content boundary
    chunks = chunks_of(data)
    check_invariants(data, chunks)
    assert [c.size for c in chunks] == [128 * KIB, 128 * KIB, 128 * KIB, 16 * KIB]
    assert len(set(hashes(chunks))) == 2  # identical chunks are still identical chunks


def test_short_period_repetition():
    data = b"abcdefgh" * 40_000
    check_invariants(data, chunks_of(data))


def test_deterministic():
    data = make_data(4, 300 * KIB)
    assert hashes(chunks_of(data)) == hashes(chunks_of(data))


@pytest.mark.parametrize("read_size", [1, 7, 4095, 4096, 65_537, 1 << 20])
def test_boundaries_independent_of_read_buffer_size(read_size):
    data = make_data(5, 150 * KIB)
    assert [c.size for c in chunks_of(data, read_size=read_size)] == [c.size for c in chunks_of(data)]


def test_short_reads_from_stream():
    class Trickle(io.RawIOBase):
        def __init__(self, data):
            self._b = io.BytesIO(data)

        def readable(self):
            return True

        def read(self, n=-1):
            return self._b.read(min(n, 333))

    data = make_data(6, 120 * KIB)
    got = list(RabinChunker().chunk_stream(Trickle(data)))
    assert [c.size for c in got] == [c.size for c in chunks_of(data)]


def test_read_error_is_reported():
    from app.chunking.interfaces import ChunkingError

    class Broken(io.RawIOBase):
        def read(self, n=-1):
            raise OSError("disk gone")

    with pytest.raises(ChunkingError):
        list(RabinChunker().chunk_stream(Broken()))


def test_custom_config_respected():
    cfg = CDCConfig(min_chunk_size=256, avg_chunk_size=1024, max_chunk_size=4096, window_size=32)
    data = make_data(7, 100 * KIB)
    chunks = chunks_of(data, cfg)
    check_invariants(data, chunks, cfg)
    assert max(c.size for c in chunks) <= 4096


# ---- stability under edits, CDC vs fixed-size ------------------------------

def reuse(old_hashes, new_hashes):
    old = set(old_hashes)
    return sum(h in old for h in new_hashes) / len(new_hashes)


@pytest.fixture(scope="module")
def base():
    data = make_data(8, 768 * KIB)
    return data, hashes(chunks_of(data)), hashes([type("C", (), {"data": c}) for c in fixed_chunks(data, 16 * KIB)])


def test_insertion_near_start_keeps_most_chunks_cdc_but_not_fixed(base, capsys):
    data, cdc_old, fixed_old = base
    edited = data[:100] + b"INSERTED-BYTES-37-LONG-xxxxxxxxxxxx" + data[100:]
    cdc_new = hashes(chunks_of(edited))
    fixed_new = hashes([type("C", (), {"data": c}) for c in fixed_chunks(edited, 16 * KIB)])
    cdc_reuse, fixed_reuse = reuse(cdc_old, cdc_new), reuse(fixed_old, fixed_new)
    print(f"insertion: CDC reuse {cdc_reuse:.3f} ({len(cdc_new)} chunks), fixed reuse {fixed_reuse:.3f}")
    assert cdc_reuse > 0.9
    assert fixed_reuse < 0.1


def test_deletion_near_start(base):
    data, cdc_old, fixed_old = base
    edited = data[:100] + data[150:]
    cdc_new = hashes(chunks_of(edited))
    fixed_new = hashes([type("C", (), {"data": c}) for c in fixed_chunks(edited, 16 * KIB)])
    print(f"deletion: CDC reuse {reuse(cdc_old, cdc_new):.3f}, fixed reuse {reuse(fixed_old, fixed_new):.3f}")
    assert reuse(cdc_old, cdc_new) > 0.9
    assert reuse(fixed_old, fixed_new) < 0.1


def test_in_place_modification_in_middle_changes_few_chunks(base):
    data, cdc_old, fixed_old = base
    mid = len(data) // 2
    edited = data[:mid] + bytes(b ^ 0xFF for b in data[mid:mid + 20]) + data[mid + 20:]
    cdc_new = hashes(chunks_of(edited))
    changed_cdc = sum(h not in set(cdc_old) for h in cdc_new)
    fixed_new = hashes([type("C", (), {"data": c}) for c in fixed_chunks(edited, 16 * KIB)])
    changed_fixed = sum(h not in set(fixed_old) for h in fixed_new)
    print(f"modification: CDC changed {changed_cdc}/{len(cdc_new)}, fixed changed {changed_fixed}/{len(fixed_new)}")
    assert 1 <= changed_cdc <= 3
    assert changed_fixed == 1  # in-place edits are NOT a case where CDC wins
