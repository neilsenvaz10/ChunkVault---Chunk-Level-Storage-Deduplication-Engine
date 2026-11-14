import { Link } from 'react-router-dom'
import { isCloudMode } from '../config/mode'

// Each stage: [focus key used by the architecture page, label shown, subtitle]. The focus key stays the same in both modes.
const STAGES = [
  ['React', 'React', 'client'],
  ['FastAPI', 'FastAPI', 'request layer'],
  ['CDC', 'CDC', 'chunk boundaries'],
  ['SHA-256', 'SHA-256', 'chunk identity'],
  ['Deduplication', 'Deduplication', 'reuse or store'],
  ['SQLite + Chunk Store', 'SQLite + Chunk Store', 'metadata + bytes'],
]
const CLOUD_LAST = ['SQLite + Chunk Store', 'Supabase PostgreSQL + Storage', 'metadata + chunk objects']

// Compact upload pipeline; every stage links to the architecture page.
export default function PipelineStrip() {
  const stages = isCloudMode() ? [...STAGES.slice(0, -1), CLOUD_LAST] : STAGES
  return (
    <ol className="pipeline" aria-label="Upload pipeline">
      {stages.map(([focus, name, sub], i) => (
        <li key={focus}>
          <Link to={`/architecture?focus=${encodeURIComponent(focus)}`} className="stage">
            <strong>{name}</strong>
            <small>{sub}</small>
          </Link>
          {i < stages.length - 1 && <span className="arrow" aria-hidden="true">→</span>}
        </li>
      ))}
    </ol>
  )
}
