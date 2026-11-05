import { useEffect, useRef, useState } from 'react'
import { AsyncState, Card, ErrorNotice } from '../../components/ui'
import { useApi } from '../../hooks/useApi'
import { api } from '../../services/api'
import { formatBytes } from '../../utils/format'

const POLL_MS = 1000

export default function Runner({ onFinished }) {
  const catalog = useApi((signal) => api.benchmarkCatalog(signal))
  const [form, setForm] = useState({ kind: 'pipeline', workload: 'identical', sizeKiB: 128, iterations: 3, seed: 1 })
  const [job, setJob] = useState(null) // {id, status, progress}
  const [error, setError] = useState(null)
  const timer = useRef(null)

  useEffect(() => () => clearInterval(timer.current), [])

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === 'number' ? Number(e.target.value) : e.target.value }))

  // pick up a benchmark that is already running (e.g. after a page reload)
  const running = useApi((signal) => api.listBenchmarks(signal))
  useEffect(() => {
    const r = running.data?.running
    if (r && !job) track(r.job_id, r.progress)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running.data])

  function track(id, progress = null) {
    setJob({ id, status: 'running', progress })
    clearInterval(timer.current)
    timer.current = setInterval(async () => {
      try {
        const s = await api.getBenchmark(id)
        if (s.status === 'running') {
          setJob({ id, status: 'running', progress: s.progress })
          return
        }
        clearInterval(timer.current)
        setJob({ id, status: s.status, progress: null, error: s.error })
        if (s.status === 'done') onFinished?.(id)
      } catch (e) {
        clearInterval(timer.current)
        setJob(null)
        setError(e)
      }
    }, POLL_MS)
  }

  const start = async () => {
    setError(null)
    const body = { kind: form.kind, size_bytes: form.sizeKiB * 1024, iterations: form.iterations, seed: form.seed }
    if (form.kind === 'pipeline') body.workload = form.workload
    try {
      const r = await api.startBenchmark(body)
      track(r.job_id)
    } catch (e) {
      setError(e)
    }
  }

  const cancel = async () => {
    try {
      await api.cancelBenchmark(job.id)
    } catch (e) {
      setError(e)
    }
    clearInterval(timer.current)
    setJob({ id: job.id, status: 'cancelled', progress: null })
  }

  const busy = job?.status === 'running'
  const pct = Math.round((job?.progress?.fraction ?? 0) * 100)

  return (
    <Card title="Run a benchmark">
      <AsyncState state={catalog} onRetry={() => catalog.reload()}>
        {(c) => {
          const lim = c.limits
          const workload = c.workloads.find((w) => w.id === form.workload)
          return (
            <>
              <p className="notice notice-info">
                Runs in a separate worker process against a private temporary store (never your stored files), one benchmark at a time, bounded to
                {' '}{formatBytes(lim.min_size_bytes)}–{formatBytes(lim.max_size_bytes)} per file, up to {lim.max_iterations} iterations, {lim.max_runtime_seconds} s.
              </p>
              <form className="form-grid" onSubmit={(e) => { e.preventDefault(); start() }} aria-label="Benchmark parameters">
                <label>Benchmark
                  <select value={form.kind} onChange={set('kind')} disabled={busy} aria-label="Benchmark kind">
                    <option value="pipeline">Upload / restore pipeline</option>
                    <option value="chunking_comparison">CDC vs fixed-size chunking</option>
                  </select>
                </label>
                {form.kind === 'pipeline' && (
                  <label>Workload
                    <select value={form.workload} onChange={set('workload')} disabled={busy} aria-label="Workload">
                      {c.workloads.map((w) => <option key={w.id} value={w.id}>{w.id.replace(/_/g, ' ')}</option>)}
                    </select>
                  </label>
                )}
                <label>File size (KiB)
                  <input type="number" min={lim.min_size_bytes / 1024} max={lim.max_size_bytes / 1024} step="16" value={form.sizeKiB} onChange={set('sizeKiB')} disabled={busy} aria-label="File size in KiB" />
                </label>
                <label>Iterations
                  <input type="number" min="1" max={lim.max_iterations} value={form.iterations} onChange={set('iterations')} disabled={busy} aria-label="Iterations" />
                </label>
                <label>Seed
                  <input type="number" min="0" value={form.seed} onChange={set('seed')} disabled={busy} aria-label="Seed" />
                </label>
                <span className="form-actions">
                  <button className="primary" type="submit" disabled={busy} aria-busy={busy || undefined}>Start benchmark</button>
                  {busy && <button type="button" onClick={cancel}>Cancel</button>}
                </span>
              </form>
              {form.kind === 'pipeline' && workload && (
                <p className="muted">{workload.description}.{workload.preset_size_bytes ? ' The size field overrides the preset.' : ''}</p>
              )}
              {busy && (
                <div role="status">
                  <progress value={pct} max="100" aria-label="Benchmark progress" /> <span className="muted">{pct}% — {job.progress?.message || 'starting…'}</span>
                </div>
              )}
              {job?.status === 'failed' && <ErrorNotice error={{ message: job.error || 'The benchmark failed.', code: 'benchmark_failed' }} />}
              {job?.status === 'cancelled' && <p className="muted">Benchmark cancelled; its temporary store was removed.</p>}
              {job?.status === 'done' && <p className="muted">Finished. The result is shown below and saved to disk.</p>}
              {error && <ErrorNotice error={error} />}
            </>
          )
        }}
      </AsyncState>
    </Card>
  )
}
