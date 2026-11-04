# Low-Level Design

Each component has an interface in `backend/app/<package>/interfaces.py` (a `typing.Protocol`
plus its exceptions). Components 1-8 were implemented in Phase 2, 9-11 in Phase 5. All errors derive from
`app.core.errors.ChunkVaultError`, which the API layer turns into
`{"error": {"code", "message", "details"}}`.

## 1. Upload Orchestrator — `deduplication/upload.py` (implemented, Phase 2)
- **Responsibility:** run `stage → CDC → SHA-256 → dedup → store → metadata commit` for one file.
- **Inputs:** filename, binary stream. **Outputs:** `UploadResult` (file id, whole-file sha256, chunk/new/duplicate counts, bytes written).
- **Dependencies:** Chunker, HashingService, DeduplicationManager, MetadataRepository, ChunkStore.
- **Exceptions:** `EmptyFilenameError`, `ChunkIdentityConflictError`, `UploadError`.
- **Failure handling:** operation → `failed`, file → `failed`, staging files removed, published-but-unreferenced chunks left for reconciliation. A file is `complete` only after every chunk is `stored` and mapped.

## 2. Rabin-style CDC Engine — `chunking/rabin.py` (implemented, Phase 2)
- **Responsibility:** yield chunks whose boundaries depend on content ([spec](cdc-specification.md)).
- **Inputs:** readable binary stream; `CDCConfig`. **Outputs:** ordered `Chunk(index, offset, data)`; concatenation equals input; empty input → no chunks.
- **Dependencies:** `CDCConfig` only.
- **Exceptions:** `ChunkingError` (stream read failure); invalid config raises `ConfigurationError` at startup.
- **Failure handling:** keeps no state; caller discards consumed chunks. Memory bounded by max chunk size.

## 3. SHA-256 Hashing Service — `hashing/sha256.py` (implemented, Phase 2)
- **Responsibility:** lowercase-hex SHA-256 of chunks; streaming digest of whole files; verification.
- **Inputs:** bytes / expected digest. **Outputs:** hex string / bool.
- **Dependencies:** none. **Exceptions:** `InvalidDigestError` for a malformed *expected* digest.
- **Failure handling:** pure; a mismatch is `False`, not an exception.

## 4. Deduplication Manager — `deduplication/manager.py` (implemented, Phase 2)
- **Responsibility:** decide `NEW` / `DUPLICATE` / `REPAIR` for a chunk from metadata (`chunks.state`), never from file existence alone.
- **Inputs:** hash, size. **Outputs:** `ChunkDecision`.
- **Dependencies:** MetadataRepository.
- **Exceptions:** `ChunkIdentityConflictError` (same hash, different size ⇒ treated as corruption, never as a duplicate).
- **Failure handling:** read-only decision; no side effects on failure. Repeats within one file become `DUPLICATE` after the first is registered but still get their own `file_chunks` row.

## 5. SQLite Metadata Repository — `database/repository.py`, `database/connection.py` (connection/schema: Phase 1; `sqlite_repository.py`: Phase 2)
- **Responsibility:** all metadata persistence; enforces the schema invariants ([schema](../database/schema.md)).
- **Inputs/Outputs:** connection + typed records (`FileRecord`, `ChunkRecord`, …).
- **Dependencies:** SQLite. **Exceptions:** `RepositoryError`, `FileNotFoundInMetadata`, `ChunkSizeConflict`, `DatabaseError`.
- **Failure handling:** never commits by itself; callers wrap work in `transaction()`, which rolls back on any exception.

## 6. Content-Addressed Chunk Store — `storage/filesystem.py` (implemented, Phase 2)
- **Responsibility:** `stage` (write + fsync to staging), `publish` (atomic rename to `chunks/ab/<hash>`), `read`, `delete`, enumerate.
- **Inputs:** hash + bytes. **Outputs:** `StagedChunk`, bool, bytes.
- **Dependencies:** filesystem (same volume for staging and chunks so rename is atomic).
- **Exceptions:** `ChunkWriteError`, `ChunkNotFoundError`, `ChunkStoreError`.
- **Failure handling:** failed stage removes its partial file; `publish` is idempotent; `delete` is idempotent and only legal at `ref_count == 0`.

