# Requirements Traceability (formal alignment, with the final acceptance corrections)

**Alignment status: COMPLETED against both original reports.**

| Source document | Authors / course | Read in full |
|---|---|---|
| *System Design Report — System Design Micro-Project* (7 pages) | Krishna Lohana, Neilsen Vaz · D12A · System Design Micro-Project | yes |
| *Chunk-Level Storage Deduplication Engine — Feasibility Report* | same | yes |
| *System Architecture (Single-Machine Prototype)* diagram supplied with them | same | yes |

**The original academic reports were reviewed but are not stored in the public repository.** They were supplied as PDFs outside it
(`docs/references/` does not exist), so the Documentation page banner says so and points here. Adding them would publish the
authors' coursework, which is the owners' decision.
Requirements below are quoted or paraphrased from those reports only; nothing was inferred beyond them. Section numbers are
the reports' own: **SD** = System Design Report, **FS** = Feasibility Report, **DG** = the architecture diagram.

## Verdict

- **No requirement of the reports for the single-machine prototype is missing in a way that affects correctness.** Every correctness
  criterion in the reports' evaluation plans and in the Feasibility Report's conditional GO decision (deterministic chunking,
  byte-perfect reconstruction, safe reference handling, transparent benchmark reporting) is implemented and has test evidence.
- **The one implementation gap found in the first pass — no free-disk-space check before an upload — is now implemented** as an advisory
  preflight on the chunk store's volume, with disk-full handling at staging, publication and SQLite writes, and regression tests
  (`test_upload_free_space`: 23 tests).
- **Three of the four measurement/reporting gaps are now implemented** for newly run benchmarks: seconds per MiB, an aggregate
  on-disk-footprint ratio with an explicit numerator and denominator, and an explicit cache state, recorded as `unknown` because the
  OS file cache is neither controlled nor measured (`test_benchmark_reporting`: 20 tests; `reporting.test.jsx`: 6 tests).
  Results saved earlier are untouched and the UI says "not recorded in this saved result" for them.
- **Two items remain, both documented limitations rather than defects:** benchmarks stay capped at 4 MiB per file (no tens-of-MiB
  fixture), and the footprint ratio exists for pipeline benchmarks only (the CDC-vs-fixed comparison has no database and compares
  payload bytes).
- **Everything else that differs is an acceptable prototype limitation, an optional item, or a naming difference** (section C).
- **Distributed features (replication, load balancing, sharded index, object store, multi-node HA, concurrent writers) are out of
  scope in both reports and are not implemented or claimed.** The application's own pages describe them as conceptual only.

Status values (as actually evidenced): **Implemented** (in the code, with test or script evidence) · **Partially implemented**
(implemented, but narrower than the report states) · **Documented limitation** (not done, deliberately, and stated in the repository) ·
**Not applicable** (out of scope or optional in the report, so not required of the prototype).

Evidence key: tests are in `backend/tests/` (`test_cdc`, `test_hashing_and_store`, `test_storage_engine`, `test_storage_api`,
`test_hardening`, `test_faultlab`, `test_benchmarks`, `test_simulation`, `test_security`, `test_database_schema`,
`test_docs_consistency`, `test_report_acceptance`); **V2** / **V6** = `scripts/verify_phase2.py` (27 checks) and
`scripts/verify_phase6.py` (74 checks); **UI** = `frontend/src/test/*` (161 tests).

## A. Matrix — System Design Report

