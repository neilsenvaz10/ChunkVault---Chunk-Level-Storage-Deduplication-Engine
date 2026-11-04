// Deterministic API fixtures for component tests (hand-written, never used by the app itself).
export const H = (c) => c.repeat(64)

export const health = {
  status: 'ok', service: 'ChunkVault', version: '0.1.0', max_upload_bytes: 64 * 1024 * 1024,
  database: { connected: true, foreign_keys_enforced: true, schema_version: 1 },
}

export const emptyStats = {
  total_files: 0, logical_bytes: 0, unique_chunks: 0, unique_chunk_bytes: 0, total_chunk_references: 0,
  reused_chunk_references: 0, unreferenced_chunks: 0, unreferenced_chunk_bytes: 0,
  storage_savings_percent: null, deduplication_ratio: null, database_bytes: 4096, chunk_files: 0,
  chunk_store_bytes: 0, staging_files: 0, staging_bytes: 0,
}

export const stats = {
  total_files: 2, logical_bytes: 2097152, unique_chunks: 49, unique_chunk_bytes: 1048576,
  total_chunk_references: 98, reused_chunk_references: 49, unreferenced_chunks: 0, unreferenced_chunk_bytes: 0,
  storage_savings_percent: 50, deduplication_ratio: 2, database_bytes: 73728, chunk_files: 49,
  chunk_store_bytes: 1048576, staging_files: 0, staging_bytes: 0,
}

export const fileA = {
  file_id: 'a'.repeat(32), filename: 'original.bin', original_size: 30000, sha256: H('1'), status: 'complete',
  created_at: '2026-01-01T10:00:00.000Z', chunk_count: 3,
}
export const fileB = {
  file_id: 'b'.repeat(32), filename: 'modified.bin', original_size: 30100, sha256: H('2'), status: 'complete',
  created_at: '2026-01-01T10:05:00.000Z', chunk_count: 3,
}
export const fileList = { items: [fileB, fileA], total: 2, limit: 25, offset: 0 }

export const detailA = {
  ...fileA, unique_chunk_count: 3,
  chunks: [
    { chunk_index: 0, chunk_hash: H('c'), chunk_size: 10000, ref_count: 1 },
    { chunk_index: 1, chunk_hash: H('d'), chunk_size: 10000, ref_count: 2 },
    { chunk_index: 2, chunk_hash: H('e'), chunk_size: 10000, ref_count: 2 },
  ],
}
export const detailB = {
  ...fileB, unique_chunk_count: 3,
  chunks: [
    { chunk_index: 0, chunk_hash: H('f'), chunk_size: 10100, ref_count: 1 },
    { chunk_index: 1, chunk_hash: H('d'), chunk_size: 10000, ref_count: 2 },
    { chunk_index: 2, chunk_hash: H('e'), chunk_size: 10000, ref_count: 2 },
  ],
}

export const chunkDetail = {
  hash: H('d'), size_bytes: 10000, ref_count: 2, state: 'stored', created_at: '2026-01-01T10:00:00.000Z',
  actual_references: 2, references_truncated: false,
  references: [
    { file_id: fileA.file_id, filename: 'original.bin', file_status: 'complete', chunk_index: 1 },
    { file_id: fileB.file_id, filename: 'modified.bin', file_status: 'complete', chunk_index: 1 },
  ],
}

export const uploadResult = {
  file_id: 'c'.repeat(32), filename: 'new.bin', original_size: 30000, sha256: H('9'), chunk_count: 3,
  new_chunks: 1, duplicate_chunks: 2, bytes_written: 10000,
  chunk_outcomes: [
    { chunk_index: 0, chunk_hash: H('c'), chunk_size: 10000, decision: 'new' },
    { chunk_index: 1, chunk_hash: H('d'), chunk_size: 10000, decision: 'reused' },
    { chunk_index: 2, chunk_hash: H('d'), chunk_size: 10000, decision: 'repeated_in_file' },
  ],
}

const col = (name, type, pk = 0, fk = null, description = `${name} description`) => ({
  name, type, not_null: true, primary_key_position: pk, default: null, foreign_key: fk, description,
})
export const schema = {
  schema_version: 1,
  tables: [
    { name: 'files', description: 'One logical file.', row_count: 2, columns: [col('file_id', 'TEXT', 1), col('filename', 'TEXT'), col('status', 'TEXT')] },
    { name: 'chunks', description: 'One row per distinct chunk.', row_count: 3, columns: [col('hash', 'TEXT', 1), col('size_bytes', 'INTEGER'), col('ref_count', 'INTEGER')] },
    { name: 'file_chunks', description: 'The ordered manifest.', row_count: 6, columns: [
      col('file_id', 'TEXT', 1, { table: 'files', column: 'file_id', on_delete: 'CASCADE' }),
      col('chunk_index', 'INTEGER', 2),
      col('chunk_hash', 'TEXT', 0, { table: 'chunks', column: 'hash', on_delete: 'RESTRICT' })] },
    { name: 'operations', description: 'Audit trail.', row_count: 2, columns: [col('operation_id', 'INTEGER', 1), col('file_id', 'TEXT', 0, { table: 'files', column: 'file_id', on_delete: 'SET NULL' })] },
  ],
  ref_count_invariant: { statement: 'chunks.ref_count = COUNT(file_chunks rows)', chunks_checked: 3, mismatches: 0 },
}

