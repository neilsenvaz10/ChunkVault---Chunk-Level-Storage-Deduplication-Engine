import { useState } from 'react'
import { COMPONENTS, DATA_FLOWS, EDGES, LAYERS, dependenciesOf, dependentsOf } from './data'

const NODE_H = 46
const byId = Object.fromEntries(COMPONENTS.map((c) => [c.id, c]))
const center = (c) => ({ x: c.x + c.w / 2, y: c.y + NODE_H / 2 })

// Scrollable, zoomable container for wide diagrams (keyboard reachable).
export function DiagramViewport({ children, minWidth = 640, label = 'Diagram' }) {
  const [zoom, setZoom] = useState(1)
  const clamp = (z) => Math.min(3, Math.max(0.6, Math.round(z * 10) / 10))
  return (
    <div className="viewport">
      <div className="viewport-controls" role="group" aria-label={`${label} zoom`}>
        <button onClick={() => setZoom((z) => clamp(z - 0.2))} aria-label="Zoom out" disabled={zoom <= 0.6}>−</button>
        <span aria-live="polite">{Math.round(zoom * 100)}%</span>
        <button onClick={() => setZoom((z) => clamp(z + 0.2))} aria-label="Zoom in" disabled={zoom >= 3}>+</button>
        <button onClick={() => setZoom(1)} aria-label="Reset zoom">Reset</button>
      </div>
      <div className="viewport-scroll" tabIndex={0} role="region" aria-label={`${label} (scrollable)`}>
        <div style={{ width: `${zoom * 100}%`, minWidth: minWidth * zoom }}>{children}</div>
      </div>
    </div>
  )
}

export function LayerLegend() {
  return (
    <ul className="legend" aria-label="Layers">
      {Object.entries(LAYERS).map(([id, l]) => (
        <li key={id}><span className={`swatch layer-${id}`} /> <strong>{l.label}</strong> — {l.note}</li>
      ))}
      <li><span className="swatch dep-swatch" /> uses the selected component</li>
      <li><span className="swatch dependent-swatch" /> used by the selected component</li>
    </ul>
  )
}

export function ComponentDiagram({ selectedId, onSelect }) {
  const uses = new Set(selectedId ? dependenciesOf(selectedId).map((d) => d.id) : [])
  const usedBy = new Set(selectedId ? dependentsOf(selectedId).map((d) => d.id) : [])
  return (
    <svg viewBox="0 0 860 460" className="diagram" role="group" aria-label="Component diagram">
      <defs>
        <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" className="arrowhead" />
        </marker>
      </defs>
      {EDGES.map(([a, b]) => {
        const p = center(byId[a])
        const q = center(byId[b])
        const active = selectedId && (a === selectedId || b === selectedId)
        return <line key={`${a}-${b}`} x1={p.x} y1={p.y} x2={q.x} y2={q.y}
                     className={`edge ${active ? 'edge-active' : ''}`} data-edge={`${a}->${b}`} markerEnd="url(#arr)" />
      })}
      {COMPONENTS.map((c) => {
        const rel = c.id === selectedId ? 'selected' : uses.has(c.id) ? 'uses' : usedBy.has(c.id) ? 'usedby' : ''
        return (
          <g key={c.id} role="button" tabIndex={0} aria-label={c.name} aria-pressed={selectedId === c.id}
             className={`node layer-${c.layer} ${rel ? `node-${rel}` : ''} ${selectedId && !rel ? 'node-dim' : ''}`}
             data-relation={rel || undefined}
             onClick={() => onSelect(c.id)}
             onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelect(c.id))}>
            <rect x={c.x} y={c.y} width={c.w} height={NODE_H} rx="6" />
            <text x={c.x + c.w / 2} y={c.y + 20} textAnchor="middle" className="node-title">{c.name}</text>
            <text x={c.x + c.w / 2} y={c.y + 36} textAnchor="middle" className="node-sub">{LAYERS[c.layer].label}</text>
          </g>
        )
      })}
    </svg>
  )
}

function List({ items, empty = 'none' }) {
  return items.length ? <ul className="plain">{items.map((i) => <li key={i}>{i}</li>)}</ul> : <span className="muted">{empty}</span>
}

