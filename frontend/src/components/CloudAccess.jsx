import { useEffect, useState, useSyncExternalStore } from 'react'
import { cloudConfigProblem, isCloudMode } from '../config/mode'
import { clearToken, setToken, snapshot, subscribe } from '../services/session'
import * as wake from '../services/wake'

// Everything here renders nothing in local mode.

export function useHasToken() {
  return useSyncExternalStore(subscribe, snapshot)
}

export function DemoBanner() {
  if (!isCloudMode()) return null
  return (
    <div className="demo-banner" role="note" aria-label="Public demo notice">
      <strong>Public demo.</strong>{' '}
      Use <strong>synthetic, non-sensitive data only</strong>. Anything you upload is visible to other visitors, is stored on shared free-tier
      services, and may be deleted at any time. Never upload personal, confidential or real files.
    </div>
  )
}

// Demo-token entry. The value is held in the field only until submitted, then moved to in-memory session state (services/session.js);
// it is never written to storage, the URL or the build, and the field is cleared as soon as it is accepted.
export function TokenEntry() {
  const has = useHasToken()
  const [value, setValue] = useState('')
  if (!isCloudMode()) return null

  const submit = (e) => {
    e.preventDefault()
    if (!value.trim()) return
    setToken(value)
    setValue('')
  }

  if (has) {
    return (
      <div className="token-entry" role="status">
        <span>Demo token active for this tab</span>
        <button type="button" onClick={clearToken}>Forget token</button>
      </div>
    )
  }
  return (
    <form className="token-entry" onSubmit={submit} aria-label="Demo token entry">
      <label>
        <span>Demo token</span>
        <input type="password" value={value} onChange={(e) => setValue(e.target.value)} autoComplete="off" spellCheck={false}
               autoCapitalize="off" name="demo-token" aria-describedby="token-help" placeholder="needed to upload, download or delete" />
      </label>
      <button className="primary" type="submit" disabled={!value.trim()}>Use token</button>
      <small id="token-help" className="muted">Kept in memory for this tab only; reloading the page forgets it.</small>
    </form>
  )
}

export function ConfigProblem() {
  const problem = cloudConfigProblem()
  return problem ? <div className="notice notice-error" role="alert">{problem}</div> : null
}

// Replaces administrative controls in the public cloud demo. The browser never asks for, holds or sends an administrator token.
export function AdminOnly({ children, what = 'This feature' }) {
  if (!isCloudMode()) return children
  return (
    <div className="notice" role="note">
      <strong>{what} is not available in the public demo.</strong>
      <span>Administrative maintenance (integrity audits, reconciliation, garbage collection, benchmarks and fault experiments) is operated
        by the project owner and is never exposed to visitors.</span>
    </div>
  )
}

// Generic "not part of the public demo" panel (local-only or costly features).
export function NotInDemo({ what = 'This feature', reason }) {
  if (!isCloudMode()) return null
  return (
    <div className="notice" role="note">
      <strong>{what} is not available in the public demo.</strong>
      <span>{reason}</span>
    </div>
  )
}

// Cold-start experience: the free backend sleeps when idle. Probes only the read-only liveness endpoint; never repeats a write.
export function WakeNotice() {
  const cloud = isCloudMode()
  const w = useSyncExternalStore(wake.subscribe, wake.snapshot)
  useEffect(() => {
    if (cloud) wake.ensureAwake()
  }, [cloud])
  if (!cloud || w.status === 'ready' || w.status === 'unknown') return null
  if (w.status === 'unreachable') {
    return (
      <div className="notice notice-error" role="alert">
        <strong>The demo server is not answering.</strong>
        <span>It may be down or still starting. Nothing you did was lost.</span>
        <button type="button" onClick={() => wake.ensureAwake()}>Check again</button>
      </div>
    )
  }
  return (
    <div className="notice notice-warn" role="status" aria-live="polite">
      <strong>The demo server is waking up…</strong>
      <span>Free hosting puts it to sleep when it is idle, so the first request can take up to a minute. This page will work as soon as it answers
        (attempt {w.attempts + 1}). Uploads and deletes are never repeated automatically.</span>
    </div>
  )
}
