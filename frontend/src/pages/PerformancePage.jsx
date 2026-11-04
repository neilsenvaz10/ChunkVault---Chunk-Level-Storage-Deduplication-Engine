import { useEffect, useMemo, useState } from 'react'
import { Card, ErrorNotice, StatusBadge } from '../components/ui'
import { useApi } from '../hooks/useApi'
import { api } from '../services/api'
import { formatBytes, formatDate, formatPercent, formatRate, formatRatio } from '../utils/format'
import Runner from './perf/Runner'
import { AggregateCharts, ComparisonView, PipelineView } from './perf/Views'

// Loads full results (not just summaries) for the saved benchmarks so charts use real measurements.
function useFullResults(list) {
  const [full, setFull] = useState({})
  const [error, setError] = useState(null)
  useEffect(() => {
    if (!list) return undefined
    let cancelled = false
    const missing = list.results.slice(0, 12).filter((r) => !full[r.job_id])
    if (!missing.length) return undefined
    Promise.all(missing.map((r) => api.getBenchmark(r.job_id))).then(
      (rows) => {
        if (cancelled) return
        setFull((f) => ({ ...f, ...Object.fromEntries(rows.filter((x) => x.result).map((x) => [x.job_id, x.result])) }))
      },
      (e) => !cancelled && setError(e),
    )
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list])
  return { full, error }
}

export default function PerformancePage() {
  const list = useApi((signal) => api.listBenchmarks(signal))
  const { full, error } = useFullResults(list.data)
  const [selected, setSelected] = useState(null)

  const results = list.data?.results ?? []
  const current = selected || results[0]?.job_id || null
  const result = current ? full[current] : null
  const pipelines = useMemo(() => results.map((r) => full[r.job_id]).filter((r) => r && r.kind === 'pipeline'), [results, full])
  const comparison = useMemo(() => results.map((r) => full[r.job_id]).find((r) => r && r.kind === 'chunking_comparison') || null, [results, full])

  return (
    <>
      <Card title="Measurement policy" aside={<StatusBadge tone="ok">real measurements only</StatusBadge>}>
        <p>
          Every number on this page comes from a benchmark that actually ran on this machine and was saved with its parameters. Nothing is preset: with no saved
          benchmark the charts are empty. Results describe this machine, this Python implementation and this pure-Python chunker; they are not a claim about any other system.
        </p>
      </Card>

      <Runner onFinished={(id) => { list.reload({ silent: true }); setSelected(id) }} />

      <Card title="Saved benchmark results">
        {list.error && <ErrorNotice error={list.error} onRetry={() => list.reload()} />}
        {list.loading && !list.data && <p className="muted" role="status">Loading…</p>}
        {list.data && results.length === 0 && <p className="empty">No benchmark has been run yet. Use “Run a benchmark” above; results will appear here.</p>}
        {results.length > 0 && (
          <div className="table-wrap">
            <table aria-label="Saved benchmarks">
              <thead><tr><th>Finished</th><th>Benchmark</th><th>Size</th><th>Iter.</th><th>Seed</th><th>Upload</th><th>Restore</th><th>Dedup</th><th>Savings</th><th /></tr></thead>
              <tbody>
                {results.map((r) => (
                  <tr key={r.job_id} className={r.job_id === current ? 'row-selected' : ''}>
                    <td>{formatDate(r.finished_at)}</td>
                    <td>{r.kind === 'pipeline' ? r.spec.workload.replace(/_/g, ' ') : 'CDC vs fixed'}</td>
                    <td>{formatBytes(r.spec.size_bytes)}</td><td>{r.spec.iterations}</td><td>{r.spec.seed}</td>
                    <td>{r.kind === 'pipeline' ? formatRate(r.upload_throughput_mib_s) : '—'}</td>
                    <td>{r.kind === 'pipeline' ? formatRate(r.restore_throughput_mib_s) : '—'}</td>
                    <td>{r.kind === 'pipeline' ? formatRatio(r.dedup_ratio) : '—'}</td>
                    <td>{r.kind === 'pipeline' ? formatPercent(r.savings_percent) : '—'}</td>
                    <td><button onClick={() => setSelected(r.job_id)} aria-pressed={r.job_id === current}>View</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {error && <ErrorNotice error={error} />}
      </Card>

      {current && !result && !error && <p className="muted" role="status">Loading the selected result…</p>}
      {result && result.kind === 'pipeline' && <PipelineView r={result} />}
      {result && result.kind === 'chunking_comparison' && <ComparisonView r={result} />}

      <AggregateCharts pipelines={pipelines} comparison={comparison} />

      {result && (
        <Card title="Measurement methodology">
          <ul>{result.methodology.map((m) => <li key={m}>{m}</li>)}</ul>
          <p className="muted">
            Reproduce a run: the same workload, size, seed and chunking parameters generate byte-identical input (<code>random.Random(seed)</code>);
            timings will differ from run to run and between machines.
          </p>
        </Card>
      )}
    </>
  )
}
