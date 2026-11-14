# C4 — cloud workflow integration: how it works

Reference for `CHUNKVAULT_BACKEND=cloud`. The local backend (SQLite + filesystem) is unchanged. Moved into `docs/` in Phase E.

## 1. One switch, never a mixed backend
`build_services` selects **PostgreSQL + Supabase Storage together** (`cloud`) or **SQLite + filesystem** (`local`) and refuses any other
pairing. In cloud mode there is no local chunk directory and no local database; the only local files are *scratch*: the upload being
received (`staging/upload-*.part`) and a restored file being sent (`staging/restore-*.tmp`). The fault lab, the benchmarks and the
capacity simulation always build their own **local** temporary stores and never receive the cloud database or bucket.

## 2. Upload (metadata-last)
1. Stream to local scratch (size limit enforced), whole-file SHA-256.
2. **Quota preflight** (below). Nothing has been written anywhere yet.
3. T1: `files(status='uploading')` + `operations(upload, running)`.
4. Chunk phase, in batches of `batch_chunks` new distinct hashes (default 64):
   one metadata lookup per batch; duplicates verified per `verify_on_reuse` (`exists` = size from one object listing, `full` = download +
   SHA-256, `none` = trust the row); quota re-checked; new rows registered **`pending`**; new chunks published **in parallel
   (`io_workers`, default 4) and the batch waits for all of them** before anything else runs.
5. T2 (one transaction, writer lock re-verified immediately before COMMIT): rows -> `stored`, whole manifest in one statement
   (triggers count every reference), file -> `complete`, operation -> `succeeded`.

Any failure before T2 commits leaves the file `failed`, no manifest, no counts. **Nothing is deleted from the object store as cleanup**:
objects the upload created keep their `pending` rows (counted by the quota) until explicit garbage collection. A retried upload finds
those rows/objects, verifies them and completes (it does not double count).

Ambiguous outcomes (timeout/5xx/dropped connection during an object upload): retried with backoff (safe because uploads never
overwrite); the existing object is then downloaded and hash-verified; if it cannot be established the upload fails with
`storage_unavailable` / `details.outcome = "unknown"` and **no file metadata is committed**.

## 3. Quota accounting model (application level; not Supabase's project limits)
`used = SUM(chunks.size_bytes)` over **every** chunk row — referenced, `pending` (failed/in-flight uploads) and unreferenced rows
awaiting GC. Unique bytes only: a duplicate chunk never counts twice. Default cap 300 MiB (`CHUNKVAULT_STORED_BYTES_CAP`).
* Preflight: if `used + upload_size <= cap` nothing more is needed; otherwise the staged file is chunked read-only to count the bytes
  that are really new, so a duplicate upload near the cap is never refused. A refusal happens before any file row or chunk exists.
* Per batch (defence): `used + new_bytes > cap` aborts the batch.
* Boundaries are inclusive: `used == cap` is allowed, one more unique byte is refused (`507 storage_quota_exceeded`, with
  `quota_bytes / used_bytes / required_new_bytes / available_bytes`).
* Concurrency: uploads hold the writer lock, so two uploads cannot both pass the check.
* Space returns only after **delete + explicit GC**. `GET /api/storage/quota` shows used / available / referenced / reclaimable.
* Objects that exist without a row (only possible after a failed GC delete, which re-registers the row, or manual interference) are
  not counted; the admin audit lists them as `orphan_chunk_file`.

## 4. Delete and garbage collection
`DELETE /api/files/{id}` removes the file row and manifest (the FK cascade + triggers lower the reference counts) and **nothing else**.
Chunk rows (even at `ref_count = 0`) and objects stay. Response adds `chunks_pending_gc`; `chunks_reclaimed` is always 0 in cloud mode.

`POST /api/admin/gc` (admin token; body `{dry_run: true, limit: 200}`; `limit` 1-1000) — **durable three-step protocol (migration 0002)**:
1. **CLAIM** (one short transaction): `UPDATE chunks SET state='deleting'` for rows with `ref_count = 0`, no manifest row, and older than
   `orphan_min_age_seconds` (default 900 s). Rows still `deleting` from an interrupted run are claimed again first. The guard is
   re-evaluated after any row-lock wait.
