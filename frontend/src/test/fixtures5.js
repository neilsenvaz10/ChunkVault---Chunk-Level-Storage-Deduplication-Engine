// Phase 5 API fixtures (hand-written for component tests only).

const steps = (labels, faultStep) => labels.map((label, i) => ({ label, phase: i < faultStep ? 'before_fault' : i === faultStep ? 'fault' : 'after_fault' }))

const base = (id, title, operation, labels, faultStep, kind = 'crash') => ({
  id, title, operation, fault_point: `fault point of ${id}`, kind, description: `${title} description`, steps: labels, fault_step: faultStep,
  expectations: [
    { key: 'before.orphan_chunk_files', label: 'Orphan chunk files', expected: '> 0' },
    { key: 'verification.status', label: 'Recovery verification (computed by the backend)', expected: '= verified_clean' },
  ],
})

export const scenarios = {
  enabled: true,
  safety: ['Every experiment runs against its own temporary store and removes it afterwards.', 'Only the fixed built-in scenarios can be selected; no custom fault can be submitted.'],
  scenarios: [
    base('crash_before_metadata_commit', 'Failure after chunk creation but before metadata commit', 'Upload',
      ['Upload started', 'Chunks generated', 'Chunks persisted', 'Fault injected', 'Recovery', 'Integrity check'], 3),
    base('restore_failure', 'Failure during file restoration', 'Restore', ['Upload committed', 'Chunk damaged', 'Restore attempted', 'Recovery', 'Integrity check'], 1, 'exception'),
    base('cdc_failure', 'Failure during CDC chunk generation', 'Upload', ['Upload started', 'Some chunks generated', 'Fault injected', 'Engine cleanup', 'Integrity check'], 2, 'exception'),
  ],
}
export const scenariosDisabled = { ...scenarios, enabled: false }

const inv = (id, title, holds = true, detail = 'ok') => ({ id, title, holds, detail })
const ALL_HOLD = ['I1', 'I2', 'I3', 'I4', 'I5', 'I6', 'I7', 'I8'].map((id, i) => inv(id, `Invariant ${i + 1} title`, true, `evidence ${id}`))

const snap = (o = {}) => ({
  files: [{ filename: 'control.bin', status: 'complete' }, { filename: 'victim.bin', status: 'uploading' }],
  operations: [{ type: 'upload', status: 'succeeded' }, { type: 'upload', status: 'running' }],
  complete_files: 1, uploading_files: 1, failed_files: 0, running_operations: 1, chunk_rows: 4, manifest_rows: 4, ref_count_mismatches: 0,
  chunk_files: 5, orphan_chunk_files: 1, missing_chunk_files: 0, staging_files: 1, staging_names: ['upload-0123456789abcdef0123456789abcdef.part'],
  victim_status: 'uploading', victim_visible: true, victim_manifest_rows: 0,
  audit: { healthy: false, files_checked: 2, chunks_checked: 4, findings: 4, by_kind: { orphan_chunk_file: 1, incomplete_file: 1 } }, ...o,
})