export function ComponentDetails({ component, onSelect }) {
  if (!component) return <p className="empty">Select a component to see its purpose, interfaces, modules, dependencies and failure modes.</p>
  const uses = dependenciesOf(component.id)
  const usedBy = dependentsOf(component.id)
  const links = (items) => (items.length ? (
    <ul className="plain">
      {items.map((d) => <li key={d.id}><button className="link-btn" onClick={() => onSelect(d.id)}>{d.name}</button></li>)}
    </ul>
  ) : <span className="muted">none</span>)
  return (
    <div className="details" aria-live="polite">
      <h3>{component.name}</h3>
      <p><span className={`badge layer-badge layer-${component.layer}`}>{LAYERS[component.layer].label} layer</span> {component.purpose}</p>
      <dl className="defs">
        <dt>Inputs</dt><dd><List items={component.inputs} /></dd>
        <dt>Outputs</dt><dd><List items={component.outputs} /></dd>
        <dt>Depends on</dt><dd>{links(uses)}</dd>
        <dt>Used by</dt><dd>{links(usedBy)}</dd>
        <dt>Python modules</dt><dd><List items={component.modules} /></dd>
        <dt>React modules</dt><dd><List items={component.reactModules} /></dd>
        <dt>API endpoints</dt><dd><List items={component.endpoints} /></dd>
        <dt>Failure scenarios</dt><dd><List items={component.failures} /></dd>
        <dt>Design rationale</dt><dd>{component.rationale}</dd>
      </dl>
    </div>
  )
}

// Generic sequence diagram: lifelines, arrows, and a selectable step with an explanation.
export function SequenceDiagram({ spec, title }) {
  const [selected, setSelected] = useState(0)
  const colW = 120
  const left = 70
  const top = 40
  const rowH = 40
  const width = left * 2 + colW * (spec.actors.length - 1)
  const height = top + rowH * (spec.messages.length + 1)
  const x = (i) => left + i * colW
  return (
    <div>
      <DiagramViewport minWidth={width} label={title}>
        <svg viewBox={`0 0 ${width} ${height}`} className="diagram" role="group" aria-label={title}>
          <defs>
            <marker id="seqarr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0 L10 5 L0 10 z" className="arrowhead" />
            </marker>
          </defs>
          {spec.actors.map((a, i) => (
            <g key={a}>
              <text x={x(i)} y={20} textAnchor="middle" className="node-title">{a}</text>
              <line x1={x(i)} y1={top - 6} x2={x(i)} y2={height - 10} className="lifeline" />
            </g>
          ))}
          {spec.messages.map((m, i) => {
            const y = top + rowH * (i + 1)
            const self = m.from === m.to
            return (
              <g key={m.label} role="button" tabIndex={0} aria-label={m.label} aria-pressed={selected === i}
                 className={`msg ${selected === i ? 'msg-selected' : ''}`} onClick={() => setSelected(i)}
                 onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), setSelected(i))}>
                <rect x="0" y={y - 22} width={width} height="34" className="msg-hit" />
                {self ? (
                  <path d={`M${x(m.from)} ${y - 8} h28 v14 h-28`} className="msg-line" fill="none" markerEnd="url(#seqarr)" />
                ) : (
                  <line x1={x(m.from)} y1={y} x2={x(m.to)} y2={y} className="msg-line" markerEnd="url(#seqarr)" />
                )}
                <text x={self ? x(m.from) + 36 : (x(m.from) + x(m.to)) / 2} y={y - 6} textAnchor={self ? 'start' : 'middle'} className="msg-label">
                  {m.label}
                </text>
              </g>
            )
          })}
        </svg>
      </DiagramViewport>
      <div className="details" aria-live="polite">
        <strong>{spec.messages[selected].label}</strong>
        <p>{spec.messages[selected].detail}</p>
      </div>
    </div>
  )
}

const FLOW_TYPES = [
  ['bytes', 'File / chunk bytes'],
  ['digest', 'SHA-256 identifiers'],
  ['metadata', 'Metadata records'],
  ['api', 'API requests / responses'],
]

export function DataFlowDiagram() {
  const [shown, setShown] = useState(new Set(FLOW_TYPES.map(([t]) => t)))
  const toggle = (t) => setShown((s) => {
    const n = new Set(s)
    if (n.has(t)) n.delete(t)
    else n.add(t)
    return n
  })
  return (
    <div>
      <div className="row" role="group" aria-label="Flow types">
        {FLOW_TYPES.map(([t, label]) => (
          <label key={t} className={`flow-toggle flow-${t}`}>
            <input type="checkbox" checked={shown.has(t)} onChange={() => toggle(t)} /> {label}
          </label>
        ))}
      </div>
      <ul className="flows">
        {DATA_FLOWS.filter((f) => shown.has(f.type)).map((f) => (
          <li key={`${f.from}-${f.to}-${f.label}`} className={`flow flow-${f.type}`}>
            <span className="flow-end">{f.from}</span>
            <span className="flow-arrow">⟶</span>
            <span className="flow-end">{f.to}</span>
            <span className="flow-label">{f.label}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