## 7. Restoration Engine — `restoration/engine.py` (implemented, Phase 2)
- **Responsibility:** stream a file's bytes in `chunk_index` order, hash-verifying each chunk and the whole file.
- **Inputs:** file id, destination path. **Outputs:** `RestoreResult`; the file is reassembled into a temporary file and served only after every chunk and the whole file verified (a streaming iterator could not guarantee that).
- **Dependencies:** repository, chunk store, hashing.
- **Exceptions:** `FileNotRestorableError`, `MissingChunkError`, `ChunkCorruptionError`, `FileHashMismatchError`.
- **Failure handling:** pre-flight failures happen before any byte is yielded; mid-stream failures abort the stream — wrong data is never silently returned.

## 8. Integrity Auditor and Reconciler — `reliability/auditor.py`, `reliability/reconciler.py` (implemented, Phase 2)
- **Responsibility:** read-only `verify_file`, `verify_store`; separate, explicitly invoked `reconcile` ([protocol](consistency-protocol.md)).
- **Outputs:** `AuditReport` of `Finding`s (missing/corrupt/orphan chunk files, orphan staging files, ref-count mismatches, file hash mismatch, stuck operations).
- **Dependencies:** repository, chunk store, hashing. **Exceptions:** `AuditError` (audit could not run; damage itself is reported as findings).
- **Failure handling:** `reconcile` is idempotent and never deletes a chunk with `ref_count > 0`.

## 9. Benchmark Runner — `benchmarking/{interfaces,datasets,profiler,runner,worker,jobs}.py` (implemented, Phase 5)
- **Responsibility:** measure upload/restore latency, stage timings (exclusive, non-overlapping), throughput, dedup ratio and a CDC-vs-fixed comparison on an isolated store with reproducible (seeded) data. Runs in a separate worker process, one job at a time, bounded and cancellable.
- **Inputs:** `BenchmarkRequest {kind, workload, size_bytes, iterations, seed}` (validated by `validate_spec`). **Outputs:** a result JSON saved under `data/benchmarks/` with parameters, environment and methodology.
- **Dependencies:** the real uploader/restorer/chunk store on a temporary store. **Exceptions:** `InvalidBenchmarkSpec` (422), `BenchmarkError`; a job can end `failed`/`cancelled`.
- **Failure handling:** never touches the application's store; failures are reported with path-scrubbed messages; the temporary store is removed (also on cancel/timeout).
- **API:** `POST/GET /api/benchmarks`, `GET /api/benchmarks/{id}`, `POST /api/benchmarks/{id}/cancel`, `GET /api/benchmarks/workloads`.

## 10. Capacity Simulation Engine — `simulation/model.py` (implemented, Phase 5)
- **Responsibility:** mathematical projection of logical/physical storage, metadata overhead, growth and retention from stated assumptions. Not a measurement or load test.
- **Inputs:** `SimulationRequest` (files, average size, chunks per file, duplicate %, metadata sizes, growth, retention, horizon). **Outputs:** outputs, formulas, assumptions, sweeps and a growth series.
- **Dependencies:** none (pure function). **Exceptions:** invalid parameters are rejected by request validation (422).
- **Failure handling:** touches no files or database. **API:** `POST /api/simulations`.

## 11. Fault Injection Lab — `reliability/faultlab.py` (implemented, Phase 5; disabled by default)
- **Responsibility:** run one of nine built-in failure scenarios against the real upload/restore/delete/recovery code in a private temporary store, then audit, recover (explicit reconcile, twice) and verify invariants by restoring files.
- **Inputs:** a scenario id only. **Outputs:** steps, expected-vs-observed, SQLite/filesystem state, recovery actions, invariants and a backend-computed `recovery_status`.
- **Dependencies:** the real services (built on the temporary store), `Uploader.checkpoint` seam. **Exceptions:** `FaultLabDisabledError` (403), `UnknownScenarioError` (422), `ConflictError` (409).
- **Failure handling:** the temporary store is always removed; refuses to run if it could overlap application data. **API:** `GET /api/reliability/scenarios`, `POST /api/reliability/experiments`.
