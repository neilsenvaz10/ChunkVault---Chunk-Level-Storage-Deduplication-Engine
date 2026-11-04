import { useState } from 'react'
import ChunkMap, { Legend } from '../../components/ChunkMap'
import { AsyncState, Card } from '../../components/ui'
import { useApi } from '../../hooks/useApi'
import { api } from '../../services/api'
import { formatBytes, formatPercent } from '../../utils/format'

// Conceptual picture: bar lengths are drawn from the configured parameters, but the byte
// stream is schematic (it is not a real file).
function ConceptDiagram({ cfg }) {
  const W = 760
  const scale = (n) => (n / cfg.max_chunk_size) * (W - 40)
  const x0 = 20
  return (
    <svg viewBox={`0 0 ${W} 190`} className="diagram" role="img"
         aria-label={`Conceptual chunk: no boundary before ${cfg.min_chunk_size} bytes, boundary possible after, forced cut at ${cfg.max_chunk_size} bytes`}>
      <text x={x0} y="18" className="node-sub">Conceptual illustration (schematic bytes, bar lengths follow the configured sizes)</text>
      <rect x={x0} y="30" width={scale(cfg.min_chunk_size)} height="30" className="zone zone-min" />
      <rect x={x0 + scale(cfg.min_chunk_size)} y="30" width={scale(cfg.max_chunk_size - cfg.min_chunk_size)} height="30" className="zone zone-cut" />
      <line x1={x0 + scale(cfg.avg_chunk_size)} y1="24" x2={x0 + scale(cfg.avg_chunk_size)} y2="66" className="avg-line" />
      <line x1={x0 + scale(cfg.max_chunk_size)} y1="24" x2={x0 + scale(cfg.max_chunk_size)} y2="66" className="max-line" />
      <text x={x0 + 4} y="50" className="node-sub">no cut allowed</text>
      <text x={x0 + scale(cfg.min_chunk_size) + 8} y="50" className="node-sub">cut when fingerprint mod {cfg.boundary_divisor} == {cfg.boundary_remainder}</text>
      <text x={x0} y="82" className="node-title">min {formatBytes(cfg.min_chunk_size)}</text>
      <text x={x0 + scale(cfg.avg_chunk_size) - 30} y="82" className="node-title">target {formatBytes(cfg.avg_chunk_size)}</text>
      <text x={x0 + scale(cfg.max_chunk_size) - 90} y="82" className="node-title">max {formatBytes(cfg.max_chunk_size)} (forced cut)</text>
      <text x={x0} y="116" className="node-sub">Rolling window: the last {cfg.window_size} bytes</text>
      {Array.from({ length: 22 }, (_, i) => (
        <rect key={i} x={x0 + 8 + i * 15} y="124" width="13" height="22" className={i >= 11 && i < 15 ? 'byte byte-window' : 'byte'} />
      ))}
      <text x={x0 + 8 + 11 * 15} y="162" className="node-sub">window slides one byte at a time: fp is updated in O(1)</text>
      <text x={x0 + 8 + 22 * 15 + 10} y="140" className="node-sub">→ next byte enters, oldest leaves</text>
    </svg>
  )
}

function Strip({ title, chunks, classify }) {
  const view = chunks.map((c, i) => ({ chunk_index: i, chunk_hash: '0'.repeat(64), chunk_size: c.size, reused: c.reused }))
  return (
    <div>
      <h3>{title}</h3>
      <ChunkMap chunks={view} classify={(c) => classify(c)} label={title} />
    </div>
  )
}

