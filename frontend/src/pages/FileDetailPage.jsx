import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import ChunkMap, { Legend } from '../components/ChunkMap'
import { AsyncState, Card, ConfirmDialog, ErrorNotice, Hash, StatusBadge } from '../components/ui'
import { useUploadHistory } from '../context/UploadHistory'
import { useApi } from '../hooks/useApi'
import { api, downloadFile } from '../services/api'
import { formatBytes, formatDate, formatInt } from '../utils/format'

const DECISION_TEXT = {
  new: 'New: bytes written by this upload',
  reused: 'Reused: chunk already stored by an earlier upload',
  adopted: 'Adopted: valid orphan bytes from an interrupted upload',
  repeated_in_file: 'Repeated: an earlier position of this same file already used it',
}

export function ChunkInspector({ chunk, fileId }) {
  const detail = useApi((signal) => api.getChunk(chunk.chunk_hash, signal), [chunk.chunk_hash])
  return (
    <Card title={`Chunk #${chunk.chunk_index}`}>
      <table>
        <tbody>
          <tr><th>Position in this file</th><td>{chunk.chunk_index}</td></tr>
          <tr><th>SHA-256</th><td><Hash value={chunk.chunk_hash} length={24} /></td></tr>
          <tr><th>Size</th><td>{formatBytes(chunk.chunk_size)} ({formatInt(chunk.chunk_size)} bytes)</td></tr>
        </tbody>
      </table>
      <AsyncState state={detail} onRetry={() => detail.reload()}>
        {(d) => {
          const files = [...new Set(d.references.map((r) => r.file_id))]
          return (
            <>
              <table>
                <tbody>
                  <tr><th>Storage status</th><td><StatusBadge tone={d.state === 'stored' ? 'ok' : 'error'}>{d.state}</StatusBadge></td></tr>
                  <tr><th>Reference count</th><td>{d.ref_count} (manifest rows now: {d.actual_references})</td></tr>
                  <tr><th>Referenced by</th><td>{files.length} file(s)</td></tr>
                </tbody>
              </table>
              <h3>Manifest references</h3>
              <ul className="plain">
                {d.references.map((r) => (
                  <li key={`${r.file_id}-${r.chunk_index}`}>
                    <Link to={`/files/${r.file_id}`}>{r.filename}</Link> at position {r.chunk_index}
                    {r.file_id === fileId && ' (this file)'}
                  </li>
                ))}
              </ul>
              {d.references_truncated && <p className="muted">List truncated; more references exist.</p>}
            </>
          )
        }}
      </AsyncState>
    </Card>
  )
}