2. **DELETE** the claimed objects from Storage (remote, in batches of 50, outside any transaction; the writer lock is re-verified before every batch).
   A failed delete leaves the row `deleting` (still counted by the quota; the next run retries).
3. **RELEASE** (one short transaction per batch): delete the `deleting` rows whose object is gone (re-checks they are still unreferenced).

The metadata row is therefore **never removed before its object** (the old "row first, object second" interval is gone), and a `deleting`
row exists for the whole time its object might be missing. Dry run is the default; never automatic; recorded as a `reconciliation` operation.

**Why no upload can be hurt, with or without the writer lock.** An upload that writes a manifest first *row-locks* (`FOR SHARE`) every chunk row
it references inside the same transaction and aborts unless all of them are `pending`/`stored`; it also refuses rows it meets in state
`deleting`. A claim is an UPDATE of the same rows. So: (a) manifest first -> the claim waits, then sees `ref_count > 0` and skips the row;
(b) claim first -> the upload's lock query no longer returns that row and it aborts (`409 chunk_reclaiming`) before committing anything.
There is no interleaving in which a committed file references a chunk whose row is `deleting` or whose object a claim has deleted.

## 5. Roles (cloud only; the local backend has no authentication)
| | public | demo token | admin token |
|---|---|---|---|
| health, system, files list/detail, chunks, stats, quota, inspect, benchmark/scenario reads | yes | yes | yes |
| upload, delete, download/restore, simulations | – | yes | yes |
| integrity audit, reconcile, GC, start/cancel benchmarks, fault lab | – | – | yes |

`Authorization: Bearer <token>`; constant-time comparison; 401 `unauthorized` (missing/invalid), 403 `forbidden` (demo on an admin route).
Tokens, the service key and database credentials live only in the backend environment: never in responses, errors, logs or the frontend.
Not implemented here (Phase D/E): request throttling.

## 6. Stats, audit, inspect — no bucket scans on request paths
* `/api/storage/stats`, `/api/inspect/*`, `/api/chunks`, `/api/files`, `/api/storage/quota`, `/api/health`: metadata only; **zero storage
  requests**. In cloud mode `chunk_files`/`chunk_store_bytes` are the recorded `stored` rows.
* Whole-store audit (admin): a short read-only snapshot gathers metadata and ends; then one object inventory (≤ 256 listing requests,
  `io_workers` at a time) checks presence/size of every stored chunk and finds orphan objects; a random sample of
  `audit_deep_chunks` (default 25) chunks is downloaded and hash-verified. The report's `notes` say exactly how much was verified.
  No PostgreSQL transaction is open during any storage request (tested).
* Per-file audit and restore download chunks with bounded read-ahead and verify every one.

## 7. The writer lock: lifetime, and what does NOT depend on it
Session advisory lock on a dedicated connection (`app/database/postgres.py`), taken **first** and released **last**:

| step | upload | GC |
|---|---|---|
| lock acquired | before the request body is staged | before the claim |
| quota | preflight under the lock, then **re-evaluated inside the registering transaction** | – |
| chunk rows registered / objects published | under the lock | – |
| claim / object deletes / release | – | under the lock; re-verified before each batch |
| metadata commit (T2) | under the lock; re-verified immediately before COMMIT | – |
| lock released | after commit or after failure cleanup (`finally`) | after the run (`finally`) |

The lock therefore covers remote Storage operations. It is a *serialisation aid*, not the safety mechanism. If its session dies (network drop,
server restart, `pg_terminate_backend`) the server releases it while the holder keeps running; the holder notices at the next `ensure_held()`
(before COMMIT / before each GC batch) and stops. Safety no longer depends on noticing, because every invariant is enforced by the database:

* **Quota**: `reserve_chunks` takes a transaction-scoped advisory lock, recomputes usage from committed rows and registers the new rows in the
  same transaction. Two reservations can never both pass, lock or no lock. Tested with the lock disabled and overlapping uploads.
