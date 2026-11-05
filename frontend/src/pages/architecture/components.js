// Component catalogue for the architecture page. It mirrors docs/architecture/lld.md and the
// real backend/frontend modules; every module path below exists in the repository.
// Descriptive content only — nothing here is a measurement.

export const LAYERS = {
  frontend: { label: 'Frontend', note: 'Browser and React application' },
  api: { label: 'API', note: 'FastAPI request layer' },
  processing: { label: 'Processing', note: 'In-process backend modules' },
  persistence: { label: 'Persistence', note: 'SQLite metadata and the local chunk store' },
}

export const COMPONENTS = [
  {
    id: 'browser', name: 'User / browser', layer: 'frontend', x: 20, y: 30, w: 150,
    purpose: 'The person using the system; the browser runs the React application and performs the file transfer.',
    inputs: ['Files chosen or dropped by the user'],
    outputs: ['HTTP requests', 'Downloaded files'],
    reactModules: ['frontend/src/main.jsx'],
    modules: [],
    endpoints: [],
    failures: ['Network failure: the UI shows "backend unreachable" instead of stale or invented data'],
    rationale: 'ChunkVault is client-server: the browser holds no storage logic and trusts nothing it has not read from the API.',
  },
  {
    id: 'client', name: 'React frontend', layer: 'frontend', x: 20, y: 120, w: 150,
    purpose: 'User interface: uploads files, shows real API results, explains the architecture, downloads restored files. Holds no storage logic.',
    inputs: ['User actions', 'JSON responses from the API'],
    outputs: ['Multipart upload requests', 'REST calls', 'Downloaded file bytes'],
    reactModules: ['frontend/src/services/api.js', 'frontend/src/pages/FilesPage.jsx', 'frontend/src/pages/Dashboard.jsx'],
    modules: [],
    endpoints: ['all /api endpoints'],
    failures: ['Backend unreachable: error state, never fabricated data', 'Upload rejected (413 / 4xx): shows the server message'],
    rationale: 'One centralised API client gives consistent error parsing, cancellation, GET-only retries and no duplicate mutations.',
  },
  {
    id: 'api', name: 'FastAPI request layer', layer: 'api', x: 220, y: 120, w: 160,
    purpose: 'Routing, validation, bounded request bodies, structured error envelope, CORS, OpenAPI. Contains no business logic.',
    inputs: ['HTTP requests'],
    outputs: ['JSON / file responses', 'Error envelope {error:{code,message,details}}'],
    reactModules: [],
    modules: ['backend/app/main.py', 'backend/app/api/'],
    endpoints: ['GET /api/health', 'POST /api/files', 'GET /api/files', 'GET /api/files/{id}', 'GET /api/files/{id}/download', 'DELETE /api/files/{id}', 'GET /api/chunks', 'GET /api/storage/stats', 'POST /api/integrity/audit', 'POST /api/integrity/reconcile', 'GET /api/inspect/*', 'GET /api/system/*'],
    failures: ['Oversized body: 413 before parsing', 'Validation failure: 422', 'Unexpected error: generic 500 with no internals'],
    rationale: 'A thin API layer keeps the modules behind it independent and testable; the body limit stops resource exhaustion before parsing.',
  },
  {
    id: 'orchestrator', name: 'Upload orchestrator', layer: 'processing', x: 430, y: 30, w: 160,
    purpose: 'Runs the upload pipeline and owns its two metadata transactions and failure handling, under the single-writer lock.',
    inputs: ['Filename', 'Binary stream'],
    outputs: ['UploadResult (counts and per-position decisions)'],
    reactModules: [],
    modules: ['backend/app/deduplication/upload.py', 'backend/app/core/locking.py'],
    endpoints: ['POST /api/files'],
    failures: ['Error before the final commit: file and operation marked failed, staged data removed, chunks this upload created are un-published', 'Crash: leftovers found by the audit, removed by reconciliation'],
    rationale: 'One component owns the order of effects (bytes before metadata), which is what makes the consistency protocol checkable.',
  },
  {
    id: 'cdc', name: 'CDC engine', layer: 'processing', x: 430, y: 120, w: 160,
    purpose: 'Chooses chunk boundaries from content with a Rabin-style rolling fingerprint (64-byte window; 4 KiB min, 16 KiB target, 128 KiB max).',
    inputs: ['Readable byte stream'],
    outputs: ['Ordered chunks (index, offset, bytes)'],
    reactModules: [],
    modules: ['backend/app/chunking/rabin.py'],
    endpoints: ['GET /api/system/config', 'GET /api/system/chunking-demo'],
    failures: ['Stream read error: ChunkingError, the upload is rolled back'],
    rationale: 'Content-defined boundaries let edits shift only nearby chunks (ADR-0001).',
  },
  {
    id: 'hashing', name: 'SHA-256 service', layer: 'processing', x: 430, y: 210, w: 160,
    purpose: 'Chunk identity and integrity: lowercase-hex SHA-256 of every chunk and of the whole file. Separate from the rolling fingerprint.',
    inputs: ['Chunk bytes'],
    outputs: ['64-character digest'],
    reactModules: [],
    modules: ['backend/app/hashing/sha256.py'],
    endpoints: [],
    failures: ['Malformed expected digest: InvalidDigestError (a caller bug)'],
    rationale: 'A cryptographic digest is safe to use as an address and doubles as a checksum (ADR-0002).',
  },
  {
    id: 'dedup', name: 'Deduplication manager', layer: 'processing', x: 430, y: 300, w: 160,
    purpose: 'Decides NEW / DUPLICATE / ADOPT for each chunk from the metadata and the verified stored bytes. Never overwrites or repairs.',
    inputs: ['Chunk digest and size'],
    outputs: ['Decision'],
    reactModules: [],
    modules: ['backend/app/deduplication/manager.py'],
    endpoints: [],
    failures: ['Same digest, different size: chunk_identity_conflict', 'Known chunk missing or corrupt on disk: chunk_unavailable (upload refused)'],
    rationale: 'Metadata alone is never trusted as proof that bytes exist; corruption must be visible, not silently overwritten.',
  },
  {
    id: 'metadata', name: 'SQLite metadata', layer: 'persistence', x: 650, y: 120, w: 170,
    purpose: 'Files, chunk records with reference counts, ordered file-to-chunk manifests and operations. Constraints and triggers enforce the invariants.',
    inputs: ['Repository calls inside explicit transactions'],
    outputs: ['Records'],
    reactModules: ['frontend/src/pages/DatabasePage.jsx'],
    modules: ['backend/app/database/schema.sql', 'backend/app/database/sqlite_repository.py'],
    endpoints: ['GET /api/inspect/schema', 'GET /api/inspect/tables/{table}/rows', 'GET /api/chunks', 'GET /api/files'],
    failures: ['Transaction error: rolled back as a unit', 'Foreign keys enforced on every connection; reference counts maintained by triggers'],
    rationale: 'Real transactions, constraints and triggers with no infrastructure (ADR-0003, ADR-0006).',
  },
  {
    id: 'store', name: 'Local chunk store', layer: 'persistence', x: 650, y: 300, w: 170,
    purpose: 'Content-addressed bytes at chunks/ab/<sha256>. Writes go to staging, are fsynced and published with an atomic rename. Never overwrites.',
    inputs: ['Digest and bytes'],
    outputs: ['Bytes by digest'],
    reactModules: [],
    modules: ['backend/app/storage/filesystem.py'],
    endpoints: [],
    failures: ['Disk error: ChunkWriteError', 'Missing file: ChunkNotFoundError', 'Existing file with wrong content: ChunkCorruptedError'],
    rationale: 'A local filesystem keeps the prototype dependency-free, and rename gives atomic publication (ADR-0004).',
  },
  {
    id: 'restore', name: 'Restoration engine', layer: 'processing', x: 430, y: 390, w: 160,
    purpose: 'Rebuilds a file in a temporary file from its ordered manifest, verifying every chunk and the whole file before it is served.',
    inputs: ['File ID'],
    outputs: ['Verified file (or an explicit error)'],
    reactModules: ['frontend/src/services/api.js (downloadFile)'],
    modules: ['backend/app/restoration/engine.py'],
    endpoints: ['GET /api/files/{id}/download'],
    failures: ['chunk_missing', 'chunk_corrupt', 'file_hash_mismatch', 'manifest_inconsistent: never a partial success; the temp file is removed'],
    rationale: 'Verifying before serving means a damaged store can never produce a silently wrong file.',
  },
  {
    id: 'auditor', name: 'Integrity auditor', layer: 'processing', x: 430, y: 480, w: 160,
    purpose: 'Read-only detection of missing/corrupt/orphan chunks, ref-count mismatches, stale staging files and incomplete operations. A separate reconciler does explicit, reported cleanup.',
    inputs: ['Metadata snapshot', 'Chunk store contents'],
    outputs: ['Findings; reconciliation action list'],
    reactModules: ['frontend/src/pages/ReliabilityPage.jsx'],
    modules: ['backend/app/reliability/auditor.py', 'backend/app/reliability/reconciler.py'],
    endpoints: ['POST /api/integrity/audit', 'POST /api/integrity/reconcile'],
    failures: ['Referenced missing/corrupt chunks are reported, never repaired', 'Reconcile never deletes a chunk that any manifest references'],
    rationale: 'Detection and repair are separate so an audit can never change data (ADR-0007).',
  },
]

