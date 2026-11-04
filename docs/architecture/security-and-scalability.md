# Security and Scalability Limitations

## Security posture (prototype)

| Topic | Current behaviour |
|---|---|
| Authentication / authorization | **None.** Anyone who can reach the API can read, upload and delete. Intended for localhost use only. |
| CORS | Explicit allow-list of development origins; `*` is rejected at startup. |
| Input validation | Pydantic schemas; file ids and digests are validated by pattern before use; table names for inspection come from a whitelist enum. |
| Path traversal | Chunk paths are derived only from a validated 64-hex digest; uploaded filenames are reduced to their last path component and never used as storage paths. |
| Request size | A body-limit middleware rejects oversize requests before multipart parsing (413), by declared length and by bytes actually received. |
| Information disclosure | Responses never include filesystem paths or stack traces; unexpected errors return a generic `internal_error`. |
| SQL | Parameterised queries only. No endpoint accepts SQL, filters or expressions. |
| Content | Files are stored and returned as opaque bytes (`application/octet-stream`, attachment). No scanning or encryption at rest. |
| Denial of service | Upload size and request body are bounded; there is no rate limiting. A single-writer lock means one slow upload delays other writers (`409 writer_busy` after a timeout). |

## Scalability limitations (stated, not solved)

- **Single writer.** All mutations are serialised by one lock; throughput cannot scale with more workers.
- **Single machine, single disk.** No replication, no redundancy. Detection of corruption does not imply recovery: damaged chunks cannot be repaired.
- **SQLite.** One database file; suitable for the prototype's data volume, not for billions of chunks.
- **One file per chunk.** Many small files; directory and inode overhead grows with chunk count.
- **Pure-Python chunker.** Throughput is a few MB/s (Phase 6 will measure it); the upload limit exists for this reason.
- **Whole-file buffering after multipart parsing.** A large upload is received into a temporary file, then copied to staging; temporary disk use is up to about twice the upload size for the duration of the request.
- **No resumable uploads.** A retry restarts the upload and reuses already-published valid chunks.
- **No garbage-collection scheduling.** Reclamation happens on delete; leftovers require an explicit reconcile.

A distributed design (sharded metadata, replicated object storage, worker pools) is discussed only
conceptually on the Architecture Trade-offs page. ChunkVault does not implement or claim it.
