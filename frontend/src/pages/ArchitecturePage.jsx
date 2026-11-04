import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Card } from '../components/ui'
import { ComponentDetails, ComponentDiagram, DataFlowDiagram, SequenceDiagram } from './architecture/diagrams'
import { COMPONENTS, CONCEPTS, FOCUS_ALIASES, RESTORE_SEQUENCE, UPLOAD_SEQUENCE } from './architecture/data'

const TABS = [
  ['components', 'Components'],
  ['upload', 'Upload sequence'],
  ['restore', 'Restore sequence'],
  ['dataflow', 'Data flow'],
  ['concepts', 'Concepts'],
]

export default function ArchitecturePage() {
  const [params] = useSearchParams()
  const [tab, setTab] = useState('components')
  const [selectedId, setSelectedId] = useState(FOCUS_ALIASES[params.get('focus')] || null)
  const selected = COMPONENTS.find((c) => c.id === selectedId) || null

  return (
    <>
      <div className="tabs" role="tablist">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'components' && (
        <Card title="Component diagram">
          <p className="muted">
            Client → React → FastAPI → CDC → SHA-256 → Deduplication → SQLite + local chunk store, plus the restoration and
            integrity-auditing components. One process with modular internals — not microservices. Select a component.
          </p>
          <ComponentDiagram selectedId={selectedId} onSelect={setSelectedId} />
          <ComponentDetails component={selected} />
        </Card>
      )}
      {tab === 'upload' && (
        <Card title="Upload sequence">
          <p className="muted">Select a step for its explanation.</p>
          <SequenceDiagram spec={UPLOAD_SEQUENCE} title="Upload sequence diagram" />
        </Card>
      )}
      {tab === 'restore' && (
        <Card title="Restore sequence">
          <p className="muted">Select a step for its explanation.</p>
          <SequenceDiagram spec={RESTORE_SEQUENCE} title="Restore sequence diagram" />
        </Card>
      )}
      {tab === 'dataflow' && (
        <Card title="Data flow">
          <p className="muted">
            File bytes, SHA-256 identifiers, metadata records and API traffic are different kinds of data that travel
            different paths. Toggle a type to isolate it.
          </p>
          <DataFlowDiagram />
        </Card>
      )}
      {tab === 'concepts' && (
        <Card title="Architecture concepts">
          {CONCEPTS.map(([title, text]) => (
            <details key={title} className="concept">
              <summary>{title}</summary>
              <p>{text}</p>
            </details>
          ))}
        </Card>
      )}
    </>
  )
}
