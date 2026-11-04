# Consistency Protocol: filesystem + SQLite

## The problem

**A SQLite transaction cannot atomically commit filesystem writes.** `COMMIT` makes metadata
durable; it knows nothing about files. A crash can leave (a) chunk bytes on disk with no
metadata, or (b) metadata pointing at bytes that never reached disk. The protocol below makes
every crash state detectable and repairable, and guarantees a `complete` file is always
restorable. *(Status: design for Phases 3 and 5; nothing here is implemented yet.)*

## Rules

1. **Bytes before references.** Chunk bytes are durable and published *before* any metadata says they exist as `stored`.
2. **Atomic publication.** Chunks are written to `data/staging/` (unique temp name), `fsync`ed, then moved with an atomic rename (`os.replace`) to `data/chunks/ab/cd/<sha256>`. Staging and chunks are on the same volume. A reader never sees a partial chunk.
3. **Metadata commits last, in one transaction.** `file_chunks` rows, `chunks.state='stored'` and `files.status='complete'` are committed together in a single SQLite transaction.
4. **Idempotence.** Publishing an existing hash is a no-op; same hash ⇒ same bytes.
5. **Deletion order is reversed:** metadata first (`ref_count → 0` committed), then delete files; leftovers are orphans, never dangling references.

## Upload protocol

1. Insert `operations(upload, running)` and `files(status='uploading')` — transaction T1.
2. Stream the upload to a file in `staging/`.
3. For each chunk (CDC → SHA-256): the dedup manager consults `chunks`. `NEW` → insert row `state='pending'`; stage + publish bytes. `DUPLICATE` → nothing to write. `REPAIR` → rewrite bytes. (Row inserts for a batch of chunks use short transactions.)
4. Transaction T2 (single commit): mark chunks `stored`, insert all `file_chunks` rows (triggers raise `ref_count`; repeated chunks count once per row), set `files.sha256` and `status='complete'`, finish operation `succeeded`.
5. Remove the staged upload.

## Crash analysis

| Crash point | State left | Recovery (`reconcile`) |
|---|---|---|
| During step 2 | file `uploading`, staging file | mark file `failed`, delete staging file |
| After bytes published, before T2 | chunk files, chunk rows `pending`/no row, file `uploading` | fail the file; chunk files with `ref_count = 0` deleted; `pending` rows with no bytes dropped |
| During T2 | SQLite rolls back atomically | same as previous row |
| After T2, before staging cleanup | consistent; stray staging file | delete staging file |
| Partial delete | file row gone, some chunk files remain | orphan chunk files (`ref_count = 0` or no row) deleted |

## Reconciliation (run at startup and on demand)

- Delete leftover `staging/` files.
- Mark `operations` still `running` as `aborted`; mark `uploading` files `failed`.
- Chunk files with no row, or row `ref_count = 0` → delete (never if `ref_count > 0`).
- Rows `stored` whose file is missing, or whose bytes don't hash to the name → state `corrupt`, reported; affected files are not restorable until re-uploaded.
- `v_ref_count_mismatch` non-empty → report (the invariant is trigger-maintained, so a mismatch means external tampering).

## Concurrency

Single process, sequential processing. SQLite is in WAL mode with `BEGIN IMMEDIATE` writers and
a busy timeout. Two uploads of the same new chunk are serialised by the write lock; the second
sees the chunk and treats it as `DUPLICATE` (or `REPAIR` if still `pending`).
