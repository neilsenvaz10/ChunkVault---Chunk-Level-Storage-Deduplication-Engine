// Centralised client for the FastAPI backend.
//  - every non-2xx response is parsed from the {"error": {code, message, details}} envelope
//  - GET requests may be retried once after a network failure (they are idempotent)
//  - mutations (POST/DELETE/upload) are NEVER retried automatically
//  - all calls accept an AbortSignal
// No measurements are computed or hard-coded here.

import { cloudConfigProblem, getApiBase, isCloudMode } from '../config/mode'
import { clearToken, getToken, redact } from './session'
import { reportSleepingBackend } from './wake'

// Cloud mode: the demo token is sent as `Authorization: Bearer ...` to THIS backend only, and only on requests that need it
// (uploads, downloads, deletes, simulations and the other non-GET calls). Public reads carry no credentials at all.

export class ApiError extends Error {
  constructor(status, code, message, body = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.body = body
  }
}

const GENERIC = {
  0: 'The backend is unreachable.',
  413: 'The file is larger than the server accepts.',
  500: 'The server reported an internal error.',
}

// Cloud mode replaces the server's wording for these statuses with an explanation a visitor can act on.
const CLOUD_MESSAGES = {
  401: 'The demo token is missing or was not accepted. Enter the demo token again to continue.',
  403: 'This action needs administrator rights, which the public demo does not offer.',
  429: 'Too many requests from your connection. Please wait a moment and try again.',
  503: 'The service is temporarily unavailable (its database or storage is not responding). Nothing was lost; try again shortly.',
  504: 'The operation took too long and was cancelled; nothing from it was kept. Try again, or use a smaller file.',
}

export function friendlyMessage(status, serverMessage, retryAfter) {
  if (!isCloudMode() || !CLOUD_MESSAGES[status]) return serverMessage
  if (status === 429 && Number.isFinite(retryAfter) && retryAfter > 0) {
    const n = Math.ceil(retryAfter)
    return `Too many requests from your connection. Try again in about ${n} second${n === 1 ? '' : 's'}.`
  }
  return CLOUD_MESSAGES[status]
}

const needsToken = (method, path) => method !== 'GET' || /\/download$/.test(path)

// Headers for one backend request. Throws a friendly 401 (before any network call) when a protected action has no token.
function authHeaders(method, path) {
  if (!isCloudMode() || !needsToken(method, path)) return {}
  const token = getToken()
  if (!token) throw new ApiError(401, 'token_required', CLOUD_MESSAGES[401])
  return { Authorization: `Bearer ${token}` }
}

function backendUrl(path) {
  const problem = cloudConfigProblem()
  if (problem) throw new ApiError(0, 'misconfigured', problem)
  if (!path.startsWith('/api/')) throw new ApiError(0, 'invalid_path', 'Only the demo backend API can be requested.') // never an arbitrary or external URL
  return `${getApiBase()}${path}`
}

const retryAfterOf = (headers) => {
  const v = headers?.get ? Number(headers.get('Retry-After')) : NaN
  return Number.isFinite(v) ? v : NaN
}

async function readJson(response) {
  try {
    return await response.json()
  } catch {
    return null
  }
}

// The raw error body is kept for pages that read details from it; it is scrubbed of the token first (a server that echoed it back).
function scrub(body) {
  if (!body) return body
  try {
    return JSON.parse(redact(JSON.stringify(body)))
  } catch {
    return null
  }
}

function toApiError(status, body, fallbackText = '', retryAfter = NaN) {
  const err = body && body.error
  const raw = err?.message ?? GENERIC[status] ?? (fallbackText || `Request failed (${status}).`)
  const apiError = new ApiError(
    status,
    err?.code ?? (status === 0 ? 'network_error' : `http_${status}`),
    redact(friendlyMessage(status, raw, retryAfter)),
    scrub(body),
  )
  apiError.retryAfter = retryAfter
  // A rejected token is forgotten at once so the visitor is asked again rather than resending it.
  if (status === 401 && isCloudMode()) clearToken()
  return apiError
}

