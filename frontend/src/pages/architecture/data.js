// Descriptive content for the Architecture page. It mirrors docs/architecture/lld.md and
// docs/architecture/consistency-protocol.md; the Documentation page renders those documents
// directly. Nothing here is a measurement.

export { COMPONENTS, EDGES, FOCUS_ALIASES, LAYERS, dependenciesOf, dependentsOf } from './components'

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
