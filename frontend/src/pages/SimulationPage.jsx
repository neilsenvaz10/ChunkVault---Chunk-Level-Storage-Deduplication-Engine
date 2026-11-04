import { useEffect, useState } from 'react'
import Chart from '../components/Chart'
import { Card, ErrorNotice, MetricTile, StatusBadge } from '../components/ui'
import { api } from '../services/api'
import { formatBytes, formatCompact, formatInt, formatPercent, formatRatio } from '../utils/format'

const MIB = 1024 * 1024
export const DEFAULTS = {
  file_count: 100000, avg_file_mib: 5, avg_chunks_per_file: '', duplicate_percent: 30,
  metadata_bytes_per_file: 300, metadata_bytes_per_unique_chunk: 150, metadata_bytes_per_manifest_row: 80,
  monthly_growth_percent: 5, retention_months: 0, horizon_months: 24,
}

const FIELDS = [
  ['file_count', 'Number of files', 1, 1e12, 1, 'files'],
  ['avg_file_mib', 'Average file size', 0.001, 1e6, 'any', 'MiB'],
  ['avg_chunks_per_file', 'Average chunks per file', 1, 1e9, 'any', 'chunks (blank = size ÷ 16 KiB)'],
  ['duplicate_percent', 'Duplicate-content percentage', 0, 100, 'any', '% of chunk references'],
  ['metadata_bytes_per_file', 'Metadata per file (estimate)', 0, 1e6, 1, 'bytes'],
  ['metadata_bytes_per_unique_chunk', 'Metadata per unique chunk (estimate)', 0, 1e6, 1, 'bytes'],
  ['metadata_bytes_per_manifest_row', 'Metadata per chunk reference (estimate)', 0, 1e6, 1, 'bytes'],
  ['monthly_growth_percent', 'Storage growth rate', 0, 50, 'any', '% of files per month, compound'],
  ['retention_months', 'Retention period', 0, 120, 1, 'months (0 = keep forever)'],
  ['horizon_months', 'Projection horizon', 1, 120, 1, 'months'],
]

export const toRequest = (f) => ({
  file_count: Number(f.file_count),
  avg_file_size_bytes: Math.max(1, Math.round(Number(f.avg_file_mib) * MIB)),
  avg_chunks_per_file: f.avg_chunks_per_file === '' ? null : Number(f.avg_chunks_per_file),
  duplicate_percent: Number(f.duplicate_percent),
  metadata_bytes_per_file: Number(f.metadata_bytes_per_file),
  metadata_bytes_per_unique_chunk: Number(f.metadata_bytes_per_unique_chunk),
  metadata_bytes_per_manifest_row: Number(f.metadata_bytes_per_manifest_row),
  monthly_growth_percent: Number(f.monthly_growth_percent),
  retention_months: Number(f.retention_months),
  horizon_months: Number(f.horizon_months),
})

const LIMITATIONS = [
  ['SQLite write concurrency', 'SQLite allows one writer at a time and ChunkVault adds its own single-writer lock, so write throughput does not grow with more clients or CPUs. Reads scale better than writes.'],
  ['Single-machine filesystem storage', 'All chunk files sit on one machine and one disk: no replication, a hardware failure loses everything, and capacity is bounded by that disk. The model\'s "total disk" is exactly what must fit on it.'],
  ['Local disk capacity', 'Total disk grows with unique data plus metadata, and filesystem block rounding on millions of small chunk files adds overhead the model does not include.'],
  ['Metadata database growth', 'One row per file, per unique chunk and per chunk reference. The manifest grows with every file even when payload is fully deduplicated, so metadata can dominate at high duplication (see "metadata % of total").'],
  ['Chunk lookup performance', 'Deduplication is a primary-key lookup in SQLite plus a disk read to verify the existing chunk. That is fast at prototype scale; very large indexes stop fitting in memory and lookups become disk-bound.'],
  ['Hash computation overhead', 'Every byte is fingerprinted by the pure-Python chunker and hashed with SHA-256, and reused chunks are re-verified. This CPU cost is not in the model; the Performance page measures it on this machine.'],
  ['Garbage collection and orphan cleanup', 'Deleting a file reclaims only chunks no other file references, and crash leftovers need an explicit reconciliation run. The model assumes perfect, immediate reclamation.'],
  ['Backup and disaster recovery', 'There is no replication or backup tooling, and a deduplicated store concentrates risk: one damaged shared chunk affects every file that uses it. Backups would have to capture the database and the chunk store consistently.'],
]
const FUTURE = [
  ['PostgreSQL (or another networked SQL database)', 'concurrent writers, replication and richer indexing; costs an extra service to operate and a new transaction/consistency story with the chunk store.'],
  ['Object storage for chunks', 'replicated, effectively unbounded capacity keyed by the SHA-256 that is already the address; loses atomic rename and local fsync semantics, so the publication protocol changes.'],
  ['Asynchronous workers', 'chunk and hash files in parallel and queue uploads; requires concurrency control around the shared chunk index and a job/failure model.'],
  ['Caching', 'keep recently verified chunk digests or the hot part of the chunk index in memory to cut lookup and re-verification cost; adds invalidation concerns.'],
  ['Distributed storage', 'shard metadata and chunks by digest across nodes with replication or erasure coding; a different system with consensus, partial failure and rebalancing — conceptual only here.'],
]

