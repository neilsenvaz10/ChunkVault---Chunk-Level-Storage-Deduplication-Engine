import { useState } from 'react'
import { COMPONENTS, DATA_FLOWS, EDGES } from './data'

const NODE_H = 46

const byId = Object.fromEntries(COMPONENTS.map((c) => [c.id, c]))
const center = (c) => ({ x: c.x + c.w / 2, y: c.y + NODE_H / 2 })

export function ComponentDiagram({ selectedId, onSelect }) {
  return (
    <svg viewBox="0 0 860 470" className="diagram" role="group" aria-label="Component diagram">
      <defs>
        <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" className="arrowhead" />
        </marker>
      </defs>
      {EDGES.map(([a, b]) => {
        const p = center(byId[a])
        const q = center(byId[b])
        return <line key={`${a}-${b}`} x1={p.x} y1={p.y} x2={q.x} y2={q.y}
                     className={`edge ${selectedId === a || selectedId === b ? 'edge-active' : ''}`} markerEnd="url(#arr)" />
      })}
      {COMPONENTS.map((c) => (
        <g key={c.id} role="button" tabIndex={0} aria-label={c.name} aria-pressed={selectedId === c.id}
           className={`node ${selectedId === c.id ? 'node-selected' : ''}`}
           onClick={() => onSelect(c.id)}
           onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelect(c.id))}>
          <rect x={c.x} y={c.y} width={c.w} height={NODE_H} rx="6" />
          <text x={c.x + c.w / 2} y={c.y + 20} textAnchor="middle" className="node-title">{c.name}</text>
          <text x={c.x + c.w / 2} y={c.y + 36} textAnchor="middle" className="node-sub">{c.layer}</text>
        </g>
      ))}
    </svg>
  )
}

export function ComponentDetails({ component }) {
  if (!component) return <p className="empty">Select a component to see its responsibility, interfaces and failure modes.</p>
  const list = (items) => (items.length ? <ul className="plain">{items.map((i) => <li key={i}>{i}</li>)}</ul> : <span className="muted">none</span>)
  return (
    <div className="details" aria-live="polite">
      <h3>{component.name}</h3>
      <p>{component.responsibility}</p>
      <dl className="defs">
        <dt>Inputs</dt><dd>{list(component.inputs)}</dd>
        <dt>Outputs</dt><dd>{list(component.outputs)}</dd>
        <dt>Dependencies</dt><dd>{list(component.dependencies)}</dd>
        <dt>Failure scenarios</dt><dd>{list(component.failures)}</dd>
        <dt>Backend module</dt><dd><code>{component.module}</code></dd>
        <dt>API endpoints</dt><dd>{list(component.endpoints)}</dd>
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
