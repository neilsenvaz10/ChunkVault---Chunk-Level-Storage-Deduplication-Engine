import { useState } from 'react'
import { Card, StatusBadge } from './ui'
import { formatBytes } from '../utils/format'

// Relative representation of the documented content-addressed layout (ADR-0004).
// It is derived from the digest; the server's real filesystem location is never shown.
export const chunkPath = (hash) => `chunks/${hash.slice(0, 2)}/${hash}`

const LIMIT = 12

export default function CasView({ file, selectedIndex, onSelect }) {
  const [all, setAll] = useState(false)
  const rows = all ? file.chunks : file.chunks.slice(0, LIMIT)
  return (
    <Card title="Content-addressed storage">
      <ol className="cas-flow" aria-label="Content-addressed chain">
        <li><strong>File</strong><small>{file.filename}</small></li>
        <li><strong>Ordered manifest</strong><small>{file.chunk_count} row(s) in file_chunks</small></li>
        <li><strong>SHA-256 chunk IDs</strong><small>{file.unique_chunk_count} distinct</small></li>
        <li><strong>Physical chunk files</strong><small>one per distinct ID</small></li>
      </ol>
      <p className="muted">
        A chunk is stored under the SHA-256 of its own bytes. Identical content always produces the same identifier, so a second
        occurrence — in this file or any other — points at the same stored chunk instead of writing it again. Paths are shown
        relative to the chunk store root.
      </p>
      {file.chunks.length === 0 ? <p className="empty">Empty file: no manifest rows, no chunk files.</p> : (
        <div className="table-wrap">
          <table aria-label="Manifest to chunk files">
            <thead><tr><th>#</th><th>Chunk ID (SHA-256)</th><th>Size</th><th>Ref count</th><th>Shared?</th><th>Chunk file (relative)</th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.chunk_index} className={selectedIndex === c.chunk_index ? 'row-selected' : ''}>
                  <td>{c.chunk_index}</td>
                  <td><button className="link-btn" onClick={() => onSelect(c)} aria-label={`Inspect chunk ${c.chunk_index}`}><code>{c.chunk_hash.slice(0, 16)}…</code></button></td>
                  <td>{formatBytes(c.chunk_size)}</td>
                  <td>{c.ref_count}</td>
                  <td>{c.ref_count > 1 ? <StatusBadge tone="ok">shared</StatusBadge> : <span className="muted">exclusive</span>}</td>
                  <td><code className="path">{chunkPath(c.chunk_hash).slice(0, 22)}…</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {file.chunks.length > LIMIT && (
        <button onClick={() => setAll((a) => !a)}>{all ? `Show first ${LIMIT}` : `Show all ${file.chunks.length} rows`}</button>
      )}
      <p className="muted">Select a shared chunk to list every file that references it.</p>
    </Card>
  )
}
