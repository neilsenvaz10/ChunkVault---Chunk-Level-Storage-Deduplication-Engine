import { useEffect, useState } from 'react'
import { Card, StatusBadge } from '../components/ui'

// Real trigger/view definitions are read from the schema file itself (single source of truth).
const SCHEMA = import.meta.glob('../../../backend/app/database/schema.sql', { query: '?raw', import: 'default' })

export function extractTriggers(sql) {
  const names = ['trg_file_chunks_ref_inc', 'trg_file_chunks_ref_dec', 'v_ref_count_mismatch']
  return names.map((n) => {
    const re = new RegExp(`CREATE (?:TRIGGER|VIEW) IF NOT EXISTS ${n}[\\s\\S]*?(?:END;|HAVING[^;]*;)`)
    const m = sql.match(re)
    return [n, m ? m[0] : null]
  })
}

// ILLUSTRATIVE numbers only: a fixed worked example, not read from the database.
export const STEPS = [
  { title: 'Upload file A', a: ['X', 'Y'], b: [], refs: { X: 1, Y: 1 }, text: 'File A\'s manifest has two rows. Each INSERT into file_chunks fires the trigger, so chunks X and Y each get ref_count 1.' },
  { title: 'Upload identical file B', a: ['X', 'Y'], b: ['X', 'Y'], refs: { X: 2, Y: 2 }, text: 'File B is a new logical file with its own manifest rows pointing at the same chunks. No chunk bytes are written; the trigger raises each count to 2.' },
  { title: 'Both files reference shared chunks', a: ['X', 'Y'], b: ['X', 'Y'], refs: { X: 2, Y: 2 }, text: 'ref_count equals the number of manifest rows that reference the chunk. A chunk repeated twice inside one file would count twice.' },
  { title: 'Delete file A', a: [], b: ['X', 'Y'], refs: { X: 1, Y: 1 }, text: 'Deleting the file row cascades to its manifest rows; each removed row fires the decrement trigger. The counts drop to 1 and no chunk file is touched.' },
  { title: 'File B remains restorable', a: [], b: ['X', 'Y'], refs: { X: 1, Y: 1 }, text: 'B\'s manifest still references X and Y, and their bytes are still stored, so restoring B verifies and succeeds.' },
  { title: 'Reclaiming', a: [], b: [], refs: { X: 0, Y: 0 }, text: 'Only after the last manifest row is gone does ref_count reach 0. Only then may the chunk row and its file be removed — and the delete also requires that no manifest row still uses the chunk.' },
]

export default function RefCountWalkthrough() {
  const [i, setI] = useState(0)
  const [sql, setSql] = useState(null)
  useEffect(() => {
    const load = Object.values(SCHEMA)[0]
    if (!load) return undefined
    let cancelled = false
    load().then((s) => !cancelled && setSql(s), () => undefined)
    return () => { cancelled = true }
  }, [])
  const s = STEPS[i]
  const defs = sql ? extractTriggers(sql) : []

  return (
    <Card title="Reference-count walkthrough" aside={<StatusBadge tone="warn">illustrative example, not your data</StatusBadge>}>
      <p className="notice notice-ok">
        <strong>Invariant:</strong>
        <code>ref_count = number of committed manifest occurrences referencing the chunk</code>
      </p>
      <div className="stepper-controls" role="group" aria-label="Reference-count steps">
        <button onClick={() => setI((n) => Math.max(0, n - 1))} disabled={i === 0}>Previous</button>
        <span aria-live="polite">Step {i + 1} of {STEPS.length}</span>
        <button className="primary" onClick={() => setI((n) => Math.min(STEPS.length - 1, n + 1))} disabled={i === STEPS.length - 1}>Next</button>
        <button onClick={() => setI(0)} disabled={i === 0}>Reset</button>
      </div>
      <div className="details" aria-live="polite">
        <h3>{i + 1}. {s.title}</h3>
        <p>{s.text}</p>
        <div className="two-col">
          <table aria-label="Illustrative manifests">
            <thead><tr><th>File</th><th>Manifest (chunk ids)</th></tr></thead>
            <tbody>
              <tr><td>A</td><td>{s.a.length ? s.a.join(', ') : <span className="muted">deleted</span>}</td></tr>
              <tr><td>B</td><td>{s.b.length ? s.b.join(', ') : <span className="muted">{i === 0 ? 'not uploaded yet' : 'deleted'}</span>}</td></tr>
            </tbody>
          </table>
          <table aria-label="Illustrative reference counts">
            <thead><tr><th>Chunk</th><th>ref_count (illustrative)</th><th>Reclaimable?</th></tr></thead>
            <tbody>
              {Object.entries(s.refs).map(([c, n]) => (
                <tr key={c}><td>{c}</td><td>{n}</td><td>{n === 0 ? 'yes — no manifest references' : 'no'}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <h3>Why triggers, not application code</h3>
      <ul>
        <li>The database enforces the rule for <em>every</em> writer and every path, including rows removed by the ON DELETE CASCADE of a file — application code would have to remember each case.</li>
        <li>The counter change and the manifest change happen in the same transaction, so they commit or roll back together.</li>
        <li>There is a single mechanism: the application never increments or decrements counts itself, and a view audits the invariant.</li>
      </ul>
      <h3>The actual definitions (read from backend/app/database/schema.sql)</h3>
      {defs.length === 0 ? <p className="empty">Schema source not available in this build.</p> : defs.map(([name, text]) => (
        <div key={name}>
          <p className="muted"><code>{name}</code></p>
          {text ? <pre className="code"><code>{text}</code></pre> : <p className="empty">Definition not found.</p>}
        </div>
      ))}
    </Card>
  )
}
