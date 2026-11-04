import { marked } from 'marked'
import { useMemo } from 'react'
import Mermaid from './Mermaid'

// Renders repository markdown. Mermaid code blocks become diagrams. The input is project
// documentation bundled at build time from the repository (trusted content, not user input).
// Links to other .md files are handed to onNavigate so the docs page can open them in-app.
export default function Markdown({ source, onNavigate }) {
  const tokens = useMemo(() => marked.lexer(source), [source])

  const onClick = (e) => {
    const a = e.target.closest?.('a')
    if (!a) return
    const href = a.getAttribute('href') || ''
    if (/^https?:/.test(href)) {
      a.target = '_blank'
      a.rel = 'noreferrer'
      return
    }
    if (/\.md(#.*)?$/.test(href) && onNavigate) {
      e.preventDefault()
      onNavigate(href.replace(/#.*$/, ''))
    }
  }

  return (
    <div className="markdown" onClick={onClick}>
      {tokens.map((t, i) =>
        t.type === 'code' && t.lang === 'mermaid' ? (
          <Mermaid key={i} chart={t.text} />
        ) : (
          <div key={i} dangerouslySetInnerHTML={{ __html: marked.parser([t]) }} />
        ),
      )}
    </div>
  )
}
