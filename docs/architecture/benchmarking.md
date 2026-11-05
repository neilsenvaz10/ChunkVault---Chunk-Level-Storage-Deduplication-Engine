# Performance Benchmarking

All numbers on this page were **measured by running the benchmark engine on one machine**. They
describe that machine, that Python implementation and ChunkVault's pure-Python chunker; they are not
claims about any other system. Reproduce them with the commands at the end.

## Test environment (where the results below were measured)
CPython 3.14.6 · Windows 11 (10.0.26300) · AMD64 · 12 logical CPUs · SQLite 3.50.4 · local NTFS temporary
directory · chunking parameters 4 KiB / 16 KiB / 128 KiB / 64-byte window.

## Dataset generation
Datasets are generated from a seed with `random.Random(seed)`; the same (workload, size, seed)
always produces byte-identical input and nothing external is needed.

| Workload | Uploads | Content |
|---|---|---|
| small / medium / large | 1 | random file (64 KiB / 512 KiB / 2 MiB presets; size field overrides) |
| identical | 2 | the same file twice |
| partial_modified | 2 | file, then a copy with 61 bytes XOR-changed in the middle |
| inserted_bytes | 2 | file, then a copy with 61 bytes inserted at offset 100 |
| deleted_bytes | 2 | file, then a copy with 61 bytes removed at offset 100 |
| repeated_content | 1 | a 20,000-byte random block repeated to the size |
| random | 1 | random file |

Bounds: file size 16 KiB–4 MiB, 1–10 iterations, seed 0–2³¹−1, a total processing budget of 48 MiB
(size × uploads × iterations), a 240-second wall-clock limit, one benchmark at a time.

