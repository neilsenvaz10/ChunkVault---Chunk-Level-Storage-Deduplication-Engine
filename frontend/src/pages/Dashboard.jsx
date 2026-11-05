import { Link } from 'react-router-dom'
import PipelineStrip from '../components/PipelineStrip'
import Icon from '../components/Icon'
import { AsyncState, Card, MetricTile, SourceChip, StatusBadge, StorageBar } from '../components/ui'
import { useApi, useHealth } from '../hooks/useApi'
import { api } from '../services/api'
import { formatBytes, formatInt, formatPercent, formatRatio } from '../utils/format'

function HealthCard() {
  const health = useHealth()
  return (
    <Card title="Backend health" aside={<button onClick={() => health.reload()}>Refresh</button>}>
      <AsyncState state={health} onRetry={() => health.reload()}>
        {(h) => (
          <table>
            <tbody>
              <tr><th>Status</th><td><StatusBadge tone={h.status === 'ok' ? 'ok' : 'warn'}>{h.status}</StatusBadge></td></tr>
              <tr><th>Service</th><td>{h.service} {h.version}</td></tr>
              <tr><th>Database connected</th><td>{String(h.database.connected)}</td></tr>
              <tr><th>Foreign keys enforced</th><td>{String(h.database.foreign_keys_enforced)}</td></tr>
              <tr><th>Schema version</th><td>{h.database.schema_version ?? 'n/a'}</td></tr>
            </tbody>
          </table>
        )}
      </AsyncState>
    </Card>
  )
}

const ACTIONS = [
  ['/files', 'Upload a file', 'Chunk, hash and store it', 'upload'],
  ['/database', 'Explore chunks', 'Browse the metadata tables', 'database'],
  ['/reliability', 'Run integrity audit', 'Read-only check of the store', 'reliability'],
  ['/architecture', 'View architecture', 'Interactive HLD and LLD', 'architecture'],
  ['/performance', 'Run benchmark', 'Measure on this machine', 'performance'],
]

function QuickActions() {
  return (
    <nav className="quick-actions" aria-label="Quick actions">
      {ACTIONS.map(([to, title, hint, icon]) => (
        <Link key={to + title} to={to} className="quick-action">
          <strong><Icon name={icon} /> {title}</strong>
          <small>{hint}</small>
        </Link>
      ))}
    </nav>
  )
}

// The three kinds of numbers in this application are kept apart on purpose.
function DataSources() {
  return (
    <Card title="Where the numbers in ChunkVault come from">
      <div className="grid-auto">
        <div>
          <SourceChip kind="measured" />
          <p><strong>Storage statistics</strong> (this page) are read live from the database and chunk store.</p>
        </div>
        <div>
          <SourceChip kind="measured" />
          <p><strong>Benchmark results</strong> are timings from runs on this machine, saved with their parameters.
            See <Link to="/performance">Performance Engineering</Link>.</p>
        </div>
        <div>
          <SourceChip kind="model" />
          <p><strong>Capacity simulation</strong> is a calculation from assumptions you enter — not a measurement.
            See <Link to="/simulation">Capacity Simulation</Link>.</p>
        </div>
      </div>
    </Card>
  )
}

export default function Dashboard() {
  const stats = useApi((signal) => api.storageStats(signal))
  return (
    <>
      <QuickActions />
      <div style={{ height: 'var(--s4)' }} />
      <Card title="Upload pipeline" aside={<Link to="/architecture">Open architecture →</Link>}>
        <PipelineStrip />
      </Card>

      <Card title="Storage overview" aside={<StatusBadge tone="ok">Actual measurements</StatusBadge>}>
        <AsyncState state={stats} onRetry={() => stats.reload()}>
          {(s) => (
            <>
              {s.total_files === 0 && (
                <p className="empty">
                  No files stored yet. <Link to="/files">Upload a file</Link> to see deduplication results here.
                </p>
              )}
              <div className="tiles">
                <MetricTile label="Stored files" value={formatInt(s.total_files)} />
                <MetricTile label="Logical storage" value={formatBytes(s.logical_bytes)} hint={`${formatInt(s.logical_bytes)} bytes`} />
                <MetricTile label="Unique chunk storage" value={formatBytes(s.unique_chunk_bytes)} hint={`${formatInt(s.unique_chunk_bytes)} bytes`} />
                <MetricTile label="Unique chunks" value={formatInt(s.unique_chunks)} />
                <MetricTile label="Chunk references" value={formatInt(s.total_chunk_references)} hint={`${formatInt(s.reused_chunk_references)} reused`} />
                <MetricTile label="Storage saved" value={formatPercent(s.storage_savings_percent)} tone="good" />
                <MetricTile label="Deduplication ratio" value={formatRatio(s.deduplication_ratio)} tone="good" />
                <MetricTile label="Integrity" value="not audited" hint="No audit has run in this view; use Run integrity audit." />
              </div>
              <h3>Logical vs unique chunk payload</h3>
              <StorageBar logical={s.logical_bytes} unique={s.unique_chunk_bytes} formatBytes={formatBytes} />
              <details className="disclosure">
              <summary>How these figures are defined</summary>
              <dl className="defs">
                <dt>Logical storage</dt>
                <dd>Total size of all stored file versions, as if every file were stored whole.</dd>
                <dt>Unique chunk storage</dt>
                <dd>Total payload bytes of the distinct chunks actually kept (each stored once, whatever its reference count).</dd>
                <dt>Storage savings</dt>
                <dd>
                  <code>100 × (logical − unique) / logical</code>: the reduction achieved by chunk reuse. It counts payload only and
                  excludes SQLite metadata and filesystem overhead.
                </dd>
              </dl>
              </details>
              <h3>Actual footprint (not part of the savings figure)</h3>
              <p className="muted">
                SQLite files {formatBytes(s.database_bytes)} · {formatInt(s.chunk_files)} chunk files totalling{' '}
                {formatBytes(s.chunk_store_bytes)} (file sizes, excluding filesystem block overhead) · staging{' '}
                {formatBytes(s.staging_bytes)}
                {s.unreferenced_chunks > 0 &&
                  ` · ${s.unreferenced_chunks} unreferenced chunk(s) (${formatBytes(s.unreferenced_chunk_bytes)}) awaiting reclamation`}
              </p>
            </>
          )}
        </AsyncState>
      </Card>

      <DataSources />
      <HealthCard />
    </>
  )
}
