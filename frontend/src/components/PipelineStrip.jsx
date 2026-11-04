import { Link } from 'react-router-dom'

const STAGES = [
  ['React', 'client'],
  ['FastAPI', 'request layer'],
  ['CDC', 'chunk boundaries'],
  ['SHA-256', 'chunk identity'],
  ['Deduplication', 'reuse or store'],
  ['SQLite + Chunk Store', 'metadata + bytes'],
]

// Compact upload pipeline; every stage links to the architecture page.
export default function PipelineStrip() {
  return (
    <ol className="pipeline" aria-label="Upload pipeline">
      {STAGES.map(([name, sub], i) => (
        <li key={name}>
          <Link to={`/architecture?focus=${encodeURIComponent(name)}`} className="stage">
            <strong>{name}</strong>
            <small>{sub}</small>
          </Link>
          {i < STAGES.length - 1 && <span className="arrow" aria-hidden="true">→</span>}
        </li>
      ))}
    </ol>
  )
}