| ID | Source | Requirement | Implementation | Evidence | Status |
|---|---|---|---|---|---|
| SD-1 | §2.1 | Deterministic content-defined chunking, compared with fixed-size chunking | `chunking/rabin.py`; fixed-size baseline in `/api/system/chunking-demo` and the benchmark `chunking_comparison` | `test_cdc` (determinism, insertion stability); V6 Test A (independent re-chunk agrees); `test_benchmarks` | Implemented |
| SD-2 | §2.1 | Identify chunks by SHA-256; avoid duplicate physical writes | `hashing/`, `deduplication/`, `storage/filesystem.py` | `test_hashing_and_store`; `test_storage_engine`; V6 Test B (no new chunk files); `test_report_acceptance` (3 identical uploads) | Implemented |
| SD-3 | §2.1 | Persistent file records, ordered manifests, reference counts | `database/schema.sql` + triggers | `test_database_schema`; V6 DB checks (`integrity_check`, `foreign_key_check`, ref_count invariant) | Implemented |
| SD-4 | §2.1, §5.3 | Restore byte-for-byte; report missing or corrupt chunks clearly | `restoration/` | `test_storage_api` (`chunk_missing`, `chunk_corrupt`); `test_storage_engine`; V6 Tests D, F | Implemented |
| SD-5 | §2.1, §9 | Evaluate storage savings, upload time, restore time on repeatable datasets | `benchmarking/` (seeded datasets, worker process) | `test_benchmarks`; V6 benchmark checks; measured tables in `docs/architecture/benchmarking.md` | Implemented |
| SD-6 | §2.1, §7 | Outline a future scale-out architecture while limiting claims to local evidence | `docs/architecture/scalability.md`, `security-and-scalability.md`; UI Trade-offs and Simulation pages | `test_docs_consistency`; UI trade-offs tests (Implemented vs Not implemented labels) | Implemented (documentation) |
| SD-7 | §3 | Local CLI **or** optional lightweight HTTP interface | FastAPI HTTP API and React UI; **no CLI** | `test_storage_api`; UI tests; V2 | Implemented (HTTP interface; CLI not built — the report allows either) |
| SD-8 | §3 | Single-process CDC, hashing and deduplication; sequential uploads | one process, single-writer lock | `test_storage_engine` (writer lock); V2 | Implemented |
| SD-9 | §3 | SQLite metadata and local filesystem chunks | `database/`, `storage/` | V6 DB checks | Implemented |
| SD-10 | §3 | Upload, restore, metadata lookup and safe deletion | `POST /api/files`, `GET …/download`, `GET /api/files/{id}`, `DELETE …` | `test_storage_api`; V6 Tests A–E | Implemented |
| SD-11 | §3 | Manual integrity / reconciliation checks and benchmarks | `POST /api/integrity/audit`, `…/reconcile`, `/api/benchmarks` | `test_hardening`; `test_faultlab`; V6 Test F, G | Implemented |
| SD-12 | §3 | Chunker settings 4 KiB min, 16 KiB target, 128 KiB max, 64-byte window (configurable) | `CDCConfig` defaults; env overrides | `test_config`; `test_report_acceptance` (exact values) | Implemented |
| SD-13 | §3 | Excluded: load balancer, replicated APIs, distributed writers, sharded KV / cloud store, replication, HA | not implemented | `docs/architecture/security-and-scalability.md`; UI labels | Not applicable (out of scope in the report; not implemented and not claimed) |
| SD-14 | §4 | Seven-stage architecture: client, chunker, hasher, dedup manager, chunk store, SQLite manager, restore/verify | `api/`, `chunking/`, `hashing/`, `deduplication/`, `storage/`, `database/`, `restoration/` | LLD (`docs/architecture/lld.md`); UI architecture page | Implemented |
| SD-15 | §4.1 | Tables `chunks(hash, size_bytes, ref_count, state)`, `files(file_id, name, file_size, sha256, status)`, `file_chunks(file_id, position, chunk_hash)` | same tables; **names differ in three columns** (name→`filename`, file_size→`original_size`, position→`chunk_index`); extra columns `chunk_size`, `created_at`; extra `operations` table | `test_database_schema`; `test_report_acceptance` | Implemented (naming deviation, section C) |
| SD-16 | §4.1 | A chunk repeated within one file counts once per manifest occurrence | trigger-maintained `ref_count` | V6 "repeated chunk inside one file counts once per occurrence"; `test_hardening` | Implemented |
| SD-17 | §5.1 | Rolling Rabin-style fingerprint; boundary only after the minimum; forced cut at maximum; SHA-256 is separate | degree-53 polynomial, boundary `fp mod 12288 == 12287`, forced cut at 128 KiB | `test_cdc` (irreducible polynomial, rolling = direct fingerprint, forced max cut); `docs/architecture/cdc-specification.md` | Implemented |
| SD-18 | §5.1 | Insertion near the start does not shift all later boundaries | boundaries depend on content only | `test_cdc`; V6 Test C (44 of 45 chunks reused after a 61-byte insertion); benchmark: CDC 48.1 % vs fixed 0.0 % | Implemented |
| SD-19 | §5.2 | Read the input incrementally, not whole into memory | upload streamed to staging; chunker reads a stream | `test_cdc`; `test_hardening` (bounded bodies) | Implemented |
| SD-20 | §5.2 | Commit file record, ordered manifest and references in **one** SQLite transaction after chunks exist | T2 single transaction | `test_storage_engine`; `test_faultlab` (`crash_before_metadata_commit`, `metadata_failure`) | Implemented |
| SD-21 | §5.3 | Restore verifies each chunk digest and the whole-file digest; any failure is a failed restore, never a silent partial file | streaming restore with abort on mid-stream failure | `test_storage_api`; V6 Test F ("no partial output remains") | Implemented |
| SD-22 | §6 | New chunks written to temp files, flushed, renamed before metadata commit | staging + `fsync` + atomic rename | `test_hashing_and_store`; `docs/architecture/consistency-protocol.md` | Implemented (power-loss durability not tested — stated limit in both reports) |
| SD-23 | §6 | Interruption may leave unreferenced chunks; a reconciliation pass finds them; no checkpoint resumption | read-only audit + explicit reconcile; retry re-uses chunks; no resumption | `test_faultlab` (9 scenarios); V6 Test G (second reconcile = 0 actions) | Implemented |
| SD-24 | §6 | Deletion removes references transactionally; chunk files removed only when no manifest references them; GC confirms first | delete = one transaction, then unlink only chunks whose count reached zero; reconcile removes leftovers | `test_hardening` (tampered counters never delete a shared chunk); V6 Test E | Implemented (timing differs from the report's separate GC step, section C) |
| SD-25 | §6 | Safeguards are proposed, to be exercised by failure-injection tests before being called verified | isolated fault-injection lab | `test_faultlab`; V6 Test G | Implemented |
| SD-26 | §7 | Local prototype is intentionally single-process; scale-out is a target architecture, not evaluated | no distributed code; documented as conceptual | `test_docs_consistency`; UI | Implemented |
| SD-27 | §8 | Python 3, Rabin-style fingerprint, SHA-256/`hashlib`, SQLite, local filesystem, Python benchmark scripts with deterministic fixtures | as listed (Python 3.14, FastAPI added for the optional HTTP interface) | repository | Implemented |
| SD-28 | §8, §10 | *Optional* capacity simulation of client, handler, dedup engine, metadata service, chunk store: request rate, service time, replicas → utilisation, queue length, throughput, P95/P99 latency; results conditional on assumptions | **different model:** `simulation/model.py` projects storage, metadata and growth from stated assumptions; it has **no queue/latency model** | `test_simulation`; V6 model checks; labelled "Mathematical model — not a measurement or load test" | Not applicable (optional in the report; a different model is implemented, section C) |
| SD-29 | §9 | Storage saved % and deduplication ratio, with unique-payload bytes distinguished from the full on-disk footprint | Dashboard and benchmark report both; savings use payload bytes | `test_storage_api` (stats); UI dashboard tests | Implemented |
| SD-30 | §9 | Experiments: identical uploads add no payload; overlapping files share chunks; edited file vs fixed-size; restore integrity; missing/corrupt chunk fails explicitly; interrupted upload safe; deleting one file keeps shared chunks; timing with trials | see rows SD-2, 4, 18, 21, 23, 24, 5 | `test_report_acceptance`; V6 Tests A–G; `test_benchmarks` | Implemented |
| SD-31 | §9 | Fixtures: unrelated files, exact duplicates, edited text and binary, **a larger file of tens of MiB** | seeded workloads: identical, partial, modified, repeated, random, inserted/deleted bytes; sizes small/medium/large **up to 4 MiB**; API accepts uploads up to 64 MiB | `test_benchmarks`; benchmarking.md | **Documented limitation** — benchmark cap of 4 MiB kept; no tens-of-MiB benchmark (section C, P1) |
| SD-32 | §9 | Measurements identify machine, repetitions, chunk parameters and cache state | environment, iterations and chunk parameters recorded with every result; **cache state recorded as `unknown`** (`conditions.cache_state`) with a note that the OS cache was neither flushed, inspected nor controlled; runs are never called cold or warm | `test_benchmarks`; `test_benchmark_reporting`; `reporting.test.jsx` | Implemented (cache state is declared unknown, not measured) |
| SD-33 | §9, §12 | No experimental numbers asserted in the design | the repository reports measured numbers only from runs, with environment | benchmarking.md; UI shows none until a benchmark has run | Implemented |
| SD-34 | §11 | Mitigations: boundary-stability tests; commit ordering + reconciliation; shared/repeated-chunk deletion tests; random/low-overlap baselines; streaming reads, file limits **and available-space checks**; separate proposed vs measured | all implemented; available-space check = advisory preflight on the chunk store's volume (`Uploader._check_free_space`) plus disk-full handling | `test_cdc`; `test_hardening`; `test_faultlab`; `test_benchmarks`; **`test_upload_free_space`** | Implemented (advisory, not a guarantee against concurrent disk exhaustion) |

## B. Matrix — Feasibility Report

| ID | Source | Requirement | Implementation | Evidence | Status |
|---|---|---|---|---|---|
| FS-1 | §1, §10 | Single-machine prototype only; no distributed fault tolerance, HA or production performance claimed | none claimed | security-and-scalability.md; UI | Implemented |
| FS-2 | §2 | Answer five questions: deterministic boundaries; reuse across files and versions; byte-for-byte reconstruction; detect missing/corrupt chunks; quantify savings and overhead on a reproducible benchmark | each implemented | SD-1, 2, 4, 5, 18 | Implemented |
| FS-3 | §3 | Scope: CDC + fixed-size baseline; SHA-256 ids and integrity checks; SQLite + content-addressed files; sequential uploads; manual maintenance/recovery tests | as listed | SD-1…SD-11 | Implemented |
| FS-4 | §3 | Out of scope: distributed KV, multi-instance APIs, authentication, tenant isolation, continuous background services, concurrent writers | none implemented; **no authentication** (documented limit; local Host/Origin guard only) | `test_security`; security-review.md | Not applicable (out of scope per the report) |
| FS-5 | §3 | Parameters 4/16/128 KiB, 64 B window are starting values to be validated empirically; a 16 KiB interval needs a suitable mask/modulus confirmed on representative data | modulus 12 288 (= target − minimum); measured mean chunk 16.0–16.5 KiB in benchmarks | `test_cdc`; `test_report_acceptance`; benchmarking.md (CDC avg chunk) | Implemented (synthetic data) |
| FS-4a | §3 | Only one writer at a time | thread + OS file-lock writer | `test_storage_engine` (`WriterBusyError`) | Implemented |
| FS-6 | §4 | Pipeline: incremental read, boundaries, per-chunk digest, check index, store new, record ordered refs, publish complete; restore reverses, verifying each chunk and the whole file | as designed | SD-19…SD-21 | Implemented |
| FS-7 | §4 | SHA-256 per chunk **and whole file** | both computed | `test_storage_engine`; V6 Test A | Implemented |
| FS-8 | §4 | Schema `chunks(hash PK, size_bytes, ref_count, state)`, `files(file_id PK, original_name, file_size, sha256, status)`, `file_chunks(file_id, position, chunk_hash)`; ordered mapping because a chunk can repeat | see SD-15 (`original_name`→`filename`, `file_size`→`original_size`, `position`→`chunk_index`) | `test_report_acceptance` | Implemented (naming) |
| FS-9 | §4 | Maintenance is on-demand (integrity scan, orphan cleanup, GC), not a continuous worker | audit and reconcile run only when requested | `test_hardening`; UI | Implemented |
| FS-10 | §5 | Invariant 1: completed file records reference existing verified chunks | T2 commit after chunks exist; audit and verified restore detect violations | `test_faultlab` invariants I1–I8; V6 Test G | Implemented |
| FS-11 | §5 | Invariant 2: deletion never removes a chunk referenced by another completed file | counts are per occurrence; `ON DELETE RESTRICT` on manifest→chunk | `test_hardening`; V6 Test E; V6 DB "referenced chunk cannot be deleted" | Implemented |
| FS-12 | §5.1 | Temp path → flush → atomic rename → one metadata transaction; orphans removed only after confirming no active upload uses them; power-loss durability not guaranteed | staged files removed only when stale and under the writer lock; durability limit documented | `test_storage_engine`; `test_hardening`; consistency-protocol.md | Implemented |
| FS-13 | §5.2 | A failed upload never publishes a completed record; retry may reuse chunks; no checkpoint resumption | failed uploads end `failed`; retry reuses | `test_faultlab`; `test_storage_engine` | Implemented |
| FS-14 | §5.2 | Physical removal only after no references remain **and a reconciliation/GC pass confirms eligibility**; tests must not delete chunks still referenced | removal happens right after the delete transaction commits with zero references; reconcile handles leftovers | `test_hardening`; V6 Test E | Implemented (stricter-timing deviation, section C) |
| FS-15 | §5.3 | Verify each chunk against its SHA-256 name and the whole file; hash collisions assumed negligible | as designed; an equal digest with a different size is treated as corruption | `test_hashing_and_store`; `test_storage_api` | Implemented |
| FS-16 | §5.3 | If an HTTP API is exposed: file-size limits, path sanitisation, input validation, **disk-space checks** | 64 MiB limit, bounded bodies, filename sanitisation, schema validation, **advisory free-space preflight** (`507 insufficient_storage`) and disk-full translation at staging, publication and SQLite writes | `test_hardening`; `test_security` (53 tests); **`test_upload_free_space`** (23 tests) | Implemented (advisory) |
| FS-17 | §6 | Streaming and bounded input keep memory predictable | streaming chunker; upload and body limits | `test_hardening`; `test_security` | Implemented |
| FS-18 | §6 | Manual execution and logs; no uptime or multi-node claims | operations table records upload/restore/delete/audit/reconcile outcomes; errors logged by the API | `test_database_schema` | Implemented |
| FS-19 | §7 | Compare workloads of different redundancy, not only the most favourable case | workloads from random (low redundancy) to identical (high) | `test_benchmarks`; benchmarking.md | Implemented |
| FS-20 | §7 | Define physical bytes two ways: (a) unique payload for algorithm comparison; (b) total on-disk footprint incl. SQLite and filesystem overhead for operational reporting; same convention for CDC and fixed | (a) used for savings everywhere; (b) **now an aggregate ratio** for pipeline benchmarks: footprint = SQLite file (after checkpoint) + chunk-file bytes; ratio = logical bytes ÷ footprint; savings % = 100 × (logical − footprint) ÷ logical (excludes filesystem block rounding, `-wal`/`-shm`, staging). The CDC-vs-fixed comparison has no database and uses (a) for both schemes | `test_benchmark_reporting`; `reporting.test.jsx`; UI dashboard tests | **Partially implemented** (ratio for pipeline benchmarks only; comparison uses payload bytes) |
| FS-21 | §7 | Record chunk count, mean chunk size, unique chunk bytes, metadata bytes, upload time, restore time, throughput (MiB/s) and seconds/MiB; state hardware, OS, dataset, cache policy, repetitions | all recorded; **seconds per MiB** per operation, in the summary (mean/median/min/max/stdev) and for CDC vs fixed processing time; **cache state `unknown`** declared in every new result | `test_benchmarks`; `test_benchmark_reporting`; `reporting.test.jsx` | Implemented for newly run benchmarks (older saved results are not back-filled) |
| FS-22 | §7 | Savings may be negative for small or nonredundant files once metadata is counted; no cloud-cost estimate | random workload reports ≈ 0 % payload savings; database overhead reported separately; no cost claim | benchmarking.md | Implemented |
| FS-23 | §8 | Each risk mitigation demonstrated by a reproducible test | CDC boundary tests; crash-before-commit; refcount and deletion tests; corrupt/missing chunk; low-savings datasets; limits | `test_cdc`, `test_faultlab`, `test_hardening`, `test_storage_api`, `test_benchmarks` | Implemented |
| FS-24 | §9 | Test plan: repeat upload ×3; cross-file overlap; edited-file CDC vs fixed; byte-perfect restore; corruption/loss; interrupted upload; reference-count deletion; multiple trials | each present | `test_report_acceptance` (×3); V6 Tests A–G; `test_cdc`; `test_benchmarks` | Implemented |
| FS-25 | §9 | Datasets: low/high redundancy, large edited text file, **a file of tens of MiB**, corruption fixtures; synthetic data labelled as such | seeded generated data (the methodology states they are generated from a seed); no tens-of-MiB benchmark | benchmarking.md | **Documented limitation** (same as SD-31) |
| FS-26 | §10 | Conditional GO: demonstrate deterministic chunking, byte-perfect reconstruction, safe reference handling, transparent benchmark reporting | all four demonstrated | SD-1, SD-4, SD-24, SD-33 | Implemented |
| FS-27 | §10 | Completion evidence: source, schema/migration scripts, test logs, fixture-generation steps, measured tables, short demo (repeat upload, edited-file reuse, restore verification, corruption detection) | source; `schema.sql` (idempotent, version 1, **no migrations exist or were needed**); tests and scripts (logs are re-run, not archived); seeded generators; benchmark tables; `docs/verification/demo-guide.md` | final-verification-report.md | Implemented (test logs not archived) |

## C. Discrepancies and how they are classified

### Resolved by the final acceptance corrections

| # | Was | Now | Evidence |
|---|---|---|---|
| G1 | No free-disk-space check before an upload | **Implemented (advisory).** After the upload is staged and before any chunk is published, free space on the chunk store's volume (the smaller of the chunks and staging directories if they differ) is compared with the worst case: every chunk new (`size`) plus a margin (configurable reserve, default 16 MiB; one maximum-size chunk in flight; about 512 B of SQLite growth per possible chunk). Only if that does not fit is the staged file chunked read-only to count the bytes that are really new, so a duplicate upload is never refused when it needs little or no space. Failure is `507 insufficient_storage`, with staging removed and nothing published. If the OS cannot report free space the check is skipped. It is advice, not a guarantee: a disk that fills later is handled as below. | `test_upload_free_space` |
| G1b | Disk-full errors surfaced as a generic upload failure | `ENOSPC`/`EDQUOT`/Windows disk-full and SQLite "database or disk is full" are recognised anywhere in the error chain and reported as `insufficient_storage` after the normal cleanup: staging removed, chunks this upload created un-published, file and operation marked `failed`, the final transaction rolled back as a unit, the store audits healthy, and a retry works. A staged chunk whose publication fails is now discarded instead of being left in staging until reconciliation. | `test_upload_free_space` |
| P2 | No seconds/MiB | Recorded per operation and summarised (new runs); shown in the UI | `test_benchmark_reporting`, `reporting.test.jsx` |
| P3 | Cache state not a recorded field | Every new result declares `cache_state: "unknown"` and says why; the old claim that the first iteration "may include cold caches" was removed from the methodology because nothing measures it | same |
| P4 | Footprint shown only as components | Aggregate footprint ratio for pipeline benchmarks, with numerator, denominator, components and exclusions stated | same |

Older saved benchmark results were **not** modified, back-filled or reinterpreted: the API serves them byte-for-byte and the UI
labels the missing figures "not recorded in this saved result".

### Documented limitations from the measurement items

| # | Discrepancy | Status |
|---|---|---|
| P1 | No benchmark fixture of "tens of MiB" (benchmarks cap at 4 MiB per file, 48 MiB of work, 240 s). Uploads up to 64 MiB work through the API, but throughput at that size has not been measured. | **Documented limitation.** The cap was kept on purpose: the pure-Python chunker runs at a few MiB/s, so a tens-of-MiB multi-iteration benchmark would exceed the job time limit unless the limits were redesigned. No value is claimed for that size. |
| P5 | The aggregate footprint ratio exists for pipeline benchmarks only; the CDC-vs-fixed comparison reports payload bytes for both schemes because it has no database. | **Documented limitation** (FS-20 is *partially implemented*). |

### Acceptable prototype limitations and optional items

| # | Discrepancy | Classification |
|---|---|---|
| A1 | No CLI; HTTP API + web UI instead | Acceptable — SD §3 says "CLI **or** optional HTTP interface". |
| A2 | Capacity model is a storage/metadata/growth projection, not a queue/latency (P95/P99) simulator; "Breakscale or equivalent" tool not used | Acceptable — SD §8 marks simulation **optional**; SD §10 itself says it cannot validate deduplication. The UI says "Mathematical model — not a measurement or load test". |
| A3 | Deleting a file removes now-unreferenced chunk files immediately after the delete transaction commits, rather than through a separate later GC pass (SD §6, FS §5.2, DG "Garbage Collection (ref_count = 0)") | Acceptable — stricter in timing only: removal still requires zero references confirmed inside the committed transaction; shared chunks are never touched (`test_hardening`, V6 Test E). Reconciliation still removes leftovers and unreferenced rows on demand. |
| A4 | Authentication, tenant isolation, replication, HA, load balancer, sharded index, cloud object store | Out of scope in both reports; not implemented, not claimed. |
| A5 | Power-loss durability not tested | Stated limit in both reports (FS §5.1). |
| A6 | No continuous background maintenance or production monitoring | Out of scope in FS §3; maintenance is on-demand by design. |

### Naming and layout differences (no behavioural effect)

| # | Reports / diagram | Implementation |
|---|---|---|
| N1 | `files.name`, `files.file_size`, `file_chunks.position` | `filename`, `original_size`, `chunk_index`; extra columns `chunk_size`, `created_at`, and an `operations` table. |
| N2 | DG endpoints `POST /upload`, `GET /file/{id}`, `GET /metadata/{id}`, `DELETE /file/{id}` | `POST /api/files`, `GET /api/files/{id}/download`, `GET /api/files/{id}`, `DELETE /api/files/{id}`; the API adds audit, reconcile, benchmarks, simulations, inspection and fault-lab endpoints. The diagram marks the API layer "(Optional)". |
| N3 | DG chunk directory `chunks/ab/cd/<hash>` (two levels) | `chunks/ab/<hash>` (one level, `storage/filesystem.py`). The reports only say "hash-derived paths". Not changed: it would invalidate existing stores. |
| N4 | DG "Monitoring & Logs" (upload/download logs, statistics, error logs) | `operations` table (browsable in the Database Explorer), `GET /api/storage/stats`, structured error envelope and API error logging. There is no log file or log viewer. |

## D. Architecture diagram comparison

| Diagram element | Present? | Notes |
|---|---|---|
| 1. Client / Interface (CLI or Web API); optional API layer | yes (Web API + UI; no CLI) | N2 |
| 2A CDC (min 4 KiB, avg 16 KiB, max 128 KiB) | yes | exact values |
| 2B Hashing (SHA-256 per chunk) | yes | also whole-file hash |
| 2C Deduplication manager (lookup, reuse, store new) | yes | also re-verifies an existing chunk before trusting it |
| Metadata manager (SQLite) with chunks / files / file_chunks tables | yes | N1 |
| Chunk storage (file named by SHA-256) | yes | N3 |
| 3. Read path: metadata lookup → chunk retrieval → integrity verification → reconstruction → byte-identical file | yes | exactly this order |
| 4. Maintenance: integrity check, garbage collection (ref_count = 0), monitoring and logs | yes / yes (within delete + reconcile) / partial | A3, N4 |

## E. What changed in the final acceptance corrections

- Backend: `core/diskfull.py` (new), `deduplication/upload.py` (preflight, disk-full translation, discard of a staged chunk whose
  publication fails), `deduplication/interfaces.py` (`InsufficientStorageError`, `507`), `storage/filesystem.py` (`free_bytes`),
  `core/config.py` (`free_space_reserve_bytes`, env `CHUNKVAULT_FREE_SPACE_RESERVE_BYTES`), `benchmarking/runner.py`
  (seconds/MiB, footprint figures, cache state).
- Unchanged: CDC, chunk identity, the SQLite schema and the storage protocol (stage, fsync, atomic rename, one metadata commit).
- Frontend: Performance results show the new figures, or "not recorded in this saved result".
- Tests added: `test_upload_free_space.py` (23), `test_benchmark_reporting.py` (20), `reporting.test.jsx` (6), and earlier
  `test_report_acceptance.py` (3).
