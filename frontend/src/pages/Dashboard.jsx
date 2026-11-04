import Card from '../components/Card'
import StatusBadge from '../components/StatusBadge'
import { useApi, useHealth } from '../hooks/useApi'
import { getPlannedEndpoints } from '../services/api'

function HealthCard() {
  const { data, error, loading, reload } = useHealth()
  return (
    <Card title="Backend health" aside={<button onClick={reload}>Refresh</button>}>
      {loading && <p>Checking…</p>}
      {error && (
        <p>
          <StatusBadge tone="error">Unreachable</StatusBadge> {error.message}. Start the
          backend (see README).
        </p>
      )}
      {data && (
        <table>
          <tbody>
            <tr><th>Status</th><td><StatusBadge tone={data.status === 'ok' ? 'ok' : 'warn'}>{data.status}</StatusBadge></td></tr>
            <tr><th>Service</th><td>{data.service} {data.version}</td></tr>
            <tr><th>Database connected</th><td>{String(data.database.connected)}</td></tr>
            <tr><th>Foreign keys enforced</th><td>{String(data.database.foreign_keys_enforced)}</td></tr>
            <tr><th>Schema version</th><td>{data.database.schema_version ?? 'n/a'}</td></tr>
          </tbody>
        </table>
      )}
    </Card>
  )
}

function PlannedCard() {
  const { data, error, loading } = useApi(getPlannedEndpoints)
  return (
    <Card title="API surface" aside={<StatusBadge tone="warn">None of these work yet</StatusBadge>}>
      <p>Endpoints below are designed but <strong>not implemented</strong>; the backend does not serve them.</p>
      {loading && <p>Loading…</p>}
      {error && <p>Unavailable: {error.message}</p>}
      {data && (
        <table>
          <thead><tr><th>Method</th><th>Path</th><th>Purpose</th><th>Phase</th></tr></thead>
          <tbody>
            {data.endpoints.map((e) => (
              <tr key={`${e.method} ${e.path}`}>
                <td><code>{e.method}</code></td>
                <td><code>{e.path}</code></td>
                <td>{e.summary}</td>
                <td>{e.phase}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

export default function Dashboard() {
  return (
    <>
      <h1>Dashboard</h1>
      <Card title="Storage statistics" aside={<StatusBadge tone="warn">Not implemented</StatusBadge>}>
        <p>
          Upload counts, deduplication ratio and storage savings will appear here once uploads
          exist (Phase 3). No figures are shown until they can be measured.
        </p>
      </Card>
      <HealthCard />
      <PlannedCard />
    </>
  )
}
