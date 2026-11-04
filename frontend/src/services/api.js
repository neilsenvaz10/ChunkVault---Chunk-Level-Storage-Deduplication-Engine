// Thin client for the FastAPI backend. All errors use the backend's
// {"error": {code, message, details}} envelope.

const BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(status, code, message, body) {
    super(message)
    this.status = status
    this.code = code
    this.body = body
  }
}

async function request(path, options = {}) {
  let response
  try {
    response = await fetch(`${BASE_URL}${path}`, options)
  } catch {
    throw new ApiError(0, 'network_error', 'Backend unreachable')
  }
  let body = null
  try {
    body = await response.json()
  } catch {
    // non-JSON body; handled below
  }
  if (!response.ok) {
    const err = body && body.error
    throw new ApiError(
      response.status,
      err?.code ?? `http_${response.status}`,
      err?.message ?? response.statusText,
      body,
    )
  }
  return body
}

export async function getHealth() {
  try {
    return await request('/api/health')
  } catch (e) {
    // A 503 still carries a valid health body describing what is degraded.
    if (e instanceof ApiError && e.status === 503 && e.body?.status) return e.body
    throw e
  }
}

export const getPlannedEndpoints = () => request('/api/planned-endpoints')
