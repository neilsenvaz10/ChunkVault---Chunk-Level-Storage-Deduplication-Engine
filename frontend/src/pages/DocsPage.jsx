import { useEffect, useMemo, useState } from 'react'
import Markdown from '../components/Markdown'
import { Card, ErrorNotice, Spinner } from '../components/ui'

// The project documentation in the repository is the single source of truth: these files are
// bundled (not copied into components) and rendered as-is.
const FILES = import.meta.glob('../../../docs/**/*.md', { query: '?raw', import: 'default' })

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

  const title = useMemo(
    () => SECTIONS.flatMap(([, items]) => items).find(([, f]) => f === current)?.[0] || current,
    [current])

  return (
    <div className="docs-layout">
      <nav className="docs-nav" aria-label="Documentation">
        {SECTIONS.map(([group, items]) => (
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
        ))}
      </nav>
      <Card title={title} className="docs-body">
        <p className="muted"><code>docs/{current}</code> — rendered from the repository</p>
        {state.error && <ErrorNotice error={state.error} />}
        {!state.error && state.source === null && <Spinner />}
        {state.source !== null && (
          <Markdown source={state.source} onNavigate={(href) => {
            const target = resolve(current, href)
            if (FILES[key(target)]) setCurrent(target)
          }} />
        )}
      </Card>
    </div>
  )
}