## Measurement methodology
- Each benchmark runs in a **separate worker process** on a **private temporary store**; the application's data is never used and the server is never blocked (a request returns `202` and is polled; it can be cancelled, which terminates the process and removes its store).
- It drives the **real services** (the same uploader, restorer and chunk store the API uses); each iteration uses a fresh store and identical input, so spread between iterations is measurement noise, not different work. The first iteration may include cold caches.
- Wall-clock time is `time.perf_counter()` around the whole `upload()` / `restore_to_file()` call.
- **Stage timings are exclusive** (no double counting): stages can nest (for example the SHA-256 re-hash inside the chunk store's verification), so when one stage starts inside another the outer one is paused. Stages therefore sum exactly to the wall-clock time; `other` is the remainder. Upload stages: `upload_staging` (receive + fsync), `cdc` (chunker incl. reading the staged file), `sha256` (chunk identity), `dedup_read` (reading existing chunks to verify before reuse), `sha256_verify` (re-hashing during that check and during staging), `chunk_persistence` (write + fsync + atomic publish), `metadata` (SQLite connect, repository calls, BEGIN/COMMIT). Restore stages: `chunk_read`, `sha256_verify`, `metadata`, `other` (output write and whole-file hash).
- After the timed restore, an independent SHA-256 of the restored bytes is compared with the source (not timed).
- Database overhead is the growth of the SQLite file after a full checkpoint over the freshly initialised schema; it is **page-granular** (4 KiB pages), so small runs show 0.

## Measured results (median of N iterations; seed 1; 5 iterations, large: 3)
Upload throughput counts all uploads of the workload; restore covers every stored file.

| Workload | Size | Upload time | Upload MiB/s | Restore time | Restore MiB/s | Dedup ratio | Savings | Chunks (new/reused) |
|---|---|---|---|---|---|---|---|---|
| small | 64 KiB | 33 ms | 1.89 | 30 ms | 2.09 | 1.00× | 0% | 4 / 0 |
| medium | 512 KiB | 175 ms | 2.86 | 143 ms | 3.50 | 1.00× | 0% | 31 / 0 |
| large | 2 MiB | 670 ms | 2.99 | 570 ms | 3.51 | 1.00× | 0% | 116 / 0 |
| random | 512 KiB | 175 ms | 2.86 | 141 ms | 3.54 | 1.00× | 0% | 31 / 0 |
| identical | 2 × 512 KiB | 450 ms | 2.22 | 20 ms | 50.4 | **2.00×** | **50.0%** | 31 / 31 |
| partial_modified | 2 × 512 KiB | 438 ms | 2.28 | 33 ms | 30.6 | 1.92× | 47.9% | 32 / 30 |
| inserted_bytes | 2 × 512 KiB | 470 ms | 2.13 | 32 ms | 31.7 | 1.93× | 48.1% | 32 / 30 |
| deleted_bytes | 2 × 512 KiB | 427 ms | 2.34 | 32 ms | 31.8 | 1.93× | 48.1% | 32 / 30 |
| repeated_content | 512 KiB | 125 ms | 4.00 | 22 ms | 22.7 | **11.84×** | 91.6% | 3 / 24 |

Every restored file was verified (SHA-256) in every iteration. Upload throughput stayed within about 5%
between iterations (for example medium: 2.80–2.89 MiB/s).

### Where upload time goes (512 KiB random file, median)
`cdc` 106 ms (≈ 61%) · `chunk_persistence` 46 ms (≈ 26%) · `other` 11 ms · `metadata` 6 ms · `dedup_read` 4 ms ·
`upload_staging` 2 ms · `sha256` 0.8 ms · `sha256_verify` 0.6 ms. The pure-Python chunker dominates; SHA-256
itself is negligible; the fsync-bound chunk writes are second.

### Storage overhead
Database size 72 KiB (empty schema) for the small cases, 88 KiB after 62 manifest rows (identical, 512 KiB × 2)
and 136 KiB after the 2 MiB upload (116 manifest rows). Because growth is page-granular these figures only
bound the cost per manifest row (≈ a few hundred bytes at most at this scale); they are not a precise per-row size.
Average chunk size across random data was 16.4–18.1 KiB.

### An environment effect worth knowing
Restore time of a *fresh* random file (≈ 3.5 MiB/s) is much slower than restoring files whose chunks were
recently read (20–50 MiB/s), and the `dedup_read` stage of a duplicate upload is ≈ 5 ms per chunk file.
The first read of a newly written file therefore appears to carry a fixed per-file cost on this machine
(plausibly operating-system or antivirus on-access scanning; **not verified**). Absolute restore numbers
are consequently environment-sensitive.

## CDC vs fixed-size chunking (512 KiB per file, fixed size = 16 KiB, 5 iterations, seed 1)
Both schemes chunk the same bytes and hash with the same SHA-256; savings count content repeated within or across the versions.

| Case | CDC chunks / avg | CDC savings | Fixed chunks / avg | Fixed savings | Verdict (computed) | CDC time vs fixed |
|---|---|---|---|---|---|---|
| original file | 31 / 16.5 KiB | 0.0% | 32 / 16.0 KiB | 0.0% | no meaningful difference | 101 ms vs 0.3 ms |
| identical duplicate | 62 / 16.5 KiB | 50.0% | 64 / 16.0 KiB | 50.0% | no meaningful difference | 204 ms vs 0.9 ms |
| insertion near start | 62 / 16.5 KiB | **48.1%** | 65 / 15.8 KiB | **0.0%** | CDC saves more | 202 ms vs 0.9 ms |
| deletion near start | 62 / 16.5 KiB | **48.1%** | 64 / 16.0 KiB | **0.0%** | CDC saves more | 225 ms vs 1.1 ms |
| modified in the middle | 62 / 16.5 KiB | 47.9% | 64 / 16.0 KiB | 48.4% | no meaningful difference | 243 ms vs 1.1 ms |
| repeated content (+ insertion) | 9 / 113.8 KiB | 0.0% | 65 / 15.8 KiB | 0.0% | no meaningful difference | 297 ms vs 0.9 ms |
| random content (unrelated) | 68 / 15.1 KiB | 0.0% | 64 / 16.0 KiB | 0.0% | no meaningful difference | 231 ms vs 0.9 ms |

Findings:
- **Why CDC wins after insertions/deletions:** boundaries depend on the bytes around them, so after an edit near the start later boundaries land on the same content and 30 of the new version's 31 chunks were reused; fixed-size boundaries all shift and **none** were.
- **Where CDC gives no advantage:** identical copies and unrelated data behave the same; an in-place edit shifts nothing (CDC 47.9% vs fixed 48.4%). In an earlier 128 KiB-per-file run the in-place edit favoured fixed-size chunking (28.6% vs 43.8%) because CDC chunks were larger, so one edit invalidated more bytes: the result depends on the data and sizes.
- **The repeated-content dataset showed no gain for either scheme** here: a 20,000-byte period is not a multiple of 16 KiB, and CDC found no content boundary inside it (9 chunks, mostly forced 128 KiB cuts), so neither scheme found duplicates between the file and its edited copy. (The *pipeline* workload `repeated_content`, which has no edit, deduplicated 24 of 27 chunks within one file.)
- **Cost:** CDC took about 100 ms per 512 KiB version chunked (plus hashing) versus well under 1 ms for fixed-size slicing; this machine's pure-Python chunker is the dominant upload cost.

## Limitations
- One machine, one run configuration; five iterations give a median and a range, not a statistical study.
- Pure-Python chunker: throughput (≈ 2–4 MiB/s end to end) is not representative of a native implementation.
- Files are bounded to 4 MiB; behaviour for much larger files, many small files or concurrent clients is **not** measured.
- Page-granular database growth hides the true per-row cost at small scale.
- Timer overhead is small but non-zero (the instrumented path wraps ~20 functions per upload).

## Reproducing
```bash
# UI: Performance Engineering -> "Run a benchmark" (results are saved under data/benchmarks/, which is git-ignored)
# API:
curl -X POST localhost:8000/api/benchmarks -H "content-type: application/json" \
     -d '{"kind":"pipeline","workload":"identical","size_bytes":524288,"iterations":5,"seed":1}'
curl localhost:8000/api/benchmarks/<job_id>          # progress, then the full result
# tests: cd backend && python -m pytest tests/test_benchmarks.py
```

## Reporting additions (2026-11-05)

New benchmark runs also record, from the same measured timings and byte counts:

- **Seconds per MiB** for each upload and restore (`seconds / (bytes / 2^20)`, the reciprocal view of MiB/s), summarised across iterations, and for the processing time in the CDC-vs-fixed comparison.
- **On-disk footprint ratio** for pipeline benchmarks. Footprint bytes = the SQLite database file (after a full checkpoint, so it includes the empty schema and indexes) + the sizes of all chunk files. Ratio = logical bytes uploaded / footprint bytes; savings % = 100 x (logical - footprint) / logical, which is negative when metadata overhead exceeds what reuse saved. Filesystem block rounding, the SQLite -wal/-shm files and the empty staging directory are not counted. The payload-only deduplication ratio is reported separately. The CDC-vs-fixed comparison has no database, so it compares payload bytes only.
- **Cache state: `unknown`.** The operating-system file cache is neither flushed, inspected nor controlled, so no run is called cold or warm.

Results saved before these fields existed were not changed or back-filled; the Performance page shows "not recorded in this saved result" for them. The 4 MiB per-file limit is unchanged, so no tens-of-MiB throughput is reported.
