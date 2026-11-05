# Consistency Protocol: filesystem + SQLite

## The problem

**A SQLite transaction cannot atomically commit filesystem writes.** `COMMIT` makes metadata
durable; it knows nothing about files. A crash can leave (a) chunk bytes on disk with no
metadata, or (b) metadata pointing at bytes that never reached disk. The protocol below makes
every crash state detectable and repairable, and guarantees a `complete` file is always
restorable. *(Status: implemented in Phase 2: `deduplication/upload.py`, `deduplication/deletion.py`, `reliability/`.)*

## Rules

1. **Bytes before references.** Chunk bytes are durable and published *before* any metadata says they exist as `stored`.
2. **Atomic publication.** Chunks are written to `data/staging/` (unique temp name), `fsync`ed, then moved with an atomic rename (`os.replace`) to `data/chunks/ab/<sha256>`. Staging and chunks are on the same volume. A reader never sees a partial chunk.
3. **Metadata commits last, in one transaction.** `file_chunks` rows, `chunks.state='stored'` and `files.status='complete'` are committed together in a single SQLite transaction.
4. **Idempotence.** Publishing an existing hash is a no-op; same hash ⇒ same bytes.
5. **Deletion order is reversed:** metadata first (`ref_count → 0` committed), then delete files; leftovers are orphans, never dangling references.

## Upload protocol

0. Take the single-writer lock (thread lock + OS file lock on `data/writer.lock`).
1. Stream the upload into `staging/upload-<id>.part`, enforcing the size limit and computing the whole-file SHA-256; `fsync`.
2. T1: insert `files(status='uploading')` and `operations(upload, running)`.
3. For each chunk (CDC → SHA-256) the dedup manager consults `chunks` **and re-hashes the stored bytes**: `DUPLICATE` (row `stored`, bytes verified) → nothing to write; `NEW` (no row, no bytes) → stage, `fsync`, atomically publish; `ADOPT` (no row but valid bytes left by an earlier interrupted upload) → reuse the bytes. A known chunk that is missing/corrupt on disk, or an unreferenced file whose bytes do not match its name, **fails the upload** (`chunk_unavailable` / `chunk_corrupt`): ordinary uploads never overwrite or repair. No chunk rows are written yet, so `pending` rows are never created by the engine.
4. Verify every chunk the manifest needs exists.
5. T2 (single commit): insert the new chunk rows as `stored`, insert all `file_chunks` rows (triggers raise `ref_count`; repeated chunks count once per row), set `files.sha256` and `status='complete'`, finish the operation `succeeded`.
6. Remove the staged upload.

On any `Exception` before the T2 commit: T2 rolls back as a unit, the file and operation are marked `failed`, staging is cleaned and chunks *this upload* created that no row references are removed again. A hard crash (modelled in tests by a `BaseException`) skips all of that and leaves the rows below for the audit/reconciliation. Retrying an interrupted upload restarts it from the beginning and reuses valid published chunks (`ADOPT`); there is no resumable upload.

## Crash analysis

| Crash point | State left | Recovery (`reconcile`) |
|---|---|---|
| While receiving the upload (before T1) | staging `.part` file only | delete staging file |
| After T1, before/while publishing | file `uploading`, op `running`, `.part` + temp chunk files, some published chunk files with no row | fail the file, abort the op, delete staging files and orphan chunk files |
| During T2 | SQLite rolls back atomically; all bytes published, none referenced | same as previous row |
| After T2, before staging cleanup | consistent; stray staging file | delete staging file |
| Delete: after metadata commit, before unlinking bytes | file/chunk rows gone, chunk files remain | orphan chunk files deleted |
| Chunk file lost or damaged after commit | row `stored`, bytes missing/corrupt | **reported only**: needs a human (delete the damaged file's record and re-upload) |

## Detection vs repair

**Audit** (`POST /api/integrity/audit`, `reliability/auditor.py`) is strictly read-only (query-only connection, no file writes). It reports: missing/corrupt chunks (naming affected files), orphan chunk files, unreferenced chunk rows, `pending` rows, stale staging files, ref-count mismatches, manifest gaps / size mismatches, whole-file hash mismatch (single-file audit), files stuck in `uploading`, stuck operations. "Stale" means older than `CHUNKVAULT_STAGING_STALE_SECONDS` (default 300) so in-flight work is not misreported.

**Reconcile** (`POST /api/integrity/reconcile`, explicit only, never automatic at startup) runs under the writer lock and reports each action: delete leftover staging files (restore temp files only once stale); mark `running` operations `aborted`; mark `uploading` files `failed`; delete chunk rows with `ref_count = 0` (SQL re-checks the count), then their bytes; delete chunk files with no row. It never deletes a chunk with `ref_count > 0` and never rewrites missing/corrupt chunks or ref counts: those remain in the `remaining` audit.

## Deletion

One transaction deletes the file row (cascade + triggers lower ref counts), deletes chunk rows that reached 0 and records the operation. Only after COMMIT are the chunk files unlinked. A repeated delete returns 404 `file_not_found` and changes nothing.

## Concurrency

Single process, sequential processing. Upload, delete and reconcile each run under the writer lock (in-process lock + OS file lock on `data/writer.lock`), so even a second server process cannot interleave mutations; a caller that cannot get the lock within `CHUNKVAULT_WRITER_LOCK_TIMEOUT_SECONDS` receives `409 writer_busy`. Run uvicorn with one worker. Restores and audits are read-only and do not take the lock. SQLite uses WAL, `synchronous=FULL`, `BEGIN IMMEDIATE` writers and a busy timeout.

## Durability assumptions

Chunk bytes are `fsync`ed before the atomic rename; metadata commits use `synchronous=FULL`. On POSIX the chunk directory is also `fsync`ed after the rename; Windows has no directory fsync, so a power loss right after a rename may lose the chunk file, surfacing later as a *missing chunk* in the audit, never as silent corruption. Staging and chunk directories must be on one filesystem (checked at startup). Nothing here protects against disk failure or bit rot beyond detection.

## Free space and disk-full failures

Before any chunk is published, the upload checks (advisory only) that the chunk store's volume has room for the worst case: every chunk new, plus a margin (`CHUNKVAULT_FREE_SPACE_RESERVE_BYTES`, one maximum-size chunk in flight, and an allowance for SQLite growth). If that does not fit, the staged file is chunked read-only to count the bytes that are really new, so a duplicate upload that needs little or no space is not refused. Insufficient space gives `507 insufficient_storage` with staging removed and nothing published.

The check cannot stop another process from filling the disk, so disk-full errors at any later step (staging, chunk staging or publication, SQLite writes) are recognised and reported as `insufficient_storage` after the normal cleanup: staging removed, chunks this upload created un-published, the file and operation marked `failed`, the final transaction rolled back as one unit. The protocol itself (stage, fsync, atomic rename, one metadata commit) is unchanged.
