import { useMemo, useState } from 'react'
import { AsyncState, Card, Hash } from '../../components/ui'
import { useUploadHistory } from '../../context/UploadHistory'
import { useApi } from '../../hooks/useApi'
import { api } from '../../services/api'
import { formatBytes, formatInt } from '../../utils/format'

const pathOf = (h) => `chunks/${h.slice(0, 2)}/${h}`

// Real facts derived from a selected stored file (and, when present, THIS session's upload response).
function Facts({ stage, file, config, outcomes }) {
  if (!file) {
    return <p className="muted">Select a stored file above to see its real values for this stage. Without one, this page is a pure architecture explanation.</p>
  }
  const sizes = file.chunks.map((c) => c.chunk_size)
  const rows = []
  const add = (k, v) => rows.push([k, v])
  const f = stage.facts
  if (f.includes('file')) {
    add('Filename', file.filename)
    add('Size', `${formatBytes(file.original_size)} (${formatInt(file.original_size)} bytes)`)
  }
  if (f.includes('id')) {
    add('File ID', <code>{file.file_id}</code>)
    add('Matches 32 lowercase hex?', /^[0-9a-f]{32}$/.test(file.file_id) ? 'yes' : 'no')
  }
  if (f.includes('limit') && config) {
    add('Configured upload limit', formatBytes(config.max_upload_bytes))
    add('This file within the limit?', file.original_size <= config.max_upload_bytes ? 'yes' : 'no')
  }
  if (f.includes('chunks')) {
    add('Chunk positions', formatInt(file.chunk_count))
    if (sizes.length) {
      add('Smallest / largest chunk', `${formatBytes(Math.min(...sizes))} / ${formatBytes(Math.max(...sizes))}`)
      add('Mean chunk size (this file)', formatBytes(Math.round(sizes.reduce((a, b) => a + b, 0) / sizes.length)))
    }
  }
  if (f.includes('config') && config) {
    const c = config.cdc
    add('Configured min / target / max', `${formatBytes(c.min_chunk_size)} / ${formatBytes(c.avg_chunk_size)} / ${formatBytes(c.max_chunk_size)}`)
    add('Window · boundary rule', `${c.window_size} bytes · fingerprint mod ${c.boundary_divisor} == ${c.boundary_remainder}`)
  }
  if (f.includes('hashes') || f.includes('whole') || f.includes('result')) {
    add('Whole-file SHA-256', <Hash value={file.sha256} length={24} />)
  }
  if (f.includes('whole')) add('Recorded size', formatInt(file.original_size))
  if (f.includes('result')) {
    add('Chunks (distinct)', `${formatInt(file.chunk_count)} (${formatInt(file.unique_chunk_count)})`)
  }
  const first = file.chunks.slice(0, 5)
  let extra = null
  if (f.includes('hashes') && first.length) {
    extra = (
      <table>
        <thead><tr><th>#</th><th>Chunk SHA-256</th><th>Size</th></tr></thead>
        <tbody>{first.map((c) => <tr key={c.chunk_index}><td>{c.chunk_index}</td><td><Hash value={c.chunk_hash} /></td><td>{formatBytes(c.chunk_size)}</td></tr>)}</tbody>
      </table>
    )
  }
  if (f.includes('manifest') || f.includes('order')) {
    extra = (
      <table>
        <thead><tr><th>chunk_index</th><th>chunk_hash</th><th>chunk_size</th><th>ref_count (now)</th></tr></thead>
        <tbody>{first.map((c) => <tr key={c.chunk_index}><td>{c.chunk_index}</td><td><Hash value={c.chunk_hash} /></td><td>{c.chunk_size}</td><td>{c.ref_count}</td></tr>)}</tbody>
      </table>
    )
    if (f.includes('order')) add('Order of reconstruction', 'ascending chunk_index (0 … ' + (file.chunk_count - 1) + ')')
    add('Sum of chunk sizes', `${formatInt(sizes.reduce((a, b) => a + b, 0))} bytes ${sizes.reduce((a, b) => a + b, 0) === file.original_size ? '= file size' : '≠ file size'}`)
  }
  if (f.includes('paths') && first.length) {
    extra = (
      <ul className="plain">
        {first.map((c) => <li key={c.chunk_index}>#{c.chunk_index} → <code>{pathOf(c.chunk_hash)}</code></li>)}
      </ul>
    )
  }
  if (f.includes('dedup') || f.includes('publish')) {
    if (outcomes) {
      const count = (d) => outcomes.filter((o) => o.decision === d).length
      add('Upload-time decisions (this session\'s upload response)', `new ${count('new')} · reused ${count('reused')} · adopted ${count('adopted')} · repeated in file ${count('repeated_in_file')}`)
      if (f.includes('publish')) add('Bytes written by that upload', formatBytes(outcomes.filter((o) => o.decision === 'new').reduce((a, o) => a + o.chunk_size, 0)))
    } else {
      add('Upload-time decisions', <em className="muted">not recorded for this file — the backend does not store them; they are only available for uploads made in this browser session</em>)
      add('Chunks currently shared (ref_count > 1)', formatInt(file.chunks.filter((c) => c.ref_count > 1).length))
    }
  }
  if (f.includes('manifest') || f.includes('result')) {
    const refs = file.chunks.map((c) => c.ref_count)
    if (refs.length) add('Reference counts now (min / max)', `${Math.min(...refs)} / ${Math.max(...refs)}`)
  }
  return (
    <>
      <table><tbody>{rows.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody></table>
      {extra}
      {file.chunks.length > 5 && extra && <p className="muted">First 5 of {file.chunk_count} chunk positions shown.</p>}
    </>
  )
}

export default function Walkthrough({ stages, kind }) {
  const [index, setIndex] = useState(0)
  const [fileId, setFileId] = useState('')
  const { byFile } = useUploadHistory()
  const files = useApi((signal) => api.listFiles({ limit: 200, offset: 0 }, signal))
  const config = useApi((signal) => api.systemConfig(signal))
  const detail = useApi((signal) => (fileId ? api.getFile(fileId, signal) : Promise.resolve(null)), [fileId])
  const stage = stages[index]
  const outcomes = useMemo(() => (fileId && byFile[fileId] ? byFile[fileId].chunk_outcomes : null), [byFile, fileId])

  return (
    <>
      <div className="notice" role="note">
        <strong>Architecture explanation — not a recorded execution trace.</strong>
        <span>
          The stages describe how the implemented code behaves. Stepping through them performs no {kind} and changes nothing.
          Values shown for a selected file are read from the current database; no timings or past decisions are invented.
        </span>
      </div>

      <Card title="Optionally connect a stored file">
        <AsyncState state={files} onRetry={() => files.reload()}>
          {(d) => (
            <label className="selector">
              Stored file
              <select value={fileId} onChange={(e) => setFileId(e.target.value)} aria-label="Stored file">
                <option value="">None — explanation only</option>
                {d.items.filter((f) => f.status === 'complete').map((f) => (
                  <option key={f.file_id} value={f.file_id}>{f.filename} · {formatBytes(f.original_size)}</option>
                ))}
              </select>
            </label>
          )}
        </AsyncState>
        {files.data?.items.length === 0 && <p className="empty">No stored files yet; upload one to attach real values.</p>}
      </Card>

      <Card title={`${kind === 'upload' ? 'Upload' : 'Restore'} lifecycle`}>
        <div className="stepper-controls" role="group" aria-label="Walkthrough controls">
          <button onClick={() => setIndex((i) => Math.max(0, i - 1))} disabled={index === 0}>Previous</button>
          <span aria-live="polite">Stage {index + 1} of {stages.length}</span>
          <button className="primary" onClick={() => setIndex((i) => Math.min(stages.length - 1, i + 1))} disabled={index === stages.length - 1}>Next</button>
          <button onClick={() => setIndex(0)} disabled={index === 0}>Reset</button>
        </div>
        <ol className="stages" aria-label="Stages">
          {stages.map((s, i) => (
            <li key={s.id}>
              <button className={i === index ? 'active' : ''} aria-current={i === index ? 'step' : undefined} onClick={() => setIndex(i)}>
                <span className="stage-no">{i + 1}</span> {s.title}
              </button>
            </li>
          ))}
        </ol>
        <div className="details" aria-live="polite">
          <h3>{index + 1}. {stage.title}</h3>
          <dl className="defs">
            <dt>Performed by</dt><dd>{stage.component}</dd>
            <dt>What happens</dt><dd>{stage.what}</dd>
            <dt>Why it is necessary</dt><dd>{stage.why}</dd>
            <dt>Data created or modified</dt><dd>{stage.data}</dd>
            <dt>If this stage fails</dt><dd>{stage.failure}</dd>
          </dl>
          <h3>Real values from the selected file</h3>
          <AsyncState
            state={!fileId ? { data: { none: true }, error: null, loading: false }
              : detail.data?.file_id === fileId ? { data: detail.data, error: null, loading: false }
                : { ...detail, data: null }}
            onRetry={() => detail.reload()}>
            {(d) => <Facts stage={stage} file={d.none ? null : d} config={config.data} outcomes={outcomes} />}
          </AsyncState>
        </div>
      </Card>
    </>
  )
}