function Live({ cfg }) {
  const [params, setParams] = useState({ seed: 7, size: 196608, insert_at: 100, insert_len: 37 })
  const [applied, setApplied] = useState(params)
  const demo = useApi((signal) => api.chunkingDemo(applied, signal), [JSON.stringify(applied)])
  const set = (k) => (e) => setParams((p) => ({ ...p, [k]: Number(e.target.value) }))
  const cls = (c) => (c.reused ? 'reused' : 'new')
  return (
    <Card title="CDC vs fixed-size chunking: computed by the implemented chunkers">
      <p className="muted">
        <strong>Computed now</strong> by the real chunker on <strong>seeded generated data</strong> (not one of your files; nothing is stored).
        The insertion changes where the data sits, which is exactly what fixed-size boundaries cannot follow.
      </p>
      <form className="row" onSubmit={(e) => { e.preventDefault(); setApplied(params) }} aria-label="Demo parameters">
        <label>Seed <input type="number" min="0" value={params.seed} onChange={set('seed')} /></label>
        <label>Size (B) <input type="number" min="16384" max="524288" step="1024" value={params.size} onChange={set('size')} /></label>
        <label>Insert at <input type="number" min="0" value={params.insert_at} onChange={set('insert_at')} /></label>
        <label>Insert length <input type="number" min="1" max="4096" value={params.insert_len} onChange={set('insert_len')} /></label>
        <button className="primary" type="submit">Run comparison</button>
      </form>
      <AsyncState state={demo} onRetry={() => demo.reload()}>
        {(d) => (
          <>
            <Legend items={[['reused', 'Chunk (same SHA-256) also existed before the insertion'], ['new', 'Chunk content is new']]} />
            <div className="two-col">
              {[['CDC', d.cdc], ['Fixed-size', d.fixed]].map(([name, s]) => (
                <div key={name} className="tile" data-testid={`result-${name}`}>
                  <div className="tile-label">{s.scheme}</div>
                  <div className="tile-value">{formatPercent(s.reuse_percent)}</div>
                  <div className="tile-hint">{s.reused_chunks} of {s.modified_chunks.length} chunks reused ({formatBytes(s.reused_bytes)})</div>
                </div>
              ))}
            </div>
            <Strip title="CDC · after insertion" chunks={d.cdc.modified_chunks} classify={cls} />
            <Strip title={`Fixed-size (${formatBytes(d.fixed_chunk_size)}) · after insertion`} chunks={d.fixed.modified_chunks} classify={cls} />
            <p className="muted">
              {d.insert_len} bytes inserted at offset {d.insert_at} of a {formatBytes(d.original_size)} input (seed {d.seed}). {d.note}
            </p>
          </>
        )}
      </AsyncState>
    </Card>
  )
}

export default function CdcExplorer() {
  const cfgState = useApi((signal) => api.systemConfig(signal))
  return (
    <AsyncState state={cfgState} onRetry={() => cfgState.reload()}>
      {(config) => {
        const cfg = config.cdc
        return (
          <>
            <Card title="Rabin-style content-defined chunking" aside={<span className="badge badge-ok">configuration read from the server</span>}>
              <ConceptDiagram cfg={cfg} />
              <table>
                <tbody>
                  <tr><th>Minimum chunk size</th><td>{formatBytes(cfg.min_chunk_size)} ({cfg.min_chunk_size} bytes)</td></tr>
                  <tr><th>Target average</th><td>{formatBytes(cfg.avg_chunk_size)} (a design parameter, not a guarantee)</td></tr>
                  <tr><th>Maximum chunk size</th><td>{formatBytes(cfg.max_chunk_size)}</td></tr>
                  <tr><th>Rolling window</th><td>{cfg.window_size} bytes</td></tr>
                  <tr><th>Boundary rule</th><td><code>fingerprint mod {cfg.boundary_divisor} == {cfg.boundary_remainder}</code> (D = target − minimum)</td></tr>
                  <tr><th>Fingerprint polynomial</th><td><code>{cfg.polynomial}</code> (degree {cfg.polynomial_degree}, irreducible over GF(2))</td></tr>
                </tbody>
              </table>
              <dl className="defs">
                <dt>Rolling fingerprint</dt>
                <dd>A polynomial residue of the bytes in the window. Sliding by one byte removes the oldest byte and adds the newest in constant time, so every position can be tested cheaply.</dd>
                <dt>Why a minimum and maximum</dt>
                <dd>The minimum prevents tiny chunks and lets the window be skipped over; the maximum forces a cut when the content offers no boundary (for example long runs of identical bytes).</dd>
                <dt>Boundary condition</dt>
                <dd>After the minimum, each position is a cut point with probability about 1/D, so the expected chunk length is near minimum + D = the target (before the maximum truncates it).</dd>
                <dt>SHA-256 is a different mechanism</dt>
                <dd>The rolling fingerprint only decides <em>where</em> to cut and is never stored. SHA-256 of the finished chunk is its identity and integrity check.</dd>
              </dl>
            </Card>
            <Live cfg={cfg} />
            <Card title="Reading the comparison honestly">
              <ul>
                <li>CDC resynchronises its boundaries after an insertion or deletion, so many later chunks keep the same SHA-256 and are reused; fixed-size boundaries all shift.</li>
                <li>This is not a guarantee for every dataset: for an <em>in-place</em> edit both schemes change about one chunk, and data with no repetition deduplicates under neither.</li>
                <li>The numbers above describe one generated input. Systematic measurements on this machine belong to the benchmark phase.</li>
              </ul>
            </Card>
          </>
        )
      }}
    </AsyncState>
  )
}
