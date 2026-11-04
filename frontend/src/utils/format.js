const UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB']

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
