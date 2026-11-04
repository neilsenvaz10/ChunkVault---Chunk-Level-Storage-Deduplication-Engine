import { AsyncState, Card, StatusBadge } from '../components/ui'
import { useApi } from '../hooks/useApi'
import { api } from '../services/api'

function PlannedEndpoints({ filter }) {
  const planned = useApi((signal) => api.plannedEndpoints(signal))
  return (
    <Card title="Backend status" aside={<StatusBadge tone="warn">planned, not served</StatusBadge>}>
      <AsyncState state={planned} onRetry={() => planned.reload()}>
        {(d) => {
          const rows = d.endpoints.filter(filter)
          return rows.length === 0 ? <p className="empty">No matching endpoint registered.</p> : (
            <table>
              <thead><tr><th>Method</th><th>Path</th><th>Purpose</th><th>Status</th></tr></thead>
              <tbody>
                {rows.map((e) => (
                  <tr key={e.path}><td><code>{e.method}</code></td><td><code>{e.path}</code></td><td>{e.summary}</td><td>{e.status} (phase {e.phase})</td></tr>
                ))}
              </tbody>
            </table>
          )
        }}
      </AsyncState>
    </Card>
  )
}

export function PerformancePage() {
  return (
    <>
      <Card title="Status" aside={<StatusBadge tone="warn">No measurements exist</StatusBadge>}>
        <p>
          No benchmark has been run, so this page shows <strong>no numbers</strong>. Throughput, chunk-size distributions or
          speed-ups would only appear here after the benchmark runner (Phase 6) has measured them on this machine.
        </p>
      </Card>
      <Card title="Planned metrics">
        <ul>
          <li><strong>Chunking throughput</strong> (MiB/s) of the pure-Python Rabin chunker.</li>
          <li><strong>End-to-end upload and restore time</strong> per MiB, including hashing and SQLite commits.</li>
          <li><strong>Chunk-size distribution</strong> against the 4 / 16 / 128 KiB design parameters.</li>
          <li><strong>Deduplication ratio</strong> on controlled datasets (identical, shifted, edited in place, unrelated).</li>
          <li><strong>CDC vs fixed-size chunking</strong>: reuse after insertion, deletion and in-place modification.</li>
          <li><strong>CPU cost of savings</strong>: extra time spent chunking and hashing versus bytes saved.</li>
        </ul>
      </Card>
      <Card title="Planned methodology">
        <ul>
          <li>Seeded, reproducible generated datasets; the seed is recorded with every result.</li>
          <li>Each scenario runs against an isolated temporary store, never the real one.</li>
          <li>Monotonic clock, several repetitions, median and spread reported rather than a single run.</li>
          <li>Results are stored as JSON with their parameters and the machine description.</li>
          <li>All results are labelled as measured on one machine; no distributed claim is made.</li>
        </ul>
      </Card>
      <PlannedEndpoints filter={(e) => e.path.includes('benchmark')} />
    </>
  )
}

export function SimulationPage() {
  return (
    <>
      <Card title="Status" aside={<StatusBadge tone="warn">Not implemented</StatusBadge>}>
        <p>
          The capacity simulator is planned for Phase 7. It will be a <strong>hypothetical workload model</strong>: it will
          compute what <em>would</em> happen under assumptions the user states. Its output will never be presented as
          measured performance of this prototype, and no simulated figure is shown today.
        </p>
      </Card>
      <Card title="What the model will do">
        <ul>
          <li>Inputs: logical data volume, assumed deduplication ratio, average chunk size, metadata bytes per chunk, growth rate.</li>
          <li><code>physical data = logical data ÷ assumed ratio</code></li>
          <li><code>chunk count = physical data ÷ average chunk size</code></li>
          <li><code>metadata size = chunk count × metadata bytes per chunk</code></li>
          <li>Every result will be tagged &ldquo;model output&rdquo; and show the assumptions it came from.</li>
        </ul>
        <p className="muted">
          ChunkVault is a single-machine, sequential prototype. A simulation of larger scale would be analytical only; the
          system does not implement distributed storage.
        </p>
      </Card>
      <PlannedEndpoints filter={(e) => e.path.includes('simulation')} />
    </>
  )
}
