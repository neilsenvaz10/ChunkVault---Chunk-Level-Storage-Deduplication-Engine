import { useEffect, useState } from 'react'
import { Card, ErrorNotice, Spinner } from '../components/ui'

// The repository's ADR documents are the source of truth; this view only parses their headings.
const FILES = import.meta.glob('../../../docs/decisions/*.md', { query: '?raw', import: 'default' })

export const SECTIONS = [
  ['Problem', 'Problem being solved'],
  ['Alternatives considered', 'Alternatives considered'],
  ['Decision', 'Selected approach'],
  ['Advantages', 'Advantages'],
  ['Disadvantages', 'Disadvantages'],
  ['Consequences', 'Consequences'],
  ['Scalability implications', 'Future scalability implications'],
]

// Split an ADR into its title, status line and "## heading" sections.
export function parseAdr(source) {
  const lines = source.split(/\r?\n/)
  const title = (lines.find((l) => l.startsWith('# ')) || '').replace(/^#\s*/, '')
  const status = (lines.find((l) => l.startsWith('**Status:**')) || '').replace(/\*\*/g, '')
  const sections = {}
  let current = null
  lines.forEach((l) => {
    const m = l.match(/^##\s+(.*)$/)
    if (m) {
      current = m[1].trim()
      sections[current] = []
    } else if (current) sections[current].push(l)
  })
  Object.keys(sections).forEach((k) => { sections[k] = sections[k].join('\n').trim() })
  return { title, status, sections }
}

function Body({ text }) {
  const items = text.split('\n').filter(Boolean)
  const bullets = items.filter((l) => /^\s*(-|\d+\.)\s/.test(l))
  if (bullets.length === items.length && items.length) {
    return <ul>{bullets.map((l) => <li key={l}>{l.replace(/^\s*(-|\d+\.)\s+/, '').replace(/\*\*(.*?)\*\*/g, '$1').replace(/`/g, '')}</li>)}</ul>
  }
  return <p>{text.replace(/\*\*(.*?)\*\*/g, '$1').replace(/`/g, '')}</p>
}

export default function AdrExplorer() {
  const [state, setState] = useState({ adrs: null, error: null })
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    let cancelled = false
    Promise.all(Object.entries(FILES).sort(([a], [b]) => a.localeCompare(b)).map(async ([path, load]) => {
      const source = await load()
      const file = path.split('/').pop()
      return { file, ...parseAdr(source) }
    })).then(
      (adrs) => { if (!cancelled) { setState({ adrs, error: null }); setSelected(adrs[0]?.file ?? null) } },
      (error) => !cancelled && setState({ adrs: null, error }),
    )
    return () => { cancelled = true }
  }, [])

  if (state.error) return <ErrorNotice error={state.error} />
  if (!state.adrs) return <Spinner label="Loading decision records…" />
  if (state.adrs.length === 0) return <p className="empty">No architecture decision records found in docs/decisions.</p>
  const adr = state.adrs.find((a) => a.file === selected) || state.adrs[0]

  return (
    <div className="docs-layout">
      <nav className="docs-nav" aria-label="Architecture decision records">
        <h3>Decisions</h3>
        <ul className="plain">
          {state.adrs.map((a) => (
            <li key={a.file}>
              <button className={`link-btn ${a.file === adr.file ? 'active' : ''}`} aria-current={a.file === adr.file ? 'page' : undefined}
                      onClick={() => setSelected(a.file)}>
                {a.title.replace(/^ADR-/, 'ADR-')}
              </button>
            </li>
          ))}
        </ul>
      </nav>
      <div>
        <Card title={adr.title}>
          <p className="muted">{adr.status} · source: <code>docs/decisions/{adr.file}</code></p>
        </Card>
        {SECTIONS.map(([key, label]) => (
          <Card key={key} title={label}>
            {adr.sections[key] ? <Body text={adr.sections[key]} /> : <p className="empty">Not recorded in this ADR.</p>}
          </Card>
        ))}
      </div>
    </div>
  )
}