export const experiment = (id) => {
  const common = {
    scenario_id: id, operation: 'Upload', fault_kind: 'crash', description: 'desc', baseline: { control_chunks: 4 },
    fault: { fault_raised: true, exception: 'SimulatedCrash (process death)' },
    second_recovery_actions: 0, retry: null, within_time_bound: true, duration_ms: 210.5,
    isolation: { temporary_store_used: true, application_data_touched: false, temporary_store_removed: true },
    restores: [{ filename: 'control.bin', outcome: 'restored', matches_original: true }],
  }
  if (id === 'restore_failure') {
    return {
      ...common, title: 'Failure during file restoration', operation: 'Restore', fault_kind: 'exception', fault_point: 'a stored chunk is damaged',
      steps: steps(['Upload committed', 'Chunk damaged', 'Restore attempted', 'Recovery', 'Integrity check'], 1),
      fault: { fault_raised: true, exception: 'ChunkCorruptionError', code: 'chunk_corrupt', partial_output_left: false },
      before_recovery: snap({ victim_status: 'complete', victim_visible: true, orphan_chunk_files: 0, staging_files: 0, staging_names: [], running_operations: 0,
        files: [{ filename: 'control.bin', status: 'complete' }, { filename: 'victim.bin', status: 'complete' }],
        audit: { healthy: false, files_checked: 2, chunks_checked: 5, findings: 1, by_kind: { corrupt_chunk: 1 } } }),
      recovery_actions: [], after_recovery: snap({ orphan_chunk_files: 0, staging_files: 0, staging_names: [], audit: { healthy: false, files_checked: 2, chunks_checked: 5, findings: 1, by_kind: { corrupt_chunk: 1 } } }),
      restores: [{ filename: 'control.bin', outcome: 'restored', matches_original: true }, { filename: 'victim.bin', outcome: 'refused', code: 'chunk_corrupt', partial_output_left: false }],
      invariants: ALL_HOLD.map((i) => (i.id === 'I1' || i.id === 'I2' ? { ...i, holds: false, detail: '1 stored chunk(s) do not match their SHA-256 (detected; not repairable)' } : i)),
      recovery_status: 'verified_with_unrepairable_damage', all_expectations_met: true,
      expectations: [{ key: 'fault.code', label: 'Restore error code', expected: '= chunk_corrupt', observed: 'chunk_corrupt', matches: true }],
    }
  }
  if (id === 'cdc_failure') {
    return {
      ...common, title: 'Failure during CDC chunk generation', fault_kind: 'exception', fault_point: 'chunker raises',
      steps: steps(['Upload started', 'Some chunks generated', 'Fault injected', 'Engine cleanup', 'Integrity check'], 2),
      before_recovery: snap({ victim_status: 'failed', orphan_chunk_files: 0, staging_files: 0, staging_names: [], running_operations: 0 }),
      recovery_actions: [], after_recovery: snap({ orphan_chunk_files: 3, audit: { healthy: false, files_checked: 2, chunks_checked: 4, findings: 3, by_kind: { orphan_chunk_file: 3 } } }),
      invariants: ALL_HOLD.map((i) => (i.id === 'I6' ? { ...i, holds: false, detail: '2 required chunk file(s) missing after recovery' } : i)),
      recovery_status: 'not_recovered', all_expectations_met: false,
      expectations: [{ key: 'after.orphan_chunk_files', label: 'Orphan chunk files after recovery', expected: '= 0', observed: 3, matches: false }],
    }
  }
  return {
    ...common, title: 'Failure after chunk creation but before metadata commit', fault_point: 'all chunks published; process dies before T2',
    steps: steps(['Upload started', 'Chunks generated', 'Chunks persisted', 'Fault injected', 'Recovery', 'Integrity check'], 3),
    before_recovery: snap(),
    recovery_actions: [{ action: 'delete_staging_file', subject: 'upload-0123456', detail: 'removed leftover staging file' }, { action: 'delete_orphan_chunk', subject: 'd54244b3127507bc', detail: 'no metadata row references it' }],
    after_recovery: snap({ files: [{ filename: 'control.bin', status: 'complete' }, { filename: 'victim.bin', status: 'failed' }], victim_status: 'failed', uploading_files: 0, failed_files: 1, running_operations: 0, orphan_chunk_files: 0, staging_files: 0, staging_names: [], audit: { healthy: true, files_checked: 2, chunks_checked: 4, findings: 0, by_kind: {} } }),
    invariants: ALL_HOLD, recovery_status: 'verified_clean', all_expectations_met: true,
    expectations: [
      { key: 'before.orphan_chunk_files', label: 'Orphan chunk files', expected: '> 0', observed: 1, matches: true },
      { key: 'verification.status', label: 'Recovery verification (computed by the backend)', expected: '= verified_clean', observed: 'verified_clean', matches: true },
    ],
  }
}