function useSimulation(form) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  useEffect(() => {
    const controller = new AbortController()
    setState((s) => ({ ...s, loading: true }))
    const timer = setTimeout(async () => {
      try {
        const data = await api.simulate(toRequest(form), controller.signal)
        setState({ data, error: null, loading: false })
      } catch (error) {
        if (error?.name !== 'AbortError') setState((s) => ({ data: null, error, loading: false }))
      }
    }, 300)
    return () => { clearTimeout(timer); controller.abort() }
  }, [form])
  return state
}

export default function SimulationPage() {
  const [form, setForm] = useState(DEFAULTS)
  const sim = useSimulation(form)
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))
  const d = sim.data
  const o = d?.outputs

  return (
    <>
      <Card title="What this is" aside={<StatusBadge tone="warn">mathematical model — not a measurement</StatusBadge>}>
        <p>
          This simulator evaluates formulas for the assumptions you enter. It does <strong>not</strong> run a load test, read your stored data or use benchmark
          results, and nothing here is measured performance of ChunkVault. Every output is labelled with the assumptions it came from.
        </p>
      </Card>

      <Card title="Assumptions (inputs)">
        <form className="sim-form" onSubmit={(e) => e.preventDefault()} aria-label="Simulation assumptions">
          {FIELDS.map(([key, label, min, max, step, unit]) => (
            <label key={key}>{label}
              <span className="input-unit">
                <input type="number" min={min} max={max} step={step} value={form[key]} onChange={set(key)} aria-label={label} />
                <small className="muted">{unit}</small>
              </span>
            </label>
          ))}
          <button type="button" onClick={() => setForm(DEFAULTS)}>Reset to defaults</button>
        </form>
        <p className="muted">The default metadata sizes are rough guesses. The Performance page measures the real database growth per manifest row on this machine.</p>
      </Card>

      {sim.error && <ErrorNotice error={sim.error} />}
      {sim.loading && !d && <p className="muted" role="status">Calculating…</p>}

      {d && (
        <>
          <Card title="Estimated results (model output)" aside={sim.loading ? <span className="muted">updating…</span> : null}>
            <div className="tiles">
              <MetricTile label="Total logical storage" value={formatBytes(o.logical_bytes)} />
              <MetricTile label="Estimated physical chunk storage" value={formatBytes(o.physical_chunk_bytes)} />
              <MetricTile label="Estimated storage savings" value={formatPercent(o.savings_percent)} tone="good" hint={`dedup ratio ${formatRatio(o.dedup_ratio)}`} />
              <MetricTile label="Estimated unique chunks" value={formatCompact(o.unique_chunks)} hint={`of ${formatCompact(o.chunk_references)} references`} />
              <MetricTile label="Estimated metadata overhead" value={formatBytes(o.metadata_bytes)} hint={o.metadata_percent_of_total === null ? undefined : `${o.metadata_percent_of_total.toFixed(2)}% of total`} />
              <MetricTile label="Estimated total disk usage" value={formatBytes(o.total_disk_bytes)} hint="chunks + metadata" />
              <MetricTile label="After the projection horizon" value={formatBytes(d.growth_summary.final_total_bytes)} hint={`${formatCompact(d.growth_summary.final_active_files)} active files at month ${d.growth_summary.months}`} />
            </div>
            <table aria-label="Metadata breakdown">
              <tbody>
                <tr><th>Metadata: files</th><td>{formatBytes(o.metadata_breakdown.files)}</td></tr>
                <tr><th>Metadata: manifest rows</th><td>{formatBytes(o.metadata_breakdown.manifest_rows)}</td></tr>
                <tr><th>Metadata: chunk rows</th><td>{formatBytes(o.metadata_breakdown.unique_chunks)}</td></tr>
                <tr><th>Average chunk size</th><td>{formatBytes(Math.round(o.avg_chunk_bytes))} ({formatInt(d.inputs.avg_chunks_per_file_used)} chunks per file)</td></tr>
              </tbody>
            </table>
            {o.dedup_ratio === null && <p className="notice">At 100% duplicates there are no physical chunks, so the deduplication ratio is undefined (shown as n/a).</p>}
          </Card>

          <Card title="Formulas and assumptions used">
            <ul>{d.formulas.map((f) => <li key={f}><code>{f}</code></li>)}</ul>
            <h3>Assumptions</h3>
            <ul>{d.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
          </Card>

          <Card title="Scalability charts (all projected from the assumptions)">
            <Chart title="File count vs storage consumption" xLabel="number of files" yLabel="bytes" formatX={formatCompact} formatY={formatBytes}
                   series={[{ name: 'Logical', points: d.file_count_sweep.map((p) => ({ x: p.files, y: p.logical_bytes })) },
                     { name: 'Estimated physical chunks', points: d.file_count_sweep.map((p) => ({ x: p.files, y: p.physical_bytes })) },
                     { name: 'Estimated total disk', points: d.file_count_sweep.map((p) => ({ x: p.files, y: p.total_bytes })) }]} />
            <Chart title="Deduplication rate vs storage savings" xLabel="duplicate content (%)" yLabel="storage savings (%)" formatX={(v) => `${v}%`} formatY={(v) => `${v.toFixed(0)}%`}
                   series={[{ name: 'Estimated savings', points: d.dedup_sweep.map((p) => ({ x: p.duplicate_percent, y: p.savings_percent })) }]} />
            <Chart title="File count vs metadata overhead" xLabel="number of files" yLabel="metadata bytes" formatX={formatCompact} formatY={formatBytes}
                   series={[{ name: 'Estimated metadata', points: d.file_count_sweep.map((p) => ({ x: p.files, y: p.metadata_bytes })) }]} />
            <Chart title="Storage growth over time" xLabel="month" yLabel="bytes" formatX={(v) => `m${v}`} formatY={formatBytes}
                   series={[{ name: 'Logical', points: d.growth.map((p) => ({ x: p.month, y: p.logical_bytes })) },
                     { name: 'Estimated total disk', points: d.growth.map((p) => ({ x: p.month, y: p.total_bytes })) }]} />
            <Chart title="Logical vs estimated physical storage" type="bar" xLabel="storage" yLabel="bytes" formatY={formatBytes}
                   series={[{ name: 'Logical', points: [{ x: 'now', y: o.logical_bytes }] }, { name: 'Estimated physical chunks', points: [{ x: 'now', y: o.physical_chunk_bytes }] },
                     { name: 'Estimated total disk', points: [{ x: 'now', y: o.total_disk_bytes }] }]} />
          </Card>
        </>
      )}

      <Card title="Architectural limitations at scale">
        <dl className="defs">
          {LIMITATIONS.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
        </dl>
      </Card>
      <Card title="Possible future improvements (conceptual — not implemented)">
        <ul>{FUTURE.map(([k, v]) => <li key={k}><strong>{k}:</strong> {v}</li>)}</ul>
        <p className="muted">ChunkVault is a single-machine, sequential prototype. None of the options above is implemented, and no distributed capability is claimed.</p>
      </Card>
    </>
  )
}
