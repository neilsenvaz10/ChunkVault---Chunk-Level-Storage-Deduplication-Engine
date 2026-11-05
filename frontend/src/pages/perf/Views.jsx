import Chart from '../../components/Chart'
import { Card, MetricTile, StatusBadge } from '../../components/ui'
import { formatBytes, formatDate, formatInt, formatPercent, formatRate, formatRatio, formatSecPerMiB, formatSeconds } from '../../utils/format'

// unique per saved run even when the same workload and size were run twice
const runLabel = (r) => `${r.spec.workload} ${Math.round(r.spec.size_bytes / 1024)}K ${String(r.job_id).slice(0, 4)}`
const sumBytes = (r) => r.iterations[0].uploads.reduce((n, u) => n + u.size_bytes, 0)

// Cache state is only stated when the result declares it; older results say nothing, so the UI says "not recorded".
const cacheText = (r) => (r.conditions
  ? `${r.conditions.cache_state} — ${r.conditions.cache_note}`
  : 'not recorded in this saved result')

export function PipelineView({ r }) {
  const s = r.summary
  const it0 = r.iterations[0]
  const env = r.environment
  const range = (m, f) => (m && m.min !== null ? `min ${f(m.min)} · max ${f(m.max)} · n=${m.n}` : undefined)
  const stat = (label, m, f) => (
    <tr key={label}><td>{label}</td><td>{f(m.mean)}</td><td>{f(m.median)}</td><td>{f(m.min)}</td><td>{f(m.max)}</td><td>{m.stdev === null ? '—' : f(m.stdev)}</td></tr>
  )
  const stages = Object.entries(s.upload_stage_seconds).sort((a, b) => b[1].median - a[1].median)
  const stageTotal = stages.reduce((n, [, m]) => n + m.median, 0)
  const st = it0.storage
  return (
    <>
      <Card title="Result overview" aside={<StatusBadge tone="ok">measured on this machine</StatusBadge>}>
        <div className="tiles">
          <MetricTile label="Upload throughput (median)" value={formatRate(s.upload_throughput_mib_s.median)} hint={range(s.upload_throughput_mib_s, formatRate)} />
          <MetricTile label="Restore throughput (median)" value={formatRate(s.restore_throughput_mib_s.median)} hint={range(s.restore_throughput_mib_s, formatRate)} />
          <MetricTile label="Deduplication ratio" value={formatRatio(s.dedup_ratio.median)} tone="good" />
          <MetricTile label="Storage savings" value={formatPercent(s.savings_percent.median)} tone="good" />
          <MetricTile label="Total execution time" value={formatSeconds(s.total_execution_seconds)} hint="uploads + restores, all iterations" />
          <MetricTile label="Restored files verified" value={s.integrity_all_verified ? 'all' : 'NOT all'} tone={s.integrity_all_verified ? 'good' : 'bad'} hint="SHA-256 of each restore vs the source" />
        </div>
      </Card>

      <Card title="Benchmark details">
        <table aria-label="Benchmark parameters">
          <tbody>
            <tr><th>Workload</th><td>{r.spec.workload}</td></tr>
            <tr><th>Dataset seed</th><td>{r.spec.seed}</td></tr>
            <tr><th>File size</th><td>{formatBytes(r.spec.size_bytes)}</td></tr>
            <tr><th>Iterations</th><td>{r.spec.iterations}</td></tr>
            <tr><th>Chunking parameters</th><td>min {formatBytes(env.cdc.min_chunk_size)} · target {formatBytes(env.cdc.avg_chunk_size)} · max {formatBytes(env.cdc.max_chunk_size)} · window {env.cdc.window_size} B · {env.chunker}</td></tr>
            <tr><th>Runtime environment</th><td>{env.implementation} {env.python} · {env.platform} · {env.cpu_count} CPUs · SQLite {env.sqlite_version}</td></tr>
            <tr><th>Cache state</th><td>{cacheText(r)}</td></tr>
            <tr><th>Finished</th><td>{formatDate(r.finished_at)}</td></tr>
          </tbody>
        </table>
        <h3>Average, median, minimum, maximum (across iterations)</h3>
        <div className="table-wrap">
          <table aria-label="Summary statistics">
            <thead><tr><th>Metric</th><th>Mean</th><th>Median</th><th>Min</th><th>Max</th><th>Std dev</th></tr></thead>
            <tbody>
              {stat('Upload time', s.upload_total_seconds, formatSeconds)}
              {stat('Upload throughput', s.upload_throughput_mib_s, formatRate)}
              {stat('Restore time', s.restore_total_seconds, formatSeconds)}
              {stat('Restore throughput', s.restore_throughput_mib_s, formatRate)}
              {s.upload_seconds_per_mib && stat('Upload seconds per MiB', s.upload_seconds_per_mib, formatSecPerMiB)}
              {s.restore_seconds_per_mib && stat('Restore seconds per MiB', s.restore_seconds_per_mib, formatSecPerMiB)}
            </tbody>
          </table>
        </div>
        {!s.upload_seconds_per_mib && (
          <p className="muted">Seconds per MiB was not recorded in this saved result (it was saved before that figure existed); nothing has been back-filled.</p>
        )}
        <h3>Individual runs</h3>
        <div className="table-wrap">
          <table aria-label="Individual runs">
            <thead><tr><th>Iteration</th><th>Upload time</th><th>Upload MiB/s</th><th>Restore time</th><th>Restore MiB/s</th><th>Verified</th></tr></thead>
            <tbody>
              {r.iterations.map((it) => {
                const ut = it.uploads.reduce((n, u) => n + u.total_seconds, 0)
                const rt = it.restores.reduce((n, x) => n + x.total_seconds, 0)
                const ub = it.uploads.reduce((n, u) => n + u.size_bytes, 0)
                const rb = it.restores.reduce((n, x) => n + x.size_bytes, 0)
                return (
                  <tr key={it.iteration}>
                    <td>{it.iteration}</td><td>{formatSeconds(ut)}</td><td>{formatRate(ub / 1048576 / ut)}</td>
                    <td>{formatSeconds(rt)}</td><td>{formatRate(rb / 1048576 / rt)}</td><td>{it.restores.every((x) => x.integrity_verified) ? 'yes' : 'NO'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Where upload time goes (median per stage, exclusive timings)">
        <div className="table-wrap">
          <table aria-label="Upload stage breakdown">
            <thead><tr><th>Stage</th><th>Median time</th><th>Share</th></tr></thead>
            <tbody>
              {stages.map(([name, m]) => (
                <tr key={name}><td>{name.replace(/_/g, ' ')}</td><td>{formatSeconds(m.median)}</td><td>{stageTotal ? `${((100 * m.median) / stageTotal).toFixed(1)}%` : '—'}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted">Stages never overlap: when one runs inside another the outer one is paused, so the shares add up to the whole upload.</p>
      </Card>

      <Card title="Deduplication and storage overhead (first iteration)">
        <table aria-label="Storage results">
          <tbody>
            <tr><th>Logical bytes</th><td>{formatBytes(st.logical_bytes)}</td></tr>
            <tr><th>Unique physical chunk bytes</th><td>{formatBytes(st.unique_chunk_bytes)}</td></tr>
            <tr><th>Chunks generated / unique / reused</th><td>{formatInt(st.chunks_generated)} / {formatInt(st.unique_chunks)} / {formatInt(st.reused_chunks)}</td></tr>
            <tr><th>Average chunk size</th><td>{formatBytes(Math.round(st.avg_chunk_bytes || 0))}</td></tr>
            <tr><th>SQLite database size</th><td>{formatBytes(st.database_bytes)}</td></tr>
            {st.footprint_ratio !== undefined ? (
              <>
                <tr><th>On-disk footprint (SQLite file + chunk files)</th><td>{formatBytes(st.footprint_bytes)} = {formatBytes(st.database_bytes)} database + {formatBytes(st.chunk_store_bytes)} chunk files</td></tr>
                <tr><th>On-disk footprint ratio</th><td>{formatRatio(st.footprint_ratio)} = logical bytes {formatBytes(st.logical_bytes)} ÷ footprint {formatBytes(st.footprint_bytes)}</td></tr>
                <tr><th>Footprint savings</th><td>{formatPercent(st.footprint_savings_percent)} (negative when metadata overhead exceeds the reuse; excludes filesystem block rounding)</td></tr>
              </>
            ) : (
              <tr><th>On-disk footprint ratio</th><td className="muted">not recorded in this saved result (saved before this figure existed)</td></tr>
            )}
            <tr><th>Database growth over the empty schema</th><td>{formatBytes(st.database_overhead_bytes)} for {formatInt(st.manifest_rows)} manifest rows (page-granular: small runs may show 0)</td></tr>
          </tbody>
        </table>
        <div className="table-wrap">
          <table aria-label="Per-upload results">
            <thead><tr><th>Upload</th><th>Size</th><th>Time</th><th>Chunks</th><th>New</th><th>Reused</th><th>Bytes written</th></tr></thead>
            <tbody>
              {it0.uploads.map((u) => (
                <tr key={u.name}><td>{u.name}</td><td>{formatBytes(u.size_bytes)}</td><td>{formatSeconds(u.total_seconds)}</td><td>{u.chunk_count}</td><td>{u.new_chunks}</td><td>{u.reused_chunks}</td><td>{formatBytes(u.bytes_written)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Charts for this result">
        <Chart title="Logical vs physical storage" type="bar" xLabel="storage" yLabel="bytes" formatY={formatBytes}
               series={[{ name: 'Logical bytes', points: [{ x: r.spec.workload, y: st.logical_bytes }] }, { name: 'Unique physical chunk bytes', points: [{ x: r.spec.workload, y: st.unique_chunk_bytes }] }]} />
        <Chart title="Benchmark iteration comparison" type="bar" xLabel="iteration" yLabel="seconds" formatY={formatSeconds}
               series={[{ name: 'Upload time', points: r.iterations.map((it) => ({ x: `#${it.iteration}`, y: it.uploads.reduce((n, u) => n + u.total_seconds, 0) })) },
                 { name: 'Restore time', points: r.iterations.map((it) => ({ x: `#${it.iteration}`, y: it.restores.reduce((n, x) => n + x.total_seconds, 0) })) }]} />
      </Card>
    </>
  )
}

export function ComparisonView({ r }) {
  return (
    <>
      <Card title="CDC vs fixed-size chunking" aside={<StatusBadge tone="ok">measured on this machine</StatusBadge>}>
        <p className="muted">
          Both schemes chunk the <strong>same bytes</strong> (seed {r.spec.seed}, {formatBytes(r.spec.size_bytes)} per file; fixed size {formatBytes(r.fixed_chunk_bytes)} = the CDC target),
          hash chunks with the same SHA-256 and are timed identically. Savings count content that repeats within or across the versions.
        </p>
        <div className="table-wrap">
          <table aria-label="CDC versus fixed-size results">
            <thead>
              <tr><th>Case</th><th>Scheme</th><th>Chunks</th><th>Avg chunk</th><th>Reused chunks</th><th>Reused bytes</th><th>Savings</th><th>Processing time (median)</th><th>Seconds per MiB (median)</th></tr>
            </thead>
            <tbody>
              {r.cases.flatMap((c) => ['cdc', 'fixed'].map((k) => (
                <tr key={`${c.id}-${k}`} className={k === 'cdc' ? 'case-first' : ''}>
                  {k === 'cdc' && <td rowSpan="2"><strong>{c.title}</strong><br /><small className="muted">{c.note}</small><br /><StatusBadge tone={c.verdict === 'CDC saves more' ? 'ok' : c.verdict === 'no meaningful difference' ? 'muted' : 'warn'}>{c.verdict}</StatusBadge></td>}
                  <td>{k === 'cdc' ? 'CDC' : 'Fixed-size'}</td><td>{c[k].chunk_count}</td><td>{formatBytes(Math.round(c[k].avg_chunk_bytes))}</td>
                  <td>{c[k].reused_chunks}</td><td>{formatBytes(c[k].reused_bytes)}</td><td>{formatPercent(c[k].savings_percent)}</td><td>{formatSeconds(c[k].processing_seconds.median)}</td>
                  <td>{c[k].processing_seconds_per_mib ? formatSecPerMiB(c[k].processing_seconds_per_mib.median) : '—'}</td>
                </tr>
              )))}
            </tbody>
          </table>
        </div>
        <Chart title="Storage savings: CDC vs fixed-size" type="bar" xLabel="case" yLabel="storage savings (%)" formatY={(v) => `${v.toFixed(0)}%`}
               series={[{ name: 'CDC', points: r.cases.map((c) => ({ x: c.id.replace(/_/g, ' '), y: c.cdc.savings_percent })) },
                 { name: 'Fixed-size', points: r.cases.map((c) => ({ x: c.id.replace(/_/g, ' '), y: c.fixed.savings_percent })) }]} />
      </Card>
      <Card title="How to read this">
        <ul>
          <li><strong>Why CDC can win after an insertion or deletion:</strong> content-defined boundaries depend on the bytes around them, so after a small edit near the start the later boundaries fall on the same content and those chunks keep their SHA-256. Fixed-size boundaries all move by the edit length, so nearly every later chunk changes.</li>
          <li><strong>Where it gives little or no advantage:</strong> identical copies and unrelated random data are handled the same by both; and an <em>in-place</em> edit shifts nothing, so the smaller fixed-size chunks can even save more because CDC chunks are larger and one edit invalidates more bytes. Read the verdict column: it is computed from the measured savings, not assumed.</li>
          <li><strong>Cost:</strong> CDC does more work per byte (compare the processing-time column).</li>
        </ul>
      </Card>
    </>
  )
}

// Charts that compare every saved pipeline result (real measurements only).
export function AggregateCharts({ pipelines, comparison }) {
  const pts = (f) => pipelines.map((r) => ({ x: sumBytes(r), y: f(r) })).filter((p) => p.y !== null && p.y !== undefined)
  return (
    <Card title="Charts across saved benchmarks">
      <p className="muted">Each point is one saved benchmark run (the median across its iterations). With few runs the lines are short — run more workloads and sizes to fill them in.</p>
      <Chart title="Upload size vs upload time" xLabel="total bytes uploaded" yLabel="median upload time" formatX={formatBytes} formatY={formatSeconds}
             empty="No saved pipeline benchmark yet."
             series={[{ name: 'Upload time', points: pts((r) => r.summary.upload_total_seconds.median) }]} />
      <Chart title="Restore size vs restore time" xLabel="total bytes restored" yLabel="median restore time" formatX={formatBytes} formatY={formatSeconds}
             empty="No saved pipeline benchmark yet."
             series={[{ name: 'Restore time', points: pts((r) => r.summary.restore_total_seconds.median) }]} />
      <Chart title="Chunk count vs file size" xLabel="total bytes uploaded" yLabel="chunks generated" formatX={formatBytes} formatY={(v) => String(Math.round(v))}
             empty="No saved pipeline benchmark yet."
             series={[{ name: 'Chunks generated', points: pts((r) => r.iterations[0].storage.chunks_generated) }]} />
      <Chart title="Logical vs physical storage (all saved runs)" type="bar" xLabel="run" yLabel="bytes" formatY={formatBytes}
             empty="No saved pipeline benchmark yet."
             series={[{ name: 'Logical bytes', points: pipelines.map((r) => ({ x: runLabel(r), y: r.iterations[0].storage.logical_bytes })) },
               { name: 'Unique physical chunk bytes', points: pipelines.map((r) => ({ x: runLabel(r), y: r.iterations[0].storage.unique_chunk_bytes })) }]} />
      {comparison && (
        <Chart title="Storage savings: CDC vs fixed-size (latest comparison)" type="bar" xLabel="case" yLabel="storage savings (%)" formatY={(v) => `${v.toFixed(0)}%`}
               series={[{ name: 'CDC', points: comparison.cases.map((c) => ({ x: c.id.replace(/_/g, ' '), y: c.cdc.savings_percent })) },
                 { name: 'Fixed-size', points: comparison.cases.map((c) => ({ x: c.id.replace(/_/g, ' '), y: c.fixed.savings_percent })) }]} />
      )}
    </Card>
  )
}