export const fileRows = {
  table: 'files', columns: ['file_id', 'filename', 'status'], total: 2, limit: 15, offset: 0,
  rows: [
    { file_id: fileA.file_id, filename: 'original.bin', status: 'complete' },
    { file_id: fileB.file_id, filename: 'modified.bin', status: 'complete' },
  ],
}

export const healthyAudit = { healthy: true, chunks_checked: 49, files_checked: 2, findings: [], counts_by_kind: {} }
export const damagedAudit = {
  healthy: false, chunks_checked: 49, files_checked: 2, counts_by_kind: { corrupt_chunk: 1, orphan_staging_file: 1 },
  findings: [
    { kind: 'corrupt_chunk', subject: H('d'), detail: 'stored bytes do not match digest/size; referenced by files: ' + fileA.file_id },
    { kind: 'orphan_staging_file', subject: 'upload-1.part', detail: 'stale file in staging area' },
  ],
}

export const planned = {
  endpoints: [
    { method: 'POST', path: '/api/benchmarks', summary: 'Run a local benchmark', request_schema: 'BenchmarkRequest', response_schema: 'BenchmarkResponse', phase: 6, status: 'planned' },
    { method: 'POST', path: '/api/simulations', summary: 'Run the analytical capacity model', request_schema: 'SimulationRequest', response_schema: 'SimulationResponse', phase: 7, status: 'planned' },
  ],
}

export const systemConfig = {
  cdc: { min_chunk_size: 4096, avg_chunk_size: 16384, max_chunk_size: 131072, window_size: 64, boundary_divisor: 12288,
    boundary_remainder: 12287, polynomial: '0x3da3358b4dc173', polynomial_degree: 53 },
  max_upload_bytes: 64 * 1024 * 1024,
  chunk_path_layout: 'chunks/<first 2 hex digits of SHA-256>/<SHA-256>',
}

const dc = (sizes, flags) => {
  let off = 0
  return sizes.map((size, i) => { const c = { offset: off, size, reused: flags ? flags[i] : null }; off += size; return c })
}
export const chunkingDemo = {
  seed: 7, original_size: 60000, modified_size: 60037, insert_at: 100, insert_len: 37, fixed_chunk_size: 16384,
  cdc: { scheme: 'content-defined (Rabin-style)', original_chunks: dc([20000, 20000, 20000]), modified_chunks: dc([20037, 20000, 20000], [false, true, true]),
    reused_chunks: 2, reused_bytes: 40000, reuse_percent: 66.6 },
  fixed: { scheme: 'fixed-size (16384 B)', original_chunks: dc([16384, 16384, 16384, 10848]), modified_chunks: dc([16384, 16384, 16384, 10885], [false, false, false, false]),
    reused_chunks: 0, reused_bytes: 0, reuse_percent: 0 },
  note: 'Computed on seeded generated data by the implemented chunker, not stored. Results describe this input only; CDC does not guarantee better reuse for every dataset.',
}

export const openapi = {
  paths: {
    '/api/files': {
      post: { summary: 'Upload a file', requestBody: { content: { 'multipart/form-data': { schema: { $ref: '#/components/schemas/Body_upload' } } } },
        responses: { 201: { description: 'Successful Response', content: { 'application/json': { schema: { $ref: '#/components/schemas/UploadResponse' } } } }, 413: { description: 'Payload Too Large' } } },
      get: { summary: 'List files', parameters: [{ name: 'limit', in: 'query', required: false, schema: { type: 'integer' } }],
        responses: { 200: { description: 'Successful Response', content: { 'application/json': { schema: { $ref: '#/components/schemas/FileListResponse' } } } } } },
    },
    '/api/integrity/audit': { post: { summary: 'Read-only consistency audit', responses: { 200: { description: 'OK' } } } },
    '/api/files/{file_id}': { delete: { summary: 'Delete a logical file', parameters: [{ name: 'file_id', in: 'path', required: true, schema: { type: 'string' } }], responses: { 200: { description: 'OK' }, 404: { description: 'Not Found' } } } },
    '/api/_secret': { get: { summary: 'not annotated', responses: { 200: { description: 'OK' } } } },
    '/openapi-internal': { get: { summary: 'ignored (not under /api)', responses: {} } },
  },
  components: { schemas: {
    Body_upload: { type: 'object', required: ['file'], properties: { file: { type: 'string', description: 'The file' } } },
    UploadResponse: { type: 'object', required: ['file_id', 'chunk_count'], properties: { file_id: { type: 'string' }, chunk_count: { type: 'integer' }, chunk_outcomes: { type: 'array', items: { $ref: '#/components/schemas/Outcome' } } } },
    Outcome: { type: 'object', properties: { decision: { type: 'string', enum: ['new', 'reused'] } } },
    FileListResponse: { type: 'object', properties: { total: { type: 'integer' } } },
  } },
}
export * from './fixtures5'