export default function FileDetailPage() {
  const { fileId } = useParams()
  const navigate = useNavigate()
  const { byFile, forget } = useUploadHistory()
  const file = useApi((signal) => api.getFile(fileId, signal), [fileId])
  const [selected, setSelected] = useState(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)

  const upload = byFile[fileId]
  const outcomes = useMemo(
    () => (upload ? new Map(upload.chunk_outcomes.map((o) => [o.chunk_index, o.decision])) : null),
    [upload])

  const download = async (f) => {
    setError(null)
    setBusy(true)
    try {
      const r = await downloadFile(f.file_id, f.filename)
      setMessage(`Restored ${formatBytes(r.size)}; server-verified SHA-256 ${r.sha256}`)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      await api.deleteFile(fileId)
      forget(fileId)
      navigate('/files')
    } catch (e) {
      setError(e)
      setConfirmDelete(false)
      setBusy(false)
    }
  }

  return (
    <>
      <p><Link to="/files">← All files</Link></p>
      <AsyncState state={file} onRetry={() => file.reload()}>
        {(f) => {
          const classify = (c) => {
            if (outcomes) return outcomes.get(c.chunk_index) || 'unknown'
            return c.ref_count > 1 ? 'shared' : 'exclusive'
          }
          const legend = outcomes
            ? [['new', DECISION_TEXT.new], ['reused', DECISION_TEXT.reused], ['repeated_in_file', DECISION_TEXT.repeated_in_file], ['adopted', DECISION_TEXT.adopted]]
            : [['shared', 'Currently referenced more than once (ref_count > 1)'], ['exclusive', 'Currently referenced once']]
          const sharedCount = f.chunks.filter((c) => c.ref_count > 1).length
          return (
            <>
              <Card title={f.filename} aside={<StatusBadge tone={f.status === 'complete' ? 'ok' : 'warn'}>{f.status}</StatusBadge>}>
                <table>
                  <tbody>
                    <tr><th>File ID</th><td><code>{f.file_id}</code></td></tr>
                    <tr><th>Original size</th><td>{formatBytes(f.original_size)} ({formatInt(f.original_size)} bytes)</td></tr>
                    <tr><th>Whole-file SHA-256</th><td><Hash value={f.sha256} length={24} /></td></tr>
                    <tr><th>Uploaded</th><td>{formatDate(f.created_at)}</td></tr>
                    <tr><th>Chunk positions</th><td>{formatInt(f.chunk_count)} ({formatInt(f.unique_chunk_count)} distinct)</td></tr>
                    <tr><th>Currently shared</th><td>{formatInt(sharedCount)} position(s) hold a chunk with ref_count &gt; 1</td></tr>
                  </tbody>
                </table>
                <div className="row">
                  <button onClick={() => download(f)} disabled={busy || f.status !== 'complete'}>Restore / download</button>
                  <button className="danger" onClick={() => setConfirmDelete(true)} disabled={busy}>Delete</button>
                  <Link to={`/files/compare?a=${f.file_id}`}>Compare with another file →</Link>
                </div>
                {message && <div className="notice notice-ok" role="status">{message}</div>}
                {error && <ErrorNotice error={error} />}
              </Card>

              <Card title="Chunk map">
                {outcomes ? (
                  <p className="muted">
                    Colours show what <strong>this browser session&rsquo;s upload</strong> actually did with each position
                    (from the upload response).
                  </p>
                ) : (
                  <p className="muted">
                    Upload-time decisions (new vs reused) are not stored by the backend, so they are not shown for this file.
                    Colours show only the <strong>current</strong> reference count.
                  </p>
                )}
                <Legend items={legend} />
                <ChunkMap chunks={f.chunks} classify={classify} selectedIndex={selected?.chunk_index} onSelect={setSelected} />
              </Card>

              {selected && <ChunkInspector chunk={selected} fileId={f.file_id} />}

              <Card title="Ordered chunk manifest">
                {f.chunks.length === 0 ? (
                  <p className="empty">Empty file: no chunks.</p>
                ) : (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr><th>Index</th><th>SHA-256</th><th>Size</th><th>Reference count</th><th>Shared</th></tr>
                      </thead>
                      <tbody>
                        {f.chunks.map((c) => (
                          <tr key={c.chunk_index} className={selected?.chunk_index === c.chunk_index ? 'row-selected' : ''}
                              onClick={() => setSelected(c)}>
                            <td>{c.chunk_index}</td>
                            <td><Hash value={c.chunk_hash} /></td>
                            <td>{formatBytes(c.chunk_size)}</td>
                            <td>{c.ref_count}</td>
                            <td>{c.ref_count > 1 ? 'yes' : 'no'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              {confirmDelete && (
                <ConfirmDialog title="Delete file?" confirmLabel="Delete file" danger busy={busy} onConfirm={remove}
                               onCancel={() => setConfirmDelete(false)}>
                  <p>
                    Delete <strong>{f.filename}</strong>? Reference counts of its {formatInt(f.chunk_count)} chunk
                    positions drop; shared chunks stay on disk.
                  </p>
                </ConfirmDialog>
              )}
            </>
          )
        }}
      </AsyncState>
    </>
  )
}