// Cloud mode hardening for every request: no cookies, no referrer, and a redirect is an ERROR (never followed), so a token can never
// be carried to another address. The backend does not redirect API calls, so a redirect means something is wrong.
const cloudFetchOptions = () => (isCloudMode() ? { redirect: 'error', credentials: 'omit', referrerPolicy: 'no-referrer' } : {})

// A sleeping free-tier backend answers through its proxy with 502/503/504 and no JSON error envelope.
const looksLikeSleepingBackend = (status, json) => [502, 503, 504].includes(status) && !(json && json.error)

const CLOUD_WAKING = 'The demo server is not responding yet. Free hosting puts it to sleep when idle, so the first request can take up to a minute. Wait a moment and try again.'
const CLOUD_WAKING_WRITE = `${CLOUD_WAKING} This request was NOT repeated automatically and it may not have been applied: check the file list before trying again.`

const networkError = (method) => new ApiError(0, 'network_error', isCloudMode() ? (method === 'GET' ? CLOUD_WAKING : CLOUD_WAKING_WRITE) : GENERIC[0])

export const RETRY = { delaysMs: [1500, 3000] } // cloud GETs only; exported so tests can shorten it

const pause = (ms, signal) => new Promise((resolve, reject) => {
  const t = setTimeout(resolve, ms)
  signal?.addEventListener('abort', () => { clearTimeout(t); reject(new DOMException('Aborted', 'AbortError')) }, { once: true })
})

export async function request(path, { method = 'GET', body, signal } = {}) {
  // Only idempotent reads are ever repeated: twice locally, up to three times (with a pause) in cloud mode to ride out a cold start.
  // Uploads, deletes and every other mutation are sent exactly once.
  const attempts = method === 'GET' ? (isCloudMode() ? 1 + RETRY.delaysMs.length : 2) : 1
  let lastError
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt > 0 && isCloudMode()) await pause(RETRY.delaysMs[attempt - 1], signal)
    let response
    try {
      response = await fetch(backendUrl(path), {
        method,
        signal,
        headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...authHeaders(method, path) },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        ...cloudFetchOptions(),
      })
    } catch (e) {
      if (e?.name === 'AbortError' || e instanceof ApiError) throw e
      lastError = networkError(method)
      reportSleepingBackend()
      continue // only a GET gets another attempt
    }
    const json = await readJson(response)
    if (!response.ok) {
      const err = toApiError(response.status, json, response.statusText, retryAfterOf(response.headers))
      if (looksLikeSleepingBackend(response.status, json)) {
        err.message = isCloudMode() ? (method === 'GET' ? CLOUD_WAKING : CLOUD_WAKING_WRITE) : err.message
        reportSleepingBackend()
        if (method === 'GET' && attempt + 1 < attempts) {
          lastError = err
          continue
        }
      }
      throw err
    }
    return json
  }
  throw lastError
}

// A 503 health response still carries a valid body describing what is degraded.
export async function getHealth(signal) {
  try {
    return await request('/api/health', { signal })
  } catch (e) {
    if (e instanceof ApiError && e.status === 503 && e.body?.status) return e.body
    throw e
  }
}

const qs = (params) => {
  const q = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => v !== undefined && v !== null && q.set(k, String(v)))
  const s = q.toString()
  return s ? `?${s}` : ''
}

