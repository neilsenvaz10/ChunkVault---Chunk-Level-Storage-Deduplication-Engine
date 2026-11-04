// Descriptive content for the Architecture page. It mirrors docs/architecture/lld.md and
// docs/architecture/consistency-protocol.md; the Documentation page renders those documents
// directly. Nothing here is a measurement.

export const COMPONENTS = [
  {
    id: 'client', name: 'React client', layer: 'Client', x: 20, y: 40, w: 150,
    responsibility: 'User interface: uploads files, shows real results from the API, downloads restored files. Holds no storage logic.',
    inputs: ['User actions', 'JSON responses from the API'],
    outputs: ['Multipart upload requests', 'REST calls', 'Downloaded file bytes'],
    dependencies: ['FastAPI request layer'],
    failures: ['Backend unreachable: shows an error state, never fabricated data', 'Upload rejected (413 / 4xx): shows the server error message'],
    module: 'frontend/src', endpoints: ['all /api endpoints'],
  },
  {
    id: 'api', name: 'FastAPI request layer', layer: 'Request layer', x: 220, y: 40, w: 160,
    responsibility: 'Routing, validation, bounded request bodies, structured error envelope, CORS, OpenAPI. No business logic.',
    inputs: ['HTTP requests'],
    outputs: ['JSON / file responses', 'Error envelope {error:{code,message,details}}'],
    dependencies: ['Upload orchestrator', 'Restoration engine', 'Integrity auditor', 'Metadata repository'],
    failures: ['Oversized body: 413 before parsing', 'Validation failure: 422', 'Unexpected error: generic 500, no internals leaked'],
    module: 'backend/app/api, backend/app/main.py',
    endpoints: ['GET /api/health', 'POST /api/files', 'GET /api/files', 'GET /api/files/{id}', 'GET /api/files/{id}/download', 'DELETE /api/files/{id}', 'GET /api/chunks', 'GET /api/storage/stats', 'POST /api/integrity/audit', 'POST /api/integrity/reconcile', 'GET /api/inspect/*'],
  },
  {
    id: 'orchestrator', name: 'Upload orchestrator', layer: 'Upload pipeline', x: 430, y: 40, w: 160,
    responsibility: 'Runs the upload pipeline and owns its two metadata transactions and failure handling, under the single-writer lock.',
    inputs: ['Filename', 'Binary stream'],
    outputs: ['UploadResult (counts and per-position decisions)'],
    dependencies: ['CDC engine', 'SHA-256 service', 'Deduplication manager', 'Chunk store', 'Metadata repository', 'Writer lock'],
    failures: ['Any error before the final commit: file and operation marked failed, staged data removed, chunks this upload created are un-published', 'Crash: leftovers found by the audit, removed by reconciliation'],
    module: 'backend/app/deduplication/upload.py', endpoints: ['POST /api/files'],
  },
  {
    id: 'cdc', name: 'CDC engine', layer: 'Upload pipeline', x: 430, y: 130, w: 160,
    responsibility: 'Chooses chunk boundaries from content with a Rabin-style rolling fingerprint (64-byte window, 4 KiB min, 16 KiB target, 128 KiB max).',
    inputs: ['Readable byte stream'],
    outputs: ['Ordered chunks (index, offset, bytes)'],
    dependencies: ['CDCConfig'],
    failures: ['Stream read error: ChunkingError, the upload is rolled back'],
    module: 'backend/app/chunking/rabin.py', endpoints: [],
  },
  {
    id: 'hashing', name: 'SHA-256 service', layer: 'Upload pipeline', x: 430, y: 220, w: 160,
    responsibility: 'Chunk identity and integrity: lowercase-hex SHA-256 of every chunk and of the whole file. Separate from the rolling fingerprint.',
    inputs: ['Chunk bytes'],
    outputs: ['64-character digest'],
    dependencies: ['Python hashlib'],
    failures: ['Malformed expected digest: InvalidDigestError (caller bug)'],
    module: 'backend/app/hashing/sha256.py', endpoints: [],
  },
  {
    id: 'dedup', name: 'Deduplication manager', layer: 'Upload pipeline', x: 430, y: 310, w: 160,
    responsibility: 'Decides NEW / DUPLICATE / ADOPT for each chunk from the metadata and the verified stored bytes. Never overwrites or repairs.',
    inputs: ['Chunk digest and size'],
    outputs: ['Decision'],
    dependencies: ['Metadata repository', 'Chunk store'],
    failures: ['Same digest, different size: chunk_identity_conflict', 'Known chunk missing or corrupt on disk: chunk_unavailable (upload refused)'],
    module: 'backend/app/deduplication/manager.py', endpoints: [],
  },
  {
    id: 'metadata', name: 'SQLite metadata', layer: 'Storage', x: 650, y: 130, w: 170,
    responsibility: 'Files, chunk records with reference counts, ordered file-to-chunk manifests and operations. Constraints and triggers enforce the invariants.',
    inputs: ['Repository calls inside explicit transactions'],
    outputs: ['Records'],
    dependencies: ['SQLite file'],
    failures: ['Transaction error: rolled back as a unit', 'Foreign keys on every connection; reference counts maintained by triggers'],
    module: 'backend/app/database/', endpoints: ['GET /api/inspect/schema', 'GET /api/inspect/tables/{table}/rows'],
  },
  {
    id: 'store', name: 'Local chunk store', layer: 'Storage', x: 650, y: 310, w: 170,
    responsibility: 'Content-addressed bytes at chunks/ab/<sha256>. Writes go to staging, are fsynced and published with an atomic rename. Never overwrites.',
    inputs: ['Digest and bytes'],
    outputs: ['Bytes by digest'],
    dependencies: ['Local filesystem (same volume for staging and chunks)'],
    failures: ['Disk error: ChunkWriteError', 'Missing file: ChunkNotFoundError', 'Existing file with wrong content: ChunkCorruptedError'],
    module: 'backend/app/storage/filesystem.py', endpoints: [],
  },
  {
    id: 'restore', name: 'Restoration engine', layer: 'Restore path', x: 220, y: 400, w: 160,
    responsibility: 'Rebuilds a file in a temporary file from its ordered manifest, verifying every chunk and the whole file before it is served.',
    inputs: ['File ID'],
    outputs: ['Verified file (or an explicit error)'],
    dependencies: ['Metadata repository', 'Chunk store', 'SHA-256 service'],
    failures: ['chunk_missing', 'chunk_corrupt', 'file_hash_mismatch', 'manifest_inconsistent: never a partial success; temp file removed'],
    module: 'backend/app/restoration/engine.py', endpoints: ['GET /api/files/{id}/download'],
  },
  {
    id: 'auditor', name: 'Integrity auditor', layer: 'Reliability', x: 430, y: 400, w: 160,
    responsibility: 'Read-only detection of missing/corrupt/orphan chunks, ref-count mismatches, stale staging files and incomplete operations. A separate reconciler performs explicit, reported cleanup.',
    inputs: ['Metadata snapshot', 'Chunk store contents'],
    outputs: ['Findings; reconciliation action list'],
    dependencies: ['Metadata repository', 'Chunk store', 'SHA-256 service', 'Writer lock (reconcile only)'],
    failures: ['Referenced missing/corrupt chunks are reported, never repaired', 'Reconcile never deletes a chunk referenced by any manifest'],
    module: 'backend/app/reliability/', endpoints: ['POST /api/integrity/audit', 'POST /api/integrity/reconcile'],
  },
]