// [from, to]: "from depends on / calls to"
export const EDGES = [
  ['browser', 'client'], ['client', 'api'], ['api', 'orchestrator'], ['orchestrator', 'cdc'],
  ['orchestrator', 'hashing'], ['orchestrator', 'dedup'], ['dedup', 'metadata'], ['dedup', 'store'],
  ['orchestrator', 'metadata'], ['orchestrator', 'store'], ['api', 'restore'], ['restore', 'hashing'],
  ['restore', 'metadata'], ['restore', 'store'], ['api', 'auditor'], ['auditor', 'hashing'],
  ['auditor', 'metadata'], ['auditor', 'store'], ['api', 'metadata'],
]

export const FOCUS_ALIASES = {
  React: 'client', FastAPI: 'api', CDC: 'cdc', 'SHA-256': 'hashing', Deduplication: 'dedup',
  'SQLite + Chunk Store': 'metadata',
}

const nameOf = (id) => COMPONENTS.find((c) => c.id === id)?.name

export const dependenciesOf = (id) => EDGES.filter(([a]) => a === id).map(([, b]) => ({ id: b, name: nameOf(b) }))
export const dependentsOf = (id) => EDGES.filter(([, b]) => b === id).map(([a]) => ({ id: a, name: nameOf(a) }))