export const api = {
  health: getHealth,
  plannedEndpoints: (signal) => request('/api/planned-endpoints', { signal }),
  storageStats: (signal) => request('/api/storage/stats', { signal }),
  listFiles: ({ limit = 50, offset = 0 } = {}, signal) => request(`/api/files${qs({ limit, offset })}`, { signal }),
  getFile: (id, signal) => request(`/api/files/${encodeURIComponent(id)}`, { signal }),
  deleteFile: (id, signal) => request(`/api/files/${encodeURIComponent(id)}`, { method: 'DELETE', signal }),
  listChunks: ({ limit = 50, offset = 0, state } = {}, signal) =>
    request(`/api/chunks${qs({ limit, offset, state })}`, { signal }),
  getChunk: (hash, signal) => request(`/api/chunks/${encodeURIComponent(hash)}`, { signal }),
  inspectSchema: (signal) => request('/api/inspect/schema', { signal }),
  inspectRows: (table, { limit = 25, offset = 0 } = {}, signal) =>
    request(`/api/inspect/tables/${encodeURIComponent(table)}/rows${qs({ limit, offset })}`, { signal }),
  systemConfig: (signal) => request('/api/system/config', { signal }),
  chunkingDemo: (params = {}, signal) => request(`/api/system/chunking-demo${qs(params)}`, { signal }),
  // FastAPI's generated contract, used by the API explorer (never hand-copied).
  openapi: (signal) => request('/api/openapi.json', { signal }),
  // Phase 5
  reliabilityScenarios: (signal) => request('/api/reliability/scenarios', { signal }),
  runExperiment: (scenarioId, signal) =>
    request('/api/reliability/experiments', { method: 'POST', body: { scenario_id: scenarioId }, signal }),
  benchmarkCatalog: (signal) => request('/api/benchmarks/workloads', { signal }),
  listBenchmarks: (signal) => request('/api/benchmarks', { signal }),
  getBenchmark: (id, signal) => request(`/api/benchmarks/${encodeURIComponent(id)}`, { signal }),
  startBenchmark: (body, signal) => request('/api/benchmarks', { method: 'POST', body, signal }),
  cancelBenchmark: (id, signal) => request(`/api/benchmarks/${encodeURIComponent(id)}/cancel`, { method: 'POST', signal }),
  simulate: (body, signal) => request('/api/simulations', { method: 'POST', body, signal }),
  audit: (body = {}, signal) => request('/api/integrity/audit', { method: 'POST', body, signal }),
  reconcile: (signal) => request('/api/integrity/reconcile', { method: 'POST', signal }),
}

// Upload with real browser-to-server transfer progress (fetch cannot report it).
// onProgress({loaded, total}) reports bytes SENT to the server only; it says nothing about the
// backend's chunking/hashing/commit stages, which the API does not report.
// Resolves with the parsed UploadResponse. Never retried.
export function uploadFile(file, { onProgress, signal } = {}) {
  return new Promise((resolve, reject) => {
    let url
    let headers
    try {
      url = backendUrl('/api/files')
      headers = authHeaders('POST', '/api/files')
    } catch (e) {
      reject(e)
      return
    }
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v))
    xhr.responseType = 'text'
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress({ loaded: e.loaded, total: e.total })
    }
    xhr.onerror = () => {
      reportSleepingBackend()
      reject(networkError('POST'))
    }
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'))
    xhr.onload = () => {
      // A redirect cannot be disabled on XHR: if the response did not come from the URL we asked, discard it.
      if (isCloudMode() && xhr.responseURL && xhr.responseURL !== url) {
        reject(new ApiError(0, 'unexpected_redirect', 'The server answered from an unexpected address, so the response was discarded.'))
        return
      }
      let json = null
      try {
        json = JSON.parse(xhr.responseText)
      } catch {
        // non-JSON body
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(json)
      else reject(toApiError(xhr.status, json, xhr.statusText, Number(xhr.getResponseHeader?.('Retry-After'))))
    }
    if (signal) {
      if (signal.aborted) {
        reject(new DOMException('Upload cancelled', 'AbortError'))
        return
      }
      signal.addEventListener('abort', () => xhr.abort(), { once: true })
    }
    const form = new FormData()
    form.append('file', file, file.name)
    xhr.send(form)
  })
}

// Restore through the verified endpoint and hand the bytes to the browser.
// Errors (missing or corrupt chunk, ...) are surfaced instead of saving a bad file.
export async function downloadFile(fileId, fileName = 'download.bin', signal) {
  let response
  try {
    const path = `/api/files/${encodeURIComponent(fileId)}/download`
    response = await fetch(backendUrl(path), { signal, headers: authHeaders('GET', path), ...cloudFetchOptions() })
  } catch (e) {
    if (e?.name === 'AbortError' || e instanceof ApiError) throw e
    reportSleepingBackend()
    throw networkError('GET')
  }
  if (!response.ok) throw toApiError(response.status, await readJson(response), response.statusText, retryAfterOf(response.headers))
  const blob = await response.blob()
  const sha256 = response.headers.get('X-ChunkVault-SHA256')
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return { size: blob.size, sha256 }
}
