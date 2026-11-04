// Educational stage descriptions for the upload and restore lifecycles. They describe how the
// implemented code works (backend/app/deduplication/upload.py, restoration/engine.py); they are
// NOT a recorded execution trace. `facts` names which real values the UI may show for a selected file.

export const UPLOAD_STAGES = [
  {
    id: 'select', title: 'Client selects file', component: 'React frontend (browser)', facts: ['file'],
    what: 'The user picks or drops a file. The browser knows its name and size, and the server limit (from /api/health) so oversized files are refused before sending.',
    why: 'Cheap early validation saves a pointless transfer and gives a clear message.',
    data: 'Nothing is stored; the File object exists only in the browser.',
    failure: 'File too large or no file chosen: the Upload button stays disabled.',
  },
  {
    id: 'send', title: 'React sends multipart request', component: 'React frontend → FastAPI', facts: ['file'],
    what: 'POST /api/files with the file as multipart form data. Transfer progress is measurable in the browser; processing progress on the server is not reported.',
    why: 'Multipart is the standard way to stream file bytes plus a filename over HTTP.',
    data: 'Bytes in flight; the server begins receiving the body.',
    failure: 'Network failure: the request is not retried automatically (a retry could duplicate a mutation) and the UI shows the error.',
  },
  {
    id: 'validate', title: 'FastAPI validates request', component: 'FastAPI request layer', facts: ['limit'],
    what: 'A body-limit middleware rejects over-limit bodies before parsing (declared length and bytes received). The filename is reduced to a safe base name. The single-writer lock is then taken.',
    why: 'Bounded requests prevent resource exhaustion; the lock keeps all mutations sequential.',
    data: 'The body is spooled by the multipart parser, then streamed into a file in data/staging/ while its size is counted and the whole-file SHA-256 is computed.',
    failure: '413 upload_too_large, 400 invalid_content_length, 422 invalid filename, 409 writer_busy. Nothing is committed.',
  },
  {
    id: 'cdc', title: 'CDC generates variable-sized chunks', component: 'CDC engine (chunking/rabin.py)', facts: ['chunks', 'config'],
    what: 'The staged file is read incrementally. A rolling Rabin-style fingerprint over a 64-byte window decides where each chunk ends: not before the minimum, at the first position where fingerprint mod D == D − 1, or at the maximum.',
    why: 'Content-defined boundaries keep chunks stable when data is inserted or removed nearby.',
    data: 'Chunks exist in memory one at a time; nothing is written yet. A transaction T1 has already recorded the file as "uploading" and the operation as "running".',
    failure: 'A read error aborts the upload: file and operation are marked failed and staging is cleaned.',
  },
  {
    id: 'hash', title: 'SHA-256 identifies chunk contents', component: 'SHA-256 service (hashing/sha256.py)', facts: ['hashes'],
    what: 'Each chunk is hashed. The 64-character digest is the chunk\'s identity and storage address. The rolling fingerprint is only used for boundaries and is never stored.',
    why: 'A cryptographic digest lets equal content be recognised by lookup and verified later.',
    data: 'Digest per chunk, held in the manifest being built.',
    failure: 'None expected for byte input; a digest mismatch later (restore or audit) reveals corruption.',
  },
  {
    id: 'dedup', title: 'Deduplication checks existing chunks', component: 'Deduplication manager (deduplication/manager.py)', facts: ['dedup'],
    what: 'For each new digest the manager looks it up in SQLite AND re-hashes the stored bytes. Result: NEW (no row, no bytes), DUPLICATE (stored and verified) or ADOPT (valid orphan bytes from an interrupted upload). A repeat within the same file reuses the earlier decision.',
    why: 'Metadata alone is never trusted as proof the bytes exist, and a damaged chunk must never be silently reused or overwritten.',
    data: 'Nothing is modified; the decision list is held in memory.',
    failure: 'Same digest with a different size → 409 chunk_identity_conflict. Known chunk missing or corrupt → 409 chunk_unavailable: the upload is refused, not repaired.',
  },
  {
    id: 'publish', title: 'New chunks are staged and published', component: 'Local chunk store (storage/filesystem.py)', facts: ['publish'],
    what: 'Only NEW chunks are written: to a temporary file in staging/, fsynced, then atomically renamed to chunks/ab/<sha256>. An existing chunk is never overwritten.',
    why: 'Bytes must be durable and complete before any metadata says they exist; an atomic rename means readers never see a partial chunk.',
    data: 'New chunk files appear in the chunk store. No chunk rows exist yet.',
    failure: 'Disk error: the upload fails and chunks this upload published are removed again. After a crash they remain as orphan chunk files that the audit reports.',
  },
  {
    id: 'commit', title: 'SQLite commits metadata and references', component: 'SQLite metadata (database/)', facts: ['manifest'],
    what: 'One transaction (T2) inserts the new chunk rows as "stored", every ordered manifest row, sets the file "complete" with its SHA-256 and finishes the operation. Database triggers raise each chunk\'s ref_count once per manifest row.',
    why: 'A single commit means a file is complete only if every chunk is stored and mapped; either all of it is visible or none.',
    data: 'Rows in chunks, file_chunks, files and operations; ref_count values.',
    failure: 'Any error rolls the whole transaction back; the file and operation are then marked failed. A crash mid-transaction rolls back automatically.',
  },
  {
    id: 'done', title: 'Upload completes and statistics are returned', component: 'FastAPI → React frontend', facts: ['result'],
    what: 'The staged upload file is removed and the API returns 201 with the file id, whole-file SHA-256, counts of new and reused chunks, bytes written and a per-position decision list.',
    why: 'The client learns exactly what the server did, with nothing estimated.',
    data: 'The response is not stored. The per-position decisions exist only in this response.',
    failure: 'If the response is lost the upload is still committed; the file appears in the inventory.',
  },
]

