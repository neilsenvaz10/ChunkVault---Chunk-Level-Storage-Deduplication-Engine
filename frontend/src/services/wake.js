// Render's free web service sleeps when idle and needs up to about a minute to wake. While that happens the browser sees network
// failures or proxy-generated 502/503/504 pages. This module tracks "is the backend awake?" for the UI and runs ONE safe probe:
// GET /api/health/live (read-only, idempotent, needs no credentials and carries none). Nothing else is ever retried from here:
// uploads and deletes are never repeated automatically (see services/api.js).

import { cloudConfigProblem, getApiBase, isCloudMode } from '../config/mode'

let state = { status: 'unknown', attempts: 0 } // unknown | waking | ready | unreachable
let running = false
let stopRequested = false
const listeners = new Set()

const set = (next) => {
  state = { ...state, ...next }
  listeners.forEach((l) => l())
}

export const subscribe = (l) => {
  listeners.add(l)
  return () => listeners.delete(l)
}
export const snapshot = () => state
export const resetWake = () => {
  stopRequested = true
  running = false
  state = { status: 'unknown', attempts: 0 }
  listeners.forEach((l) => l())
}

export const DEFAULTS = { intervalMs: 3000, probeTimeoutMs: 10000, giveUpAfterMs: 150000 }

async function probeOnce(timeoutMs) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const r = await fetch(`${getApiBase()}/api/health/live`, { method: 'GET', signal: controller.signal, redirect: 'error', credentials: 'omit' })
    return r.ok
  } catch {
    return false
  } finally {
    clearTimeout(timer)
  }
}

// Probe until the backend answers, then stop. Safe to call repeatedly: only one loop ever runs.
export async function ensureAwake(opts = {}) {
  if (!isCloudMode() || cloudConfigProblem() || running) return
  const { intervalMs, probeTimeoutMs, giveUpAfterMs } = { ...DEFAULTS, ...opts }
  running = true
  stopRequested = false
  const started = Date.now()
  let attempts = 0
  try {
    while (!stopRequested) {
      attempts += 1
      if (await probeOnce(probeTimeoutMs)) {
        if (!stopRequested) set({ status: 'ready', attempts })
        return
      }
      if (stopRequested) return
      if (Date.now() - started >= giveUpAfterMs) {
        set({ status: 'unreachable', attempts })
        return
      }
      set({ status: 'waking', attempts })
      await new Promise((r) => setTimeout(r, intervalMs))
    }
  } finally {
    running = false
  }
}

// Called by the API layer when a request failed in a way that looks like a sleeping server.
export function reportSleepingBackend() {
  if (!isCloudMode()) return
  if (state.status === 'ready' || state.status === 'unknown') set({ status: 'waking' })
  ensureAwake()
}
