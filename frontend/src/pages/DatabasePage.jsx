import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AsyncState, Card, Hash, Pagination, StatusBadge } from '../components/ui'
import { useApi } from '../hooks/useApi'
import { api } from '../services/api'
import { formatInt } from '../utils/format'

const PAGE = 15
const BOXES = {
  files: { x: 20, y: 30 },
  file_chunks: { x: 250, y: 30 },
  chunks: { x: 480, y: 30 },
  operations: { x: 20, y: 150 },
}
const RELATIONS = [
  ['files', 'file_chunks', '1 : N  (CASCADE)'],
  ['chunks', 'file_chunks', '1 : N  (RESTRICT)'],
  ['files', 'operations', '1 : N  (SET NULL)'],
]

function ErDiagram({ tables, selected, onSelect }) {
  const count = (n) => tables.find((t) => t.name === n)?.row_count
  const BW = 190
  const BH = 70
  const mid = (n) => ({ x: BOXES[n].x + BW / 2, y: BOXES[n].y + BH / 2 })
  return (
    <svg viewBox="0 0 700 240" className="diagram" role="group" aria-label="Entity relationship diagram">
      {RELATIONS.map(([a, b, label]) => {
        const p = mid(a)
        const q = mid(b)
        return (
          <g key={`${a}-${b}`}>
            <line x1={p.x} y1={p.y} x2={q.x} y2={q.y} className="edge" />
            <text x={(p.x + q.x) / 2} y={(p.y + q.y) / 2 - 6} textAnchor="middle" className="msg-label">{label}</text>
          </g>
        )
      })}
      {Object.entries(BOXES).map(([name, pos]) => (
        <g key={name} role="button" tabIndex={0} aria-label={`Table ${name}`} aria-pressed={selected === name}
           className={`node ${selected === name ? 'node-selected' : ''}`} onClick={() => onSelect(name)}
           onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelect(name))}>
          <rect x={pos.x} y={pos.y} width={BW} height={BH} rx="6" />
          <text x={pos.x + BW / 2} y={pos.y + 28} textAnchor="middle" className="node-title">{name}</text>
          <text x={pos.x + BW / 2} y={pos.y + 48} textAnchor="middle" className="node-sub">
            {count(name) === undefined ? '' : `${formatInt(count(name))} row(s)`}
          </text>
        </g>
      ))}
    </svg>
  )
}

