import { useId } from 'react'

// Dependency-free SVG charts. Every chart plots exactly the points it is given and offers the same
// numbers as a table, so nothing is conveyed by colour or shape alone.
//   type "line": numeric x (points: [{x, y}]), one polyline per series
//   type "bar":  categorical x (points: [{x: 'label', y}]), grouped bars per series
const W = 640
const H = 300
const M = { top: 18, right: 16, bottom: 48, left: 64 }

function niceMax(v) {
  if (!(v > 0)) return 1
  const p = 10 ** Math.floor(Math.log10(v))
  const f = v / p
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p
}

export default function Chart({
  title, type = 'line', series, xLabel, yLabel, formatX = (x) => String(x), formatY = (y) => String(y), empty = 'No data to plot.', description,
}) {
  const uid = useId()
  const clean = (series || []).map((s) => ({ ...s, points: (s.points || []).filter((p) => p.y !== null && p.y !== undefined && Number.isFinite(p.y)) }))
  const total = clean.reduce((n, s) => n + s.points.length, 0)
  if (!total) {
    return (
      <figure className="chart">
        <figcaption>{title}</figcaption>
        <p className="empty">{empty}</p>
      </figure>
    )
  }
  const iw = W - M.left - M.right
  const ih = H - M.top - M.bottom
  const ymax = niceMax(Math.max(...clean.flatMap((s) => s.points.map((p) => p.y))))
  const y = (v) => M.top + ih - (v / ymax) * ih
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * ymax)

  let body
  let xTicks
  if (type === 'bar') {
    const cats = [...new Set(clean.flatMap((s) => s.points.map((p) => p.x)))]
    const gw = iw / cats.length
    const bw = Math.min(46, (gw * 0.8) / clean.length)
    xTicks = cats.map((c, i) => ({ label: c, x: M.left + gw * i + gw / 2 }))
    body = cats.flatMap((c, ci) =>
      clean.map((s, si) => {
        const p = s.points.find((q) => q.x === c)
        if (!p) return null
        const x = M.left + gw * ci + gw / 2 - (bw * clean.length) / 2 + bw * si
        return <rect key={`${c}-${s.name}`} className={`bar s${si % 4}`} x={x} y={y(p.y)} width={bw - 2} height={Math.max(0, M.top + ih - y(p.y))}>
          <title>{`${s.name} · ${c}: ${formatY(p.y)}`}</title>
        </rect>
      }))
  } else {
    const xs = clean.flatMap((s) => s.points.map((p) => p.x))
    const xmin = Math.min(...xs)
    const xmax = Math.max(...xs)
    const xr = xmax - xmin || 1
    const x = (v) => M.left + ((v - xmin) / xr) * iw
    xTicks = [...new Set(xs)].sort((a, b) => a - b).slice(0, 8).map((v) => ({ label: formatX(v), x: x(v) }))
    body = clean.map((s, si) => {
      const pts = [...s.points].sort((a, b) => a.x - b.x)
      return (
        <g key={s.name} className={`line s${si % 4}`}>
          <polyline points={pts.map((p) => `${x(p.x)},${y(p.y)}`).join(' ')} fill="none" />
          {pts.map((p, i) => (
            <circle key={`${p.x}-${i}`} cx={x(p.x)} cy={y(p.y)} r="3.5"><title>{`${s.name} · ${formatX(p.x)}: ${formatY(p.y)}`}</title></circle>
          ))}
        </g>
      )
    })
  }

  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={`${uid}-t`} aria-describedby={description ? `${uid}-d` : undefined}>
        <title id={`${uid}-t`}>{title}</title>
        {description && <desc id={`${uid}-d`}>{description}</desc>}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} className="grid" />
            <text x={M.left - 6} y={y(t) + 4} textAnchor="end" className="tick">{formatY(t)}</text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={t.label + t.x} x={t.x} y={H - M.bottom + 16} textAnchor="middle" className="tick">{String(t.label).slice(0, 14)}</text>
        ))}
        <text x={M.left + iw / 2} y={H - 6} textAnchor="middle" className="axis-label">{xLabel}</text>
        <text x={14} y={M.top + ih / 2} textAnchor="middle" className="axis-label" transform={`rotate(-90 14 ${M.top + ih / 2})`}>{yLabel}</text>
        {body}
      </svg>
      <ul className="legend chart-legend">
        {clean.map((s, i) => <li key={s.name}><span className={`swatch s${i % 4}`} /> {s.name}</li>)}
      </ul>
      <details>
        <summary>Show the plotted data</summary>
        <table>
          <thead><tr><th>Series</th><th>{xLabel}</th><th>{yLabel}</th></tr></thead>
          <tbody>
            {clean.flatMap((s) => s.points.map((p, i) => (
              <tr key={`${s.name}-${p.x}-${i}`}><td>{s.name}</td><td>{type === 'bar' ? p.x : formatX(p.x)}</td><td>{formatY(p.y)}</td></tr>
            )))}
          </tbody>
        </table>
      </details>
    </figure>
  )
}