export const catalog = {
  workloads: [
    { id: 'small', description: 'One small random file', uploads: 1, preset_size_bytes: 65536 },
    { id: 'identical', description: 'The same file uploaded twice', uploads: 2, preset_size_bytes: null },
    { id: 'inserted_bytes', description: 'A file, then a copy with bytes inserted near the beginning', uploads: 2, preset_size_bytes: null },
  ],
  comparison_cases: [{ id: 'original', title: 'Original file', note: 'n' }],
  limits: { min_size_bytes: 16384, max_size_bytes: 4194304, max_iterations: 10, max_bytes_processed: 50331648, max_runtime_seconds: 240 },
  methodology: ['Stage timings are exclusive.'],
}

const stat = (m) => ({ n: 3, mean: m, median: m, min: m * 0.9, max: m * 1.1, stdev: m * 0.05 })
const upload = (name, size, total, extra = {}) => ({
  name, size_bytes: size, total_seconds: total, throughput_mib_s: size / 1048576 / total, stages_seconds: { cdc: total * 0.5, metadata: total * 0.2, other: total * 0.3 },
  stages_percent: { cdc: 50, metadata: 20, other: 30 }, chunk_count: 8, new_chunks: 8, reused_chunks: 0, bytes_written: size, sha256_matches_source: true, ...extra,
})

export const pipelineResult = (over = {}) => ({
  job_id: 'aaaaaaaaaaaa', status: 'done', kind: 'pipeline', finished_at: '2026-01-01T10:00:00+00:00',
  spec: { kind: 'pipeline', workload: 'identical', size_bytes: 131072, iterations: 2, seed: 7, fixed_chunk_bytes: null },
  environment: { python: '3.14.0', implementation: 'CPython', platform: 'TestOS', machine: 'x86_64', cpu_count: 8, sqlite_version: '3.45.0', chunker: 'pure-Python Rabin-style CDC',
    cdc: { min_chunk_size: 4096, avg_chunk_size: 16384, max_chunk_size: 131072, window_size: 64, boundary_divisor: 12288 } },
  methodology: ['Stage timings are exclusive.', 'Datasets are generated from a seed.'],
  iterations: [1, 2].map((i) => ({
    iteration: i,
    uploads: [upload('v1', 131072, 0.2), upload('v2-identical', 131072, 0.1, { new_chunks: 0, reused_chunks: 8, bytes_written: 0 })],
    restores: [{ name: 'v1', size_bytes: 131072, total_seconds: 0.02, throughput_mib_s: 6.25, stages_seconds: { chunk_read: 0.01, other: 0.01 }, stages_percent: { chunk_read: 50, other: 50 }, integrity_verified: true },
      { name: 'v2-identical', size_bytes: 131072, total_seconds: 0.02, throughput_mib_s: 6.25, stages_seconds: { chunk_read: 0.01, other: 0.01 }, stages_percent: { chunk_read: 50, other: 50 }, integrity_verified: true }],
    storage: { logical_bytes: 262144, unique_chunk_bytes: 131072, dedup_ratio: 2, savings_percent: 50, chunks_generated: 16, unique_chunks: 8, reused_chunks: 8, avg_chunk_bytes: 16384,
      database_bytes: 73728, database_overhead_bytes: 0, manifest_rows: 16, overhead_bytes_per_manifest_row: 0, chunk_files: 8, chunk_store_bytes: 131072 },
  })),
  summary: {
    upload_total_seconds: stat(0.3), upload_throughput_mib_s: stat(0.83), restore_total_seconds: stat(0.04), restore_throughput_mib_s: stat(6.25),
    dedup_ratio: stat(2), savings_percent: stat(50), upload_stage_seconds: { cdc: stat(0.15), metadata: stat(0.06), other: stat(0.09) },
    integrity_all_verified: true, total_execution_seconds: 0.68,
  }, ...over,
})

