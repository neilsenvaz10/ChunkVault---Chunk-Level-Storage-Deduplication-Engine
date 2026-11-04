import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import ChunkMap, { Legend } from '../components/ChunkMap'
import { AsyncState, Card, Hash, MetricTile } from '../components/ui'
import { useApi } from '../hooks/useApi'
import { api } from '../services/api'
import { compareManifests } from '../utils/compare'
import { formatBytes, formatInt, formatPercent } from '../utils/format'

function Selector({ label, value, files, onChange }) {
  return (
    <label className="selector">
      {label}
      <select value={value || ''} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        <option value="">Select a file…</option>
        {files.map((f) => (
          <option key={f.file_id} value={f.file_id}>{f.filename} · {formatBytes(f.original_size)} · {f.file_id.slice(0, 8)}</option>
        ))}
      </select>
    </label>
  )
}

function Result({ a, b }) {
  const cmp = useMemo(() => compareManifests(a.chunks, b.chunks), [a, b])
  const cls = (c) => (cmp.isCommon(c.chunk_hash) ? 'reused' : 'new')
  return (
    <>
      <Card title="Result (from the two stored manifests)">
        <div className="tiles">
          <MetricTile label="Chunks in common" value={formatInt(cmp.commonDistinct)} hint="distinct chunks" />
          <MetricTile label={`Only in ${a.filename}`} value={formatInt(cmp.onlyADistinct)} hint="distinct chunks" />
          <MetricTile label={`Only in ${b.filename}`} value={formatInt(cmp.onlyBDistinct)} hint="distinct chunks" />
          <MetricTile label="Shared payload" value={formatBytes(cmp.sharedPayloadBytes)} hint="bytes of common chunks (each counted once)" />
          <MetricTile label={`Reuse: ${b.filename} from ${a.filename}`} value={formatPercent(cmp.reusePctB)} hint={`${formatBytes(cmp.reusedBytesB)} of ${formatBytes(cmp.totalB)}`} tone="good" />
          <MetricTile label={`Reuse: ${a.filename} from ${b.filename}`} value={formatPercent(cmp.reusePctA)} hint={`${formatBytes(cmp.reusedBytesA)} of ${formatBytes(cmp.totalA)}`} />
        </div>
        <p className="muted">
          Reuse % = bytes of chunk positions whose SHA-256 also appears in the other file ÷ the file&rsquo;s size.
          {cmp.commonDistinct > 0 && (
            <> Common chunks {cmp.sharedChunksKeepRelativeOrder ? 'keep their relative order' : 'appear in a different order'}; position shift(s) of
            the first occurrence: {cmp.shifts.map((s) => (s > 0 ? `+${s}` : String(s))).join(', ')}.</>
          )}
        </p>
      </Card>

      <Card title="Sequence comparison">
        <Legend items={[['reused', 'Chunk also present in the other file'], ['new', 'Chunk only in this file']]} />
        <h3>{a.filename}</h3>
        <ChunkMap chunks={a.chunks} classify={cls} label={`Chunks of ${a.filename}`} />
        <h3>{b.filename}</h3>
        <ChunkMap chunks={b.chunks} classify={cls} label={`Chunks of ${b.filename}`} />
      </Card>

      <Card title="Common chunks">
        {cmp.commonChunks.length === 0 ? (
          <p className="empty">These files share no chunks.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>SHA-256</th><th>Size</th><th>Position(s) in A</th><th>Position(s) in B</th></tr></thead>
              <tbody>
                {cmp.commonChunks.map((c) => (
                  <tr key={c.hash}>
                    <td><Hash value={c.hash} /></td>
                    <td>{formatBytes(c.size)}</td>
                    <td>{c.indexesA.join(', ')}</td>
                    <td>{c.indexesB.join(', ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}

export default function ComparePage() {
  const [params, setParams] = useSearchParams()
  const a = params.get('a')
  const b = params.get('b')
  const files = useApi((signal) => api.listFiles({ limit: 500, offset: 0 }, signal))
  const fa = useApi((signal) => (a ? api.getFile(a, signal) : Promise.resolve(null)), [a])
  const fb = useApi((signal) => (b ? api.getFile(b, signal) : Promise.resolve(null)), [b])
  const set = (key) => (value) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }
  return (
    <>
      <p><Link to="/files">← All files</Link></p>
      <Card title="Choose two stored files">
        <AsyncState state={files} onRetry={() => files.reload()} isEmpty={(d) => d.items.length < 2}
                    empty="Upload at least two files to compare them.">
          {(d) => (
            <div className="row">
              <Selector label="File A" value={a} files={d.items.filter((f) => f.status === 'complete')} onChange={set('a')} />
              <Selector label="File B" value={b} files={d.items.filter((f) => f.status === 'complete')} onChange={set('b')} />
            </div>
          )}
        </AsyncState>
      </Card>
      {a && b && (
        <AsyncState state={{ data: fa.data && fb.data ? { a: fa.data, b: fb.data } : null, error: fa.error || fb.error, loading: fa.loading || fb.loading }}
                    onRetry={() => { fa.reload(); fb.reload() }}>
          {(d) => <Result a={d.a} b={d.b} />}
        </AsyncState>
      )}
      {(!a || !b) && <p className="empty">Select two files to compare their chunk manifests.</p>}
    </>
  )
}
