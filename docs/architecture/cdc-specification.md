# CDC Specification (Rabin-style content-defined chunking)

Status: **implemented in Phase 2** in `backend/app/chunking/rabin.py` (parameters in `CDCConfig`, `backend/app/core/config.py`). Pure Python; throughput is measured in Phase 6.

## Parameters

| Parameter | Default | Env var |
|---|---|---|
| Minimum chunk size | 4 KiB | `CHUNKVAULT_CDC_MIN_CHUNK_SIZE` |
| Target average chunk size | 16 KiB | `CHUNKVAULT_CDC_AVG_CHUNK_SIZE` |
| Maximum chunk size | 128 KiB | `CHUNKVAULT_CDC_MAX_CHUNK_SIZE` |
| Rolling window | 64 bytes | `CHUNKVAULT_CDC_WINDOW_SIZE` |

Validated invariants: all positive integers; `window ≤ min < avg < max`.

## Boundary detection

A Rabin fingerprint treats the bytes in a sliding window as coefficients of a polynomial over
GF(2) and reduces it modulo a fixed irreducible polynomial. It is a *rolling* hash: when the
window advances one byte, the new fingerprint is computed in O(1) from the old one, the byte
leaving the window and the byte entering it (using precomputed tables).

For each file:

1. Start a chunk at offset `s`. Do not test for a boundary until the chunk holds `min` bytes (this also guarantees the window is full).
2. Beyond `min`, slide the 64-byte window one byte at a time and compute fingerprint `fp`.
3. Cut after the current byte when `fp mod D == D − 1`, where `D = avg − min` (`CDCConfig.boundary_divisor`, 12 288 by default). Each position is a cut point with probability ≈ `1/D`, so the expected length is ≈ `min + D = avg` before truncation.
4. Force a cut at `max` bytes if no content boundary occurred.
5. The final chunk of the file is whatever remains (it may be shorter than `min`). Reset and repeat.

Because boundaries depend only on the bytes in the window, inserting data early in a file shifts
only nearby boundaries; later chunks keep their content and therefore their SHA-256 — this is
why CDC deduplicates shifted data where fixed-size chunking does not ([ADR-0001](../decisions/0001-cdc-over-fixed-size-chunking.md)).
The irreducible polynomial and the lookup tables are fixed constants so the same input always
produces the same chunks.

## Implementation details (Phase 2)

- Polynomial `P(x) = 0x3DA3358B4DC173` (degree 53, irreducible; the constant used by restic's chunker). `tests/test_cdc.py` checks `x^(2^53) = x (mod P)` and the absence of roots, which proves irreducibility for the prime degree 53.
- Fingerprint of window `w[0..63]` = sum of `w[i] * x^(8*(63-i)) mod P`. Rolling update: `fp ^= OUT[outgoing]` (removes `w[0] * x^(8*63) mod P`), then `fp = (fp << 8 | incoming)`, `fp ^= MOD[fp >> 53]` (reduction table). Two 256-entry tables per window size.
- The window state is reset at every chunk start. Bytes before position `min - window` cannot affect any test (the window is rebuilt from the last 64 bytes), so they are skipped.
- First test at chunk length `min`; cut when `fp mod D == D - 1`; forced cut at `max`; the last chunk may be shorter than `min`.
- Streaming: input is read in `read_size` blocks (default 64 KiB); memory is bounded by `max + read_size`. Boundaries do not depend on the read size (tested with 1 byte to 1 MiB reads).
- `tests/test_cdc.py` compares the rolling chunker against a naive implementation that recomputes the full window polynomial at every position (two configurations): identical boundaries.
- The 16 KiB average is a **design parameter**, not a guarantee: observed means on random data differ from it (the Phase 2 report lists measured values). CDC is not claimed to beat fixed-size chunking for in-place edits, where both change about one chunk.

## Rolling fingerprint ≠ SHA-256

- **Rolling fingerprint:** cheap, non-cryptographic, used *only* to choose where to cut. Never stored, never compared across chunks.
- **SHA-256:** computed over each complete chunk after it is cut. It is the chunk's identity (its address in the store, `chunks.hash`) and the integrity check used on restore and audit.

## Interface

```python
class Chunker(Protocol):
    def chunk_stream(self, stream: BinaryIO) -> Iterator[Chunk]: ...
```

Phase 2 correctness tests will cover: reassembly equals input, size bounds, determinism,
empty input, boundary stability after a prefix insertion, and forced max-size cuts.