export const comparisonResult = () => ({
  job_id: 'bbbbbbbbbbbb', status: 'done', kind: 'chunking_comparison', finished_at: '2026-01-01T11:00:00+00:00', fixed_chunk_bytes: 16384,
  spec: { kind: 'chunking_comparison', workload: null, size_bytes: 196608, iterations: 1, seed: 11, fixed_chunk_bytes: null },
  environment: pipelineResult().environment, methodology: ['Both schemes chunk the same bytes.'],
  cases: [
    { id: 'insert_start', title: 'Small insertion near the beginning', note: '61 bytes inserted', versions: [196608, 196669], savings_difference_points: 47, verdict: 'CDC saves more',
      cdc: { chunk_count: 14, unique_chunks: 8, avg_chunk_bytes: 28000, reused_chunks: 6, reused_bytes: 180000, logical_bytes: 393277, unique_bytes: 213277, savings_percent: 47, processing_seconds: stat(0.3) },
      fixed: { chunk_count: 25, unique_chunks: 25, avg_chunk_bytes: 15731, reused_chunks: 0, reused_bytes: 0, logical_bytes: 393277, unique_bytes: 393277, savings_percent: 0, processing_seconds: stat(0.01) } },
    { id: 'modify_middle', title: 'Modified content in the middle', note: 'in place', versions: [196608, 196608], savings_difference_points: -15, verdict: 'fixed-size saves more',
      cdc: { chunk_count: 14, unique_chunks: 8, avg_chunk_bytes: 28000, reused_chunks: 6, reused_bytes: 112000, logical_bytes: 393216, unique_bytes: 281216, savings_percent: 28.6, processing_seconds: stat(0.3) },
      fixed: { chunk_count: 25, unique_chunks: 14, avg_chunk_bytes: 15731, reused_chunks: 11, reused_bytes: 172000, logical_bytes: 393216, unique_bytes: 221216, savings_percent: 43.8, processing_seconds: stat(0.01) } },
  ],
})

const MIB = 1048576
export const simulation = (b) => {
  const n = b.file_count
  const logical = n * b.avg_file_size_bytes
  const dup = b.duplicate_percent / 100
  const phys = logical * (1 - dup)
  const meta = n * b.metadata_bytes_per_file
  const pt = (files) => ({ files, logical_bytes: files * b.avg_file_size_bytes, physical_bytes: files * b.avg_file_size_bytes * (1 - dup), metadata_bytes: files * b.metadata_bytes_per_file,
    total_bytes: files * b.avg_file_size_bytes * (1 - dup) + files * b.metadata_bytes_per_file, savings_percent: b.duplicate_percent, dedup_ratio: dup < 1 ? 1 / (1 - dup) : null })
  return {
    model: 'mathematical model - not a measurement or load test',
    inputs: { ...b, avg_chunks_per_file_used: b.avg_chunks_per_file ?? 320, duplicate_fraction: dup },
    outputs: { avg_chunk_bytes: 16384, chunk_references: n * 320, unique_chunks: n * 320 * (1 - dup), logical_bytes: logical, physical_chunk_bytes: phys, savings_percent: b.duplicate_percent,
      dedup_ratio: dup < 1 ? 1 / (1 - dup) : null, metadata_bytes: meta, metadata_breakdown: { files: meta, manifest_rows: 0, unique_chunks: 0 }, total_disk_bytes: phys + meta,
      metadata_percent_of_total: (100 * meta) / (phys + meta || 1) },
    formulas: ['logical storage L = files x average file size', 'physical chunk storage P = U x a'],
    assumptions: ['The duplicate fraction applies to chunk references and stays constant as data grows.'],
    file_count_sweep: [1, 2, 3].map((k) => pt((n * k) / 3)), dedup_sweep: [0, 50, 100].map((p) => ({ duplicate_percent: p, ...pt(n), savings_percent: p })),
    growth: [0, 1, 2].map((m) => ({ month: m, ...pt(n * (1 + m / 10)) })),
    growth_summary: { months: b.horizon_months, retention_months: b.retention_months, final_active_files: n * 1.2, final_total_bytes: phys * 1.2, peak_total_bytes: phys * 1.2 },
  }
}
export { MIB }