export const RESTORE_STAGES = [
  {
    id: 'request', title: 'Client requests file restoration', component: 'React frontend', facts: ['file'],
    what: 'The UI calls GET /api/files/{id}/download through its download helper, which saves the bytes only if the server confirms success.',
    why: 'Surfacing server errors prevents saving a bad file.',
    data: 'Nothing is modified.',
    failure: 'Network failure: error shown; nothing saved.',
  },
  {
    id: 'validate', title: 'FastAPI validates the file ID', component: 'FastAPI request layer', facts: ['id'],
    what: 'The path parameter must be 32 lowercase hex characters, otherwise the request is rejected before any lookup.',
    why: 'Validated identifiers keep malformed or hostile input away from the database and filesystem.',
    data: 'Nothing is modified.',
    failure: '422 validation_error for a malformed id.',
  },
  {
    id: 'manifest', title: 'SQLite retrieves the ordered manifest', component: 'Restoration engine + SQLite', facts: ['manifest'],
    what: 'The file must exist and be "complete". Its manifest rows are read in chunk_index order and checked for gaps.',
    why: 'Chunk order defines the file: the same chunks in a different order are a different file.',
    data: 'A short transaction records a "restore" operation as running.',
    failure: '404 file_not_restorable (unknown or incomplete file); 500 manifest_inconsistent for a gap.',
  },
  {
    id: 'read', title: 'The chunk store reads required chunks', component: 'Local chunk store', facts: ['paths'],
    what: 'Each chunk is read from its content address chunks/ab/<sha256>. Repeated chunks are simply read again.',
    why: 'The digest alone locates the bytes; no per-file copy exists.',
    data: 'Nothing is modified.',
    failure: '500 chunk_missing when a chunk file is absent. The restore stops; no partial result is returned.',
  },
  {
    id: 'verify', title: 'SHA-256 verifies individual chunks', component: 'SHA-256 service', facts: ['hashes'],
    what: 'Every chunk\'s length and SHA-256 are compared with the recorded size and digest before it is used.',
    why: 'Chunk-level verification pinpoints which stored chunk is damaged, and catches corruption that metadata cannot show.',
    data: 'Nothing is modified.',
    failure: '500 chunk_corrupt for a size or digest mismatch.',
  },
  {
    id: 'rebuild', title: 'The restoration engine reconstructs the file', component: 'Restoration engine', facts: ['order'],
    what: 'Verified chunks are appended in order to a temporary file in staging/ while the whole-file hash is updated.',
    why: 'Building into a temporary file means the client is only given a result that is already fully verified.',
    data: 'A temporary restore file in data/staging/.',
    failure: 'Any failure removes the temporary file; the operation is marked failed.',
  },
  {
    id: 'whole', title: 'Whole-file hash and size are verified', component: 'Restoration engine', facts: ['whole'],
    what: 'The reassembled size and SHA-256 are compared with the values recorded at upload.',
    why: 'Chunk-level checks prove each piece is intact; the whole-file check proves the pieces are the right ones in the right order and nothing is missing.',
    data: 'Nothing is modified.',
    failure: '500 file_hash_mismatch: the file is not served.',
  },
  {
    id: 'return', title: 'The verified file is returned', component: 'FastAPI → React frontend', facts: ['whole'],
    what: 'The temporary file is sent as an attachment with an X-ChunkVault-SHA256 header, then deleted. The operation is recorded as succeeded.',
    why: 'The client receives bytes the server has proven identical to what was uploaded.',
    data: 'The "restore" operation row is completed; the temporary file is removed.',
    failure: 'If the client disconnects the temporary file is still cleaned up.',
  },
]
