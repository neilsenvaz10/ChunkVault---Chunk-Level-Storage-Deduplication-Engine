// Centralised client for the FastAPI backend.
//  - every non-2xx response is parsed from the {"error": {code, message, details}} envelope
//  - GET requests may be retried once after a network failure (they are idempotent)
//  - mutations (POST/DELETE/upload) are NEVER retried automatically
//  - all calls accept an AbortSignal
// No measurements are computed or hard-coded here.

const BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

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

async function readJson(response) {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function toApiError(status, body, fallbackText = '') {
  const err = body && body.error
  return new ApiError(
    status,
    err?.code ?? (status === 0 ? 'network_error' : `http_${status}`),
    err?.message ?? GENERIC[status] ?? (fallbackText || `Request failed (${status}).`),
    body,
  )
}

export async function request(path, { method = 'GET', body, signal } = {}) {
  const attempts = method === 'GET' ? 2 : 1
  let lastError
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    let response
    try {
      response = await fetch(`${BASE_URL}${path}`, {
        method,
        signal,
        headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      })
    } catch (e) {
      if (e?.name === 'AbortError') throw e
      lastError = new ApiError(0, 'network_error', GENERIC[0])
      continue // only a GET gets a second attempt
    }
    const json = await readJson(response)
    if (!response.ok) throw toApiError(response.status, json, response.statusText)
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
  audit: (body = {}, signal) => request('/api/integrity/audit', { method: 'POST', body, signal }),
  reconcile: (signal) => request('/api/integrity/reconcile', { method: 'POST', signal }),
}

// Upload with real browser-to-server transfer progress (fetch cannot report it).
// onProgress({loaded, total}) reports bytes SENT to the server only; it says nothing about the
// backend's chunking/hashing/commit stages, which the API does not report.
// Resolves with the parsed UploadResponse. Never retried.
export function uploadFile(file, { onProgress, signal } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${BASE_URL}/api/files`)
    xhr.responseType = 'text'
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress({ loaded: e.loaded, total: e.total })
    }
    xhr.onerror = () => reject(new ApiError(0, 'network_error', GENERIC[0]))
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'))
    xhr.onload = () => {
      let json = null
      try {
        json = JSON.parse(xhr.responseText)
      } catch {
        // non-JSON body
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(json)
      else reject(toApiError(xhr.status, json, xhr.statusText))
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
    response = await fetch(`${BASE_URL}/api/files/${encodeURIComponent(fileId)}/download`, { signal })
  } catch (e) {
    if (e?.name === 'AbortError') throw e
    throw new ApiError(0, 'network_error', GENERIC[0])
  }
  if (!response.ok) throw toApiError(response.status, await readJson(response), response.statusText)
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
