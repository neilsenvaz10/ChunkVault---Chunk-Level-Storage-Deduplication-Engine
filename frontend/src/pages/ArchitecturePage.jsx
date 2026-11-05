import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Card } from '../components/ui'
import ApiExplorer from './architecture/ApiExplorer'
import CdcExplorer from './architecture/CdcExplorer'
import ConsistencyView from './architecture/ConsistencyView'
import FaultLab from './architecture/FaultLab'
import { ComponentDetails, ComponentDiagram, DataFlowDiagram, DiagramViewport, LayerLegend, SequenceDiagram } from './architecture/diagrams'
import { COMPONENTS, CONCEPTS, FOCUS_ALIASES, RESTORE_SEQUENCE, UPLOAD_SEQUENCE } from './architecture/data'
import { RESTORE_STAGES, UPLOAD_STAGES } from './architecture/lifecycles'
import Walkthrough from './architecture/Walkthrough'

const TABS = [
  ['components', 'Components'],
  ['uploadlife', 'Upload lifecycle'],
  ['restorelife', 'Restore lifecycle'],
  ['cdc', 'Chunking (CDC)'],
  ['consistency', 'Consistency protocol'],
  ['faults', 'Fault injection & recovery'],
  ['api', 'API explorer'],
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
      <div className="tabs" role="tablist" aria-label="Architecture views">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'components' && (
        <Card title="Component diagram">
          <p className="muted">
            Browser → React → FastAPI → CDC → SHA-256 → Deduplication → SQLite + local chunk store, plus restoration and integrity
            auditing. One process with modular internals — these are the project&rsquo;s real modules, not microservices.
            Select a component; its dependencies are highlighted.
          </p>
          <LayerLegend />
          <div className="split">
            <DiagramViewport minWidth={640} label="Component diagram">
              <ComponentDiagram selectedId={selectedId} onSelect={setSelectedId} />
            </DiagramViewport>
            <div className="split-side">
              <ComponentDetails component={selected} onSelect={setSelectedId} />
            </div>
          </div>
        </Card>
      )}
      {tab === 'uploadlife' && <Walkthrough key="up" kind="upload" stages={UPLOAD_STAGES} />}
      {tab === 'restorelife' && <Walkthrough key="re" kind="restore" stages={RESTORE_STAGES} />}
      {tab === 'cdc' && <CdcExplorer />}
      {tab === 'consistency' && <ConsistencyView />}
      {tab === 'faults' && <FaultLab />}
      {tab === 'api' && <ApiExplorer />}
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
