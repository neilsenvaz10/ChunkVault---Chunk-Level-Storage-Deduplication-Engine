# Low-Level Design

Each component has an interface in `backend/app/<package>/interfaces.py` (a `typing.Protocol`
plus its exceptions). Phase 2 implemented components 1-8 (see each heading); benchmarking and simulation remain interface-only. All errors derive from
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

## 9. Benchmark Runner — `benchmarking/interfaces.py` (Phase 6)
- **Responsibility:** measure throughput and dedup ratio on an isolated store with reproducible (seeded) data.
- **Inputs:** `BenchmarkSpec`. **Outputs:** `BenchmarkResult` + JSON in `data/benchmarks/`.
- **Dependencies:** upload orchestrator. **Exceptions:** `BenchmarkError`.
- **Failure handling:** never touches the user's real store; writes no result file on failure.

## 10. Capacity Simulation Engine — `simulation/interfaces.py` (Phase 7)
- **Responsibility:** analytical projection of physical size and metadata overhead from stated assumptions.
- **Inputs:** `SimulationParameters`. **Outputs:** `SimulationResult` (labelled model output).
- **Dependencies:** none (pure). **Exceptions:** `SimulationError`.
- **Failure handling:** rejects invalid parameters; touches no files or database.
