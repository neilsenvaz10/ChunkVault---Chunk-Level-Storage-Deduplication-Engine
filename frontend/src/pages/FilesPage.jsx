import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AsyncState, Card, ConfirmDialog, ErrorNotice, Hash, Pagination, StatusBadge } from '../components/ui'
import { useUploadHistory } from '../context/UploadHistory'
import { useApi } from '../hooks/useApi'
import { api, downloadFile, uploadFile } from '../services/api'
import { formatBytes, formatDate, formatInt } from '../utils/format'

const PAGE = 25

function UploadCard({ maxBytes, onUploaded }) {
  const { record } = useUploadHistory()
  const inputRef = useRef(null)
  const abortRef = useRef(null)
  const [file, setFile] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [phase, setPhase] = useState('idle') // idle | sending | processing | done | failed
  const [progress, setProgress] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => () => abortRef.current?.abort(), [])

  const tooBig = file && maxBytes && file.size > maxBytes
  const busy = phase === 'sending' || phase === 'processing'

  const choose = (f) => {
    if (!f || busy) return
    setFile(f)
    setResult(null)
    setError(null)
    setPhase('idle')
    setProgress(null)
  }

  const start = async () => {
    if (!file || tooBig || busy) return
    const controller = new AbortController()
    abortRef.current = controller
    setPhase('sending')
    setError(null)
    setResult(null)
    try {
      const res = await uploadFile(file, {
        signal: controller.signal,
        onProgress: (p) => {
          setProgress(p)
          if (p.loaded >= p.total) setPhase('processing')
        },
      })
      record(res)
      setResult(res)
      setPhase('done')
      onUploaded?.(res)
    } catch (e) {
      if (e?.name === 'AbortError') setPhase('idle')
      else {
        setError(e)
        setPhase('failed')
      }
    }
  }

  const pct = progress ? Math.round((progress.loaded / progress.total) * 100) : 0

  return (
    <Card title="Upload a file">
      <div
        className={`dropzone ${dragging ? 'dragging' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); choose(e.dataTransfer.files?.[0]) }}
      >
        <p>Drag a file here, or</p>
        <label className="file-picker">
          <input ref={inputRef} type="file" aria-label="Choose file" disabled={busy}
                 onChange={(e) => choose(e.target.files?.[0])} />
        </label>
        {maxBytes && <small className="muted">Server limit: {formatBytes(maxBytes)}</small>}
      </div>

      {file && (
        <p className="selected-file">
          <strong>{file.name}</strong> · {formatBytes(file.size)} ({formatInt(file.size)} bytes)
        </p>
      )}
      {tooBig && (
        <div className="notice notice-error" role="alert">
          This file is {formatBytes(file.size)}; the server accepts at most {formatBytes(maxBytes)}.
        </div>
      )}

      <div className="row">
        <button className="primary" onClick={start} disabled={!file || tooBig || busy}>Upload</button>
        {busy && <button onClick={() => abortRef.current?.abort()}>Cancel</button>}
      </div>

      {phase === 'sending' && (
        <div>
          <progress value={pct} max="100" aria-label="Transfer progress" />
          <span className="muted"> Sending to server: {pct}% ({formatBytes(progress?.loaded || 0)} of {formatBytes(progress?.total || file.size)})</span>
        </div>
      )}
      {phase === 'processing' && (
        <p className="muted" role="status">
          Transfer complete. The server is chunking, hashing and committing — it does not report progress for
          these stages, so none is shown.
        </p>
      )}
      {phase === 'failed' && <ErrorNotice error={error} />}

      {phase === 'done' && result && <UploadResult result={result} />}
    </Card>
  )
}

export function UploadResult({ result }) {
  const reusedBytes = result.original_size - result.bytes_written
  return (
    <div className="notice notice-ok" role="status">
      <strong>Upload stored</strong>
      <table>
        <tbody>
          <tr><th>File ID</th><td><code>{result.file_id}</code></td></tr>
          <tr><th>Original size</th><td>{formatBytes(result.original_size)} ({formatInt(result.original_size)} bytes)</td></tr>
          <tr><th>Whole-file SHA-256</th><td><Hash value={result.sha256} length={20} /></td></tr>
          <tr><th>Chunks</th><td>{formatInt(result.chunk_count)}</td></tr>
          <tr><th>New chunks</th><td>{formatInt(result.new_chunks)}</td></tr>
          <tr><th>Reused chunk occurrences</th><td>{formatInt(result.duplicate_chunks)}</td></tr>
          <tr><th>Newly stored bytes</th><td>{formatBytes(result.bytes_written)}</td></tr>
          <tr><th>Bytes deduplicated</th><td>{formatBytes(reusedBytes)}</td></tr>
        </tbody>
      </table>
      <Link to={`/files/${result.file_id}`}>Inspect chunk map →</Link>
    </div>
  )
}

function Inventory({ files, reload, offset, setOffset }) {
  const { forget } = useUploadHistory()
  const [pendingDelete, setPendingDelete] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [message, setMessage] = useState(null)
  const [error, setError] = useState(null)

  const download = async (f) => {
    setError(null)
    setBusyId(f.file_id)
    try {
      const r = await downloadFile(f.file_id, f.filename)
      setMessage(`Restored "${f.filename}" (${formatBytes(r.size)}); server-verified SHA-256 ${r.sha256?.slice(0, 12)}…`)
    } catch (e) {
      setError(e)
    } finally {
      setBusyId(null)
    }
  }

  const confirmDelete = async () => {
    const f = pendingDelete
    setBusyId(f.file_id)
    try {
      const r = await api.deleteFile(f.file_id)
      forget(f.file_id)
      setMessage(
        `Deleted "${f.filename}": ${r.chunks_released} chunk reference(s) released, ${r.chunks_reclaimed} chunk file(s) ` +
        `physically removed (${formatBytes(r.bytes_reclaimed)}); chunks shared with other files were kept.`)
      setPendingDelete(null)
      if (files.items.length === 1 && offset > 0) setOffset(Math.max(0, offset - PAGE))
      else reload()
    } catch (e) {
      setError(e)
      setPendingDelete(null)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card title="Stored files" aside={<Link to="/files/compare">Compare two files →</Link>}>
      {message && <div className="notice notice-ok" role="status">{message}</div>}
      {error && <ErrorNotice error={error} />}
      {files.items.length === 0 ? (
        <p className="empty">No files stored yet. Upload one above.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Filename</th><th>File ID</th><th>Size</th><th>Chunks</th><th>SHA-256</th><th>Uploaded</th><th>Status</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {files.items.map((f) => (
                <tr key={f.file_id}>
                  <td>{f.filename}</td>
                  <td><code title={f.file_id}>{f.file_id.slice(0, 8)}…</code></td>
                  <td>{formatBytes(f.original_size)}</td>
                  <td>{formatInt(f.chunk_count)}</td>
                  <td><Hash value={f.sha256} /></td>
                  <td>{formatDate(f.created_at)}</td>
                  <td><StatusBadge tone={f.status === 'complete' ? 'ok' : f.status === 'failed' ? 'error' : 'warn'}>{f.status}</StatusBadge></td>
                  <td className="actions">
                    <Link to={`/files/${f.file_id}`}>Inspect</Link>
                    <button onClick={() => download(f)} disabled={busyId === f.file_id || f.status !== 'complete'}>Download</button>
                    <button className="danger" onClick={() => setPendingDelete(f)} disabled={busyId === f.file_id}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pagination total={files.total} limit={PAGE} offset={offset} onChange={setOffset} />
      {pendingDelete && (
        <ConfirmDialog
          title="Delete file?" confirmLabel="Delete file" danger busy={busyId === pendingDelete.file_id}
          onConfirm={confirmDelete} onCancel={() => setPendingDelete(null)}
        >
          <p>
            Delete <strong>{pendingDelete.filename}</strong> ({formatInt(pendingDelete.chunk_count)} chunk references)?
            Its manifest is removed and reference counts drop by one per occurrence. Chunks still referenced by other
            files are kept; only chunks that reach zero references are removed from disk. This cannot be undone.
          </p>
        </ConfirmDialog>
      )}
    </Card>
  )
}

export default function FilesPage() {
  const [offset, setOffset] = useState(0)
  const files = useApi((signal) => api.listFiles({ limit: PAGE, offset }, signal), [offset])
  const health = useApi((signal) => api.health(signal))
  return (
    <>
      <UploadCard maxBytes={health.data?.max_upload_bytes} onUploaded={() => files.reload({ silent: true })} />
      <AsyncState state={files} onRetry={() => files.reload()}>
        {(data) => <Inventory files={data} reload={() => files.reload({ silent: true })} offset={offset} setOffset={setOffset} />}
      </AsyncState>
    </>
  )
}
