import { useEffect, useRef, useState } from 'react'
import { shortHash } from '../utils/format'

export function StatusBadge({ tone = 'muted', children }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function Card({ title, children, aside, className = '' }) {
  return (
    <section className={`card ${className}`}>
      {(title || aside) && (
        <header className="card-head">
          <h2>{title}</h2>
          {aside}
        </header>
      )}
      {children}
    </section>
  )
}

export function PageHeader({ title, description, status }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {description && <p className="page-desc">{description}</p>}
      </div>
      {status}
    </header>
  )
}

export function ErrorNotice({ error, onRetry }) {
  return (
    <div className="notice notice-error" role="alert">
      <strong>{error?.code === 'network_error' ? 'Backend unreachable' : 'Request failed'}</strong>
      <span>{error?.message || 'Unknown error'}</span>
      {error?.code && error.code !== 'network_error' && <code>{error.code}</code>}
      {onRetry && <button onClick={onRetry}>Retry</button>}
    </div>
  )
}

export function Spinner({ label = 'Loading…' }) {
  return <p className="muted" role="status">{label}</p>
}

// Wraps the three states every data view needs. `isEmpty(data)` decides the empty state.
export function AsyncState({ state, onRetry, isEmpty, empty, children }) {
  const { data, error, loading } = state
  if (error) return <ErrorNotice error={error} onRetry={onRetry} />
  if (loading && !data) return <Spinner />
  if (data && isEmpty?.(data)) return <p className="empty">{empty || 'Nothing to show.'}</p>
  if (!data) return null
  return children(data)
}

export function MetricTile({ label, value, hint, tone }) {
  return (
    <div className={`tile ${tone ? `tile-${tone}` : ''}`}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {hint && <div className="tile-hint">{hint}</div>}
    </div>
  )
}

// Hash with a copy button; the full digest is in the title and in the text for assistive tech.
export function Hash({ value, length = 12 }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef(null)
  useEffect(() => () => clearTimeout(timer.current), [])
  if (!value) return <span className="muted">—</span>
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      timer.current = setTimeout(() => setCopied(false), 1200)
    } catch {
      // clipboard unavailable: the full value stays visible in the title
    }
  }
  return (
    <span className="hash" title={value}>
      <code>{shortHash(value, length)}</code>
      <button className="link-btn" onClick={copy} aria-label={`Copy ${value}`}>
        {copied ? 'copied' : 'copy'}
      </button>
    </span>
  )
}

export function Pagination({ total, limit, offset, onChange }) {
  const page = Math.floor(offset / limit) + 1
  const pages = Math.max(1, Math.ceil(total / limit))
  return (
    <div className="pagination">
      <button disabled={offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))}>Previous</button>
      <span>
        Page {page} of {pages} · {total} rows
      </span>
      <button disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>Next</button>
    </div>
  )
}

export function ConfirmDialog({ title, children, confirmLabel = 'Confirm', danger, busy, onConfirm, onCancel }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onCancel()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])
  return (
    <div className="overlay">
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        <div className="dialog-body">{children}</div>
        <div className="dialog-actions">
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className={danger ? 'danger' : 'primary'} onClick={onConfirm} disabled={busy}>
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

// Horizontal bar comparing two byte totals on a common scale (logical vs unique payload).
export function StorageBar({ logical, unique, formatBytes }) {
  if (!logical) return <p className="empty">No stored data yet — nothing to compare.</p>
  const pct = Math.max(0, Math.min(100, (unique / logical) * 100))
  return (
    <div className="storage-bar" role="img"
         aria-label={`Logical ${formatBytes(logical)}, unique chunk payload ${formatBytes(unique)}`}>
      <div className="bar-row">
        <span className="bar-label">Logical</span>
        <div className="bar-track"><div className="bar-fill bar-logical" style={{ width: '100%' }} /></div>
        <span className="bar-value">{formatBytes(logical)}</span>
      </div>
      <div className="bar-row">
        <span className="bar-label">Unique chunks</span>
        <div className="bar-track"><div className="bar-fill bar-unique" style={{ width: `${pct}%` }} /></div>
        <span className="bar-value">{formatBytes(unique)}</span>
      </div>
    </div>
  )
}