export const EDGES = [
  ['client', 'api'], ['api', 'orchestrator'], ['orchestrator', 'cdc'], ['orchestrator', 'hashing'],
  ['orchestrator', 'dedup'], ['dedup', 'metadata'], ['orchestrator', 'metadata'], ['orchestrator', 'store'],
  ['api', 'restore'], ['restore', 'metadata'], ['restore', 'store'], ['api', 'auditor'],
  ['auditor', 'metadata'], ['auditor', 'store'],
]

export const FOCUS_ALIASES = {
  React: 'client', FastAPI: 'api', CDC: 'cdc', 'SHA-256': 'hashing', Deduplication: 'dedup',
  'SQLite + Chunk Store': 'metadata',
}

export const UPLOAD_SEQUENCE = {
  actors: ['Client', 'FastAPI', 'Orchestrator', 'CDC + SHA-256', 'Dedup', 'Chunk store', 'SQLite'],
  messages: [
    { from: 0, to: 1, label: '1. POST /api/files (multipart)', detail: 'The browser sends the file. Before parsing, a middleware rejects bodies over the limit (413) by declared length and by bytes received.' },
    { from: 1, to: 2, label: '2. validate, take writer lock', detail: 'The filename is reduced to a safe base name; the single-writer lock (thread + OS file lock) serialises all mutations. The upload is streamed into staging/ with the size limit enforced and the whole-file SHA-256 computed.' },
    { from: 2, to: 6, label: 'T1: file(uploading) + operation(running)', detail: 'First transaction: the file row exists only as "uploading", so an interrupted upload can never look complete.' },
    { from: 2, to: 3, label: '3. CDC cuts chunks, 4. SHA-256 each', detail: 'The staged file is read incrementally. The rolling fingerprint decides where each chunk ends; SHA-256 of the chunk bytes is its identity.' },
    { from: 2, to: 4, label: '5. is this digest already stored?', detail: 'The dedup manager checks the metadata and re-hashes the stored bytes before trusting them: DUPLICATE, NEW or ADOPT. A known-but-damaged chunk aborts the upload.' },
    { from: 2, to: 5, label: '6. stage, fsync, atomic publish (new chunks)', detail: 'Only chunks that are not stored yet are written: temp file in staging/, fsync, then an atomic rename to chunks/ab/<sha256>. Bytes become durable before any metadata refers to them.' },
    { from: 2, to: 6, label: '7. T2: chunks, manifest, file complete', detail: 'One transaction inserts the new chunk rows, every manifest row (triggers raise ref_count once per occurrence), marks the file complete and the operation succeeded. If it fails, everything rolls back.' },
    { from: 1, to: 0, label: '8. 201 UploadResponse', detail: 'The response reports the whole-file SHA-256, chunk count, new vs reused chunks, bytes actually written and a per-position decision list.' },
  ],
}

