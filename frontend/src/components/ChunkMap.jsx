import { formatBytes } from '../utils/format'

// Ordered chunk blocks; width is proportional to chunk size. The colour class comes from
// `classify(chunk)` so each caller states which kind of evidence it is using.
export default function ChunkMap({ chunks, classify, selectedIndex, onSelect, label = 'Chunk map' }) {
  if (!chunks.length) return <p className="empty">This file has no chunks (it is empty).</p>
  return (
    <div className="chunk-map" role="group" aria-label={label}>
      {chunks.map((c) => {
        const cls = classify(c)
        return (
          <button
            key={c.chunk_index}
            className={`chunk chunk-${cls} ${selectedIndex === c.chunk_index ? 'selected' : ''}`}
            style={{ flexGrow: c.chunk_size, flexBasis: 0, minWidth: 18 }}
            onClick={() => onSelect?.(c)}
            title={`#${c.chunk_index} · ${formatBytes(c.chunk_size)} · ${c.chunk_hash.slice(0, 12)}…`}
            aria-label={`Chunk ${c.chunk_index}, ${formatBytes(c.chunk_size)}, ${cls}`}
            aria-pressed={selectedIndex === c.chunk_index}
          >
            {c.chunk_index}
          </button>
        )
      })}
    </div>
  )
}

export function Legend({ items }) {
  return (
    <ul className="legend">
      {items.map(([cls, text]) => (
        <li key={cls}><span className={`swatch chunk-${cls}`} /> {text}</li>
      ))}
    </ul>
  )
}