function Relationship({ focus, setFocus }) {
  const file = useApi((signal) => (focus?.type === 'file' ? api.getFile(focus.id, signal) : Promise.resolve(null)), [focus?.type, focus?.id])
  const chunk = useApi((signal) => (focus?.type === 'chunk' ? api.getChunk(focus.id, signal) : Promise.resolve(null)), [focus?.type, focus?.id])
  if (!focus) return <p className="empty">Select a row in <code>files</code> or <code>chunks</code> to follow its relationships.</p>

  if (focus.type === 'file') {
    return (
      <AsyncState state={file} onRetry={() => file.reload()}>
        {(f) => (
          <div>
            <h3>File <code>{f.file_id.slice(0, 8)}…</code> → its ordered chunk references (file_chunks)</h3>
            <p className="muted">{f.filename} · {f.chunk_count} manifest row(s) · <Link to={`/files/${f.file_id}`}>open in File Storage</Link></p>
            {f.chunks.length === 0 ? <p className="empty">No manifest rows.</p> : (
              <div className="table-wrap short">
                <table>
                  <thead><tr><th>chunk_index</th><th>chunk_hash</th><th>chunk_size</th><th>chunks.ref_count</th></tr></thead>
                  <tbody>
                    {f.chunks.map((c) => (
                      <tr key={c.chunk_index}>
                        <td>{c.chunk_index}</td>
                        <td><button className="link-btn" onClick={() => setFocus({ type: 'chunk', id: c.chunk_hash })}>{c.chunk_hash.slice(0, 12)}…</button></td>
                        <td>{c.chunk_size}</td>
                        <td>{c.ref_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </AsyncState>
    )
  }
  return (
    <AsyncState state={chunk} onRetry={() => chunk.reload()}>
      {(c) => (
        <div>
          <h3>Chunk <code>{c.hash.slice(0, 12)}…</code> → files that reference it</h3>
          <p>
            Stored <code>ref_count</code> = <strong>{c.ref_count}</strong>; manifest rows counted now ={' '}
            <strong>{c.actual_references}</strong>{' '}
            <StatusBadge tone={c.ref_count === c.actual_references ? 'ok' : 'error'}>
              {c.ref_count === c.actual_references ? 'invariant holds' : 'MISMATCH'}
            </StatusBadge>
          </p>
          <div className="table-wrap short">
            <table>
              <thead><tr><th>file</th><th>position (chunk_index)</th></tr></thead>
              <tbody>
                {c.references.map((r) => (
                  <tr key={`${r.file_id}-${r.chunk_index}`}>
                    <td><button className="link-btn" onClick={() => setFocus({ type: 'file', id: r.file_id })}>{r.filename}</button></td>
                    <td>{r.chunk_index}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </AsyncState>
  )
}

export default function DatabasePage() {
  const schema = useApi((signal) => api.inspectSchema(signal))
  const [table, setTable] = useState('files')
  const [offset, setOffset] = useState(0)
  const [row, setRow] = useState(null)
  const [focus, setFocus] = useState(null)
  const rows = useApi((signal) => api.inspectRows(table, { limit: PAGE, offset }, signal), [table, offset])

  const pick = (name) => { setTable(name); setOffset(0); setRow(null) }
  const select = (r) => {
    setRow(r)
    if (table === 'files') setFocus({ type: 'file', id: r.file_id })
    else if (table === 'chunks') setFocus({ type: 'chunk', id: r.hash })
    else if (table === 'file_chunks') setFocus({ type: 'file', id: r.file_id })
    else if (table === 'operations' && r.file_id) setFocus({ type: 'file', id: r.file_id })
  }

  return (
    <AsyncState state={schema} onRetry={() => schema.reload()}>
      {(s) => {
        const info = s.tables.find((t) => t.name === table)
        const colInfo = Object.fromEntries(info.columns.map((c) => [c.name, c]))
        const inv = s.ref_count_invariant
        return (
          <>
            <Card title="Entity relationships" aside={<StatusBadge tone="ok">live row counts</StatusBadge>}>
              <ErDiagram tables={s.tables} selected={table} onSelect={pick} />
              <p className="muted">Select a table box to browse it. Schema version {s.schema_version}. Foreign keys are enforced on every connection.</p>
              <div className={`notice ${inv.mismatches === 0 ? 'notice-ok' : 'notice-error'}`}>
                <strong>Reference-count invariant</strong>
                <code>chunk.ref_count = COUNT(committed manifest occurrences referencing that chunk)</code>
                <span>
                  {inv.mismatches === 0
                    ? `Holds for all ${formatInt(inv.chunks_checked)} chunk(s).`
                    : `${inv.mismatches} of ${formatInt(inv.chunks_checked)} chunk(s) violate it.`}
                </span>
              </div>
            </Card>

            <Card title="Table browser" aside={
              <div className="tabs" role="tablist">
                {s.tables.map((t) => (
                  <button key={t.name} role="tab" aria-selected={table === t.name} className={table === t.name ? 'active' : ''} onClick={() => pick(t.name)}>
                    {t.name}
                  </button>
                ))}
              </div>
            }>
              <p className="muted">{info.description} <em>Read-only.</em></p>
              <AsyncState state={rows} onRetry={() => rows.reload()} isEmpty={(d) => d.total === 0} empty={`The ${table} table is empty.`}>
                {(d) => (
                  <>
                    <div className="table-wrap">
                      <table className="rows">
                        <thead>
                          <tr>
                            {d.columns.map((c) => {
                              const ci = colInfo[c]
                              return (
                                <th key={c} title={ci?.description}>
                                  {c}
                                  {ci?.primary_key_position > 0 && <span className="key pk">PK{info.columns.filter((x) => x.primary_key_position).length > 1 ? ci.primary_key_position : ''}</span>}
                                  {ci?.foreign_key && <span className="key fk" title={`on delete ${ci.foreign_key.on_delete}`}>FK→{ci.foreign_key.table}.{ci.foreign_key.column}</span>}
                                </th>
                              )
                            })}
                          </tr>
                        </thead>
                        <tbody>
                          {d.rows.map((r, i) => (
                            <tr key={i} onClick={() => select(r)} className={row === r ? 'row-selected' : ''} tabIndex={0}
                                onKeyDown={(e) => e.key === 'Enter' && select(r)}>
                              {d.columns.map((c) => (
                                <td key={c}>{String(r[c] ?? 'NULL').length > 24 && /^[0-9a-f]{32,}$/.test(String(r[c])) ? <Hash value={String(r[c])} length={10} /> : String(r[c] ?? 'NULL')}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <Pagination total={d.total} limit={PAGE} offset={offset} onChange={setOffset} />
                  </>
                )}
              </AsyncState>
            </Card>

            <div className="two-col">
              <Card title="Record detail">
                {!row ? <p className="empty">Select a row to see every field with its meaning.</p> : (
                  <dl className="defs">
                    {Object.entries(row).map(([k, v]) => (
                      <div key={k}>
                        <dt>{k}</dt>
                        <dd><code>{String(v ?? 'NULL')}</code><br /><small className="muted">{colInfo[k]?.description}</small></dd>
                      </div>
                    ))}
                  </dl>
                )}
              </Card>
              <Card title="Relationship navigation">
                <Relationship focus={focus} setFocus={setFocus} />
              </Card>
            </div>
          </>
        )
      }}
    </AsyncState>
  )
}