export const RESTORE_SEQUENCE = {
  actors: ['Client', 'FastAPI', 'Restoration', 'SQLite', 'Chunk store', 'SHA-256'],
  messages: [
    { from: 0, to: 1, label: '1. GET /api/files/{id}/download', detail: 'The file id is validated by pattern before any lookup.' },
    { from: 1, to: 2, label: 'restore_to_file(id, temp)', detail: 'Only files in the "complete" state can be restored.' },
    { from: 2, to: 3, label: '2. ordered manifest', detail: 'The manifest is read in chunk_index order and checked for gaps.' },
    { from: 2, to: 4, label: '3. read chunk bytes', detail: 'Each chunk is read from its content address.' },
    { from: 4, to: 5, label: '4. verify each chunk (size, SHA-256)', detail: 'Stored bytes are re-hashed and compared with the recorded digest and size. Any mismatch aborts with chunk_corrupt / chunk_missing.' },
    { from: 2, to: 2, label: '5. reconstruct into a temp file', detail: 'Verified chunks are appended in order to a temporary file in staging/.' },
    { from: 2, to: 5, label: '6. whole-file size + SHA-256', detail: 'The finished file is compared with the size and SHA-256 recorded at upload.' },
    { from: 1, to: 0, label: '7. verified file returned', detail: 'Only now is the file sent, with an X-ChunkVault-SHA256 header. A failure at any step removes the temp file and returns an error envelope instead of partial data.' },
  ],
}

export const DATA_FLOWS = [
  { type: 'bytes', from: 'Client', to: 'Staging file', label: 'uploaded file bytes (multipart body)' },
  { type: 'bytes', from: 'Staging file', to: 'CDC engine', label: 'incremental read of the file' },
  { type: 'bytes', from: 'CDC engine', to: 'SHA-256 service', label: 'chunk bytes' },
  { type: 'digest', from: 'SHA-256 service', to: 'Deduplication manager', label: 'chunk digest (identity)' },
  { type: 'metadata', from: 'Deduplication manager', to: 'SQLite', label: 'lookup: is this digest stored?' },
  { type: 'bytes', from: 'Orchestrator', to: 'Chunk store', label: 'bytes of NEW chunks only' },
  { type: 'metadata', from: 'Orchestrator', to: 'SQLite', label: 'file, chunk and manifest rows (T1, T2)' },
  { type: 'digest', from: 'Chunk store', to: 'Chunk store', label: 'digest is the file name (chunks/ab/<sha256>)' },
  { type: 'api', from: 'Client', to: 'FastAPI', label: 'HTTP requests: upload, list, download, delete, audit' },
  { type: 'api', from: 'FastAPI', to: 'Client', label: 'JSON responses and error envelopes; verified file on download' },
  { type: 'metadata', from: 'SQLite', to: 'Restoration engine', label: 'ordered manifest (digests and sizes)' },
  { type: 'bytes', from: 'Chunk store', to: 'Restoration engine', label: 'chunk bytes, re-verified against their digests' },
]

export const CONCEPTS = [
  ['Client-server architecture', 'The React app is a pure client: every number it shows comes from an API response. The server owns all storage logic and validates every request.'],
  ['Modular backend architecture', 'One FastAPI process with independent internal modules (chunking, hashing, deduplication, storage, database, restoration, reliability), each behind an interface. It is deliberately not microservices: components call each other in-process.'],
  ['Content-addressed storage', 'A chunk is stored under the SHA-256 of its own bytes. Identical content always maps to the same address, so deduplication is a lookup, and any stored chunk can be verified by re-hashing it.'],
  ['Metadata versus payload storage', 'SQLite holds small, queryable records (files, chunk records, ordered manifests, reference counts). The filesystem holds the bytes. They are linked only by the digest, so listing files or counting references never reads chunk data.'],
  ['Transaction boundaries', 'SQLite offers atomic transactions; the filesystem does not take part in them. Uploads use two: T1 records an in-progress file, T2 atomically commits chunks, manifest and completion. Deletes remove all metadata in one transaction and unlink bytes only afterwards.'],
  ['Data consistency', 'Bytes are published before metadata references them; a file becomes "complete" only in the final transaction; reference counts are maintained by database triggers (one per manifest occurrence), and a view audits them. Reads re-verify digests rather than trusting metadata.'],
  ['Failure recovery', 'Every crash state is detectable: orphan chunk files, stale staging files, files stuck in "uploading", operations stuck in "running". The audit only reports; reconciliation is a separate, explicit, logged action and never deletes referenced data. Damaged referenced chunks are reported, not silently repaired.'],
]