* **GC vs uploads**: the claim/fence protocol above (row locks + the durable `deleting` state).
* **Counters and references**: foreign keys (`RESTRICT` on chunk rows), triggers, CHECK constraints, immutable manifest rows.
* **File completion**: `complete_file` only completes a file that is still `uploading`, so reconciliation marking a live upload failed
  cannot be overwritten by a late commit (the upload aborts and rolls back).
* **Objects** are immutable and content-addressed; uploads never overwrite.

**Reconciliation and stale records (cloud).** `POST /api/integrity/reconcile` only fails an `uploading` file or aborts a `running` operation
that is older than `orphan_min_age_seconds` (900 s); younger ones may be live work. It never touches chunk rows or objects (that is GC).
`pending` rows are collected only after the same grace window, and an upload that still depends on a claimed row aborts instead of dangling.

**Remaining limitations (honest list).**
1. The lock is taken *before* the request body is staged, so a very slow client holds the single-writer lock while it transfers; other
   writers wait up to `writer_lock_timeout_seconds` and then get `409 writer_busy`. (Bounded by the body limit; staging before locking is a
   possible later improvement.)
2. A lost lock can still produce *wasted work* (two uploads publishing the same new chunk, a GC and an upload aborting each other, an unfinished
   `uploading` file / `running` bookkeeping row). It cannot produce a dangling reference or exceed the quota.
3. A GC cut short leaves rows in `deleting`; uploads of that content get `409 chunk_reclaiming` until an administrator runs GC again.
4. Reconciliation's grace window equals the GC grace window; a legitimate upload lasting longer than 15 minutes could be failed by an
   administrator's reconcile (it then aborts safely and can be retried).

## C5E-2 — upload reliability and security (supersedes limitation 1 above)

**Upload phases (cloud).** (1) stage the body and chunk+hash it into a plan, *without* the writer lock; (2) advisory quota pre-flight from the
plan; (3) take the writer lock, waiting at most min(`writer_lock_timeout_seconds`, time left in the deadline); (4) commit: T1 → one duplicate
lookup + prefix-grouped `check_many` verification → reserve → publish new chunks → T2 (fenced). The lock is held for the commit only; the CPU
and the client transfer no longer hold it. Safety still rests on the database fencing, not on the lock.

**Deadline** (`CHUNKVAULT_UPLOAD_DEADLINE_SECONDS`, 90 s, 504 `upload_timeout`) is checked between steps, never inside a transaction, so
overshoot is bounded by one batch / one Storage timeout.

**Failure policy (revises C4: failed uploads used to leave their pending rows for GC).** A failed or timed-out upload gives back only the
reservations it created itself: rows still `pending`, ref_count 0, in no manifest are claimed (`deleting`), their objects deleted, rows released,
within `CHUNKVAULT_UPLOAD_CLEANUP_BUDGET_SECONDS`. If Storage cannot delete in time the rows stay durable `deleting` (still counted in the quota)
and the administrator's GC finishes them. Rows of other uploads are never touched; a process death leaves `pending` rows exactly as before.

**Limits** (in memory, one process): auth-failure lockout per address (only invalid presented tokens count, success never resets), expensive-request
rate, concurrency gates for uploads / restores / maintenance (over the limit → immediate 429 + `Retry-After`). Client address: `X-Forwarded-For`
is believed only when the direct peer is inside the explicitly configured `CHUNKVAULT_TRUSTED_PROXY_CIDRS` (default: none, so the header is
ignored); then the rightmost-N entry (`CHUNKVAULT_TRUSTED_PROXY_HOPS`) is used. A private or non-public peer is NOT trusted by itself.

**Start-up with the database down.** The process starts, `/api/health/live` is 200, `/api/health` is 503 `degraded`, database routes answer
503 `database_unavailable` at once, and readiness re-checks (single-flight, one probe per `CHUNKVAULT_STARTUP_RETRY_SECONDS`) and recovers by
itself. No migration ever runs at start-up; a wrong schema version still stops the process.

**C5E-2A additions.** (1) A cleanup's Storage delete that is still running when the cleanup budget ends keeps its row `deleting`:
`SupabaseChunkStore.delete_in_flight(hash)` is consulted by GC, which then leaves the row alone, so a late delete cannot hit an object
that was published again after the row was gone. (2) Proxy trust is explicit (above); on Render the CIDRs are unset until verified.
