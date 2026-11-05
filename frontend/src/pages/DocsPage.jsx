import { useEffect, useMemo, useState } from 'react'
import Markdown from '../components/Markdown'
import { Card, ErrorNotice, Notice, Spinner } from '../components/ui'

// The project documentation in the repository is the single source of truth: these files are
// bundled (not copied into components) and rendered as-is.
const FILES = import.meta.glob('../../../docs/**/*.md', { query: '?raw', import: 'default' })

// The original System Design and Feasibility reports are expected in docs/references/. The banner below is derived from
// what is actually bundled, so it can never claim alignment that the repository cannot back up.
const REFERENCES = Object.keys(import.meta.glob('../../../docs/references/*.{md,pdf,docx,txt}', { query: '?url', import: 'default' }))

const key = (rel) => `../../../docs/${rel}`

export const SECTIONS = [
  ['Requirements', [
    ['Functional & non-functional requirements, assumptions', 'architecture/requirements.md'],
  ]],
  ['Architecture', [
    ['High-level design (HLD)', 'architecture/hld.md'],
    ['Low-level design (LLD)', 'architecture/lld.md'],
    ['Sequence diagrams', 'architecture/sequences.md'],
    ['Data flow & API communication', 'architecture/data-flow.md'],
    ['CDC specification', 'architecture/cdc-specification.md'],
  ]],
  ['Data & API', [
    ['Database schema', 'database/schema.md'],
    ['API contracts', 'api/planned-contracts.md'],
  ]],
  ['Reliability & security', [
    ['Consistency protocol & failure handling', 'architecture/consistency-protocol.md'],
    ['Hardening review', 'architecture/hardening-review.md'],
    ['Security & scalability limitations', 'architecture/security-and-scalability.md'],
    ['Actual data vs illustration', 'architecture/explainability.md'],
    ['Reliability engineering (fault lab)', 'architecture/reliability-engineering.md'],
    ['Performance benchmarking', 'architecture/benchmarking.md'],
    ['Capacity model & scalability', 'architecture/scalability.md'],
  ]],
  ['Verification & submission', [
    ['Final verification report', 'verification/final-verification-report.md'],
    ['Requirements traceability matrix', 'verification/requirements-traceability.md'],
    ['Security review', 'verification/security-review.md'],
    ['Demonstration guide', 'verification/demo-guide.md'],
  ]],
  ['Decisions & roadmap', [
    ['ADR-0001 CDC over fixed-size chunking', 'decisions/0001-cdc-over-fixed-size-chunking.md'],
    ['ADR-0002 SHA-256 content addressing', 'decisions/0002-sha256-content-addressing.md'],
    ['ADR-0003 SQLite for metadata', 'decisions/0003-sqlite-for-metadata.md'],
    ['ADR-0004 Local filesystem chunk store', 'decisions/0004-local-filesystem-chunk-store.md'],
    ['ADR-0005 Sequential processing', 'decisions/0005-sequential-processing.md'],
    ['ADR-0006 Separate metadata and bytes', 'decisions/0006-separate-metadata-and-bytes.md'],
    ['ADR-0007 Consistency and recovery', 'decisions/0007-consistency-and-recovery.md'],
    ['ADR-0008 Isolated experiment subsystems', 'decisions/0008-isolated-experiment-subsystems.md'],
    ['Roadmap', 'roadmap/phases.md'],
  ]],
]

// Resolve a relative markdown link against the document that contains it.
function resolve(current, href) {
  const parts = current.split('/').slice(0, -1)
  href.split('/').forEach((p) => {
    if (p === '..') parts.pop()
    else if (p !== '.') parts.push(p)
  })
  return parts.join('/')
}

export default function DocsPage() {
  const [current, setCurrent] = useState(SECTIONS[0][1][0][1])
  const [state, setState] = useState({ source: null, error: null })
  const loader = FILES[key(current)]

  useEffect(() => {
    let cancelled = false
    setState({ source: null, error: null })
    if (!loader) {
      setState({ source: null, error: { message: 'This document is not bundled in the build.' } })
      return undefined
    }
    loader().then(
      (source) => !cancelled && setState({ source, error: null }),
      (error) => !cancelled && setState({ source: null, error }),
    )
    return () => { cancelled = true }
  }, [current, loader])

  const [filter, setFilter] = useState('')
  const flat = useMemo(() => SECTIONS.flatMap(([, items]) => items), [])
  const at = flat.findIndex(([, f]) => f === current)
  const prev = at > 0 ? flat[at - 1] : null
  const next = at >= 0 && at < flat.length - 1 ? flat[at + 1] : null
  const q = filter.trim().toLowerCase()

  const title = useMemo(
    () => SECTIONS.flatMap(([, items]) => items).find(([, f]) => f === current)?.[0] || current,
    [current])

  return (
    <div className="docs-layout">
      <nav className="docs-nav" aria-label="Documentation">
        <input type="search" placeholder="Filter documents…" aria-label="Filter documents" value={filter}
               onChange={(e) => setFilter(e.target.value)} style={{ width: '100%' }} />
        {q && flat.every(([label]) => !label.toLowerCase().includes(q)) && <p className="muted">No document matches “{filter}”.</p>}
        {SECTIONS.map(([group, allItems]) => {
          const items = q ? allItems.filter(([label]) => label.toLowerCase().includes(q)) : allItems
          if (!items.length) return null
          return (
          <div key={group}>
            <h3>{group}</h3>
            <ul className="plain">
              {items.map(([label, file]) => (
                <li key={file}>
                  <button className={`link-btn ${file === current ? 'active' : ''}`} onClick={() => setCurrent(file)}
                          aria-current={file === current ? 'page' : undefined}>
                    {label}
                  </button>
                </li>
              ))}
            </ul>
          </div>
          )
        })}
      </nav>
      <div className="docs-body">
      {REFERENCES.length === 0 ? (
        <Notice tone="info" title="Original reports are not stored in the repository">
          <code>docs/references/</code> is empty. The System Design Report and Feasibility Report were reviewed separately; the result,
          including every deviation, is in the{' '}
          <button className="link-btn" onClick={() => setCurrent('verification/requirements-traceability.md')}>requirements traceability matrix</button>.
        </Notice>
      ) : (
        <Notice tone="info" title="Reference documents present">
          {REFERENCES.length} file(s) in <code>docs/references/</code>. Alignment status is recorded only in the{' '}
          <button className="link-btn" onClick={() => setCurrent('verification/requirements-traceability.md')}>requirements traceability matrix</button>.
        </Notice>
      )}
      <Card title={title} className="doc-reader">
        <p className="muted"><code>docs/{current}</code> — rendered from the repository</p>
        {state.error && <ErrorNotice error={state.error} />}
        {!state.error && state.source === null && <Spinner />}
        {state.source !== null && (
          <Markdown source={state.source} onNavigate={(href) => {
            const target = resolve(current, href)
            if (FILES[key(target)]) setCurrent(target)
          }} />
        )}
        <div className="pagination" style={{ justifyContent: 'space-between' }}>
          {prev ? <button onClick={() => setCurrent(prev[1])}>← {prev[0]}</button> : <span />}
          {next ? <button onClick={() => setCurrent(next[1])}>{next[0]} →</button> : <span />}
        </div>
      </Card>
      </div>
    </div>
  )
}
