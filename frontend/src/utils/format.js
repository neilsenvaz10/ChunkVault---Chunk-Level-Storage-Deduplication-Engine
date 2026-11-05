const UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB', 'ZiB', 'YiB']

export function formatBytes(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  if (n < 1024) return `${n} B`
  let value = n
  let unit = 0
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value >= 100 ? 0 : value >= 10 ? 1 : 2)} ${UNITS[unit]}`
}

export const formatInt = (n) => (n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US'))

export const formatPercent = (p) => (p === null || p === undefined ? 'n/a' : `${p.toFixed(1)}%`)

export const formatRatio = (r) => (r === null || r === undefined ? 'n/a' : `${r.toFixed(2)}×`)

export const shortHash = (h, n = 10) => (h ? `${h.slice(0, n)}…` : '—')

export function formatDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

export const formatSeconds = (s) => {
  if (s === null || s === undefined || Number.isNaN(s)) return '—'
  if (s < 0.001) return `${(s * 1e6).toFixed(0)} µs`
  if (s < 1) return `${(s * 1000).toFixed(s < 0.01 ? 2 : 1)} ms`
  return `${s.toFixed(2)} s`
}

// seconds per MiB: the reciprocal view of throughput (small numbers are easier to read than tiny rates)
export const formatSecPerMiB = (v) => (v === null || v === undefined ? '—' : `${v.toFixed(v >= 10 ? 1 : v >= 1 ? 2 : 3)} s/MiB`)

export const formatRate = (v) => (v === null || v === undefined ? '—' : `${v.toFixed(v >= 10 ? 1 : 2)} MiB/s`)

// Compact number for large model outputs (1.2e9 -> 1.20 G)
export function formatCompact(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const a = Math.abs(n)
  if (a < 1000) return String(Math.round(n * 100) / 100)
  const units = ['K', 'M', 'G', 'T', 'P', 'E']
  let i = -1
  let v = a
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000
    i += 1
  }
  return `${n < 0 ? '-' : ''}${v.toFixed(2)} ${units[i]}`
}
