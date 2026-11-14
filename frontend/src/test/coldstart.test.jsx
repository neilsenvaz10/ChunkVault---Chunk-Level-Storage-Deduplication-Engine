// Phase D2: Render cold start (waking-up experience, safe retries), redirect / cross-origin protections, authenticated download
// regressions, and cloud-vs-local feature separation. All network calls are stubs; no backend is contacted.
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RETRY, downloadFile, request, uploadFile } from '../services/api'
import { clearToken, setToken } from '../services/session'
import * as wake from '../services/wake'

// The API layer reports a sleeping backend to the probe; here the report is a spy so request counts stay about the request itself
// (the probe loop is exercised directly in the "waking-up probe" tests).
vi.mock('../services/wake', async () => {
  const actual = await vi.importActual('../services/wake')
  return { ...actual, reportSleepingBackend: vi.fn() }
})

const BACKEND = 'https://chunkvault-api.example.test'
const SECRET = 'demo-secret-token-0123456789abcdef'
const res = (body, status = 200, headers = {}) => ({
  ok: status < 400, status, statusText: '', json: async () => { if (body === undefined) throw new Error('no json'); return body },
  blob: async () => new Blob(['x']), headers: { get: (k) => headers[k] ?? null },
})
const cloud = () => { vi.stubEnv('VITE_CHUNKVAULT_MODE', 'cloud'); vi.stubEnv('VITE_API_BASE_URL', BACKEND) }

beforeEach(() => {
  clearToken()
  wake.resetWake()
  vi.stubGlobal('fetch', vi.fn())
  RETRY.delaysMs = [1, 1]
})
afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  wake.resetWake()
  clearToken()
})

// ------------------------------------------------------------------------------------------------ safe retries
describe('retry policy during a cold start', () => {
  beforeEach(cloud)

  it('retries an idempotent read up to three times and succeeds once the server is awake', async () => {
    fetch.mockRejectedValueOnce(new TypeError('x')).mockResolvedValueOnce(res(undefined, 503)).mockResolvedValue(res({ ok: 1 }))
    await expect(request('/api/files')).resolves.toEqual({ ok: 1 })
    expect(fetch.mock.calls.filter(([u]) => u === `${BACKEND}/api/files`)).toHaveLength(3)
  })

  it('gives up after three attempts with a waking-up explanation', async () => {
    fetch.mockRejectedValue(new TypeError('x'))
    const err = await request('/api/files').catch((e) => e)
    expect(err).toMatchObject({ status: 0, code: 'network_error' })
    expect(err.message).toMatch(/waking|sleep|not responding/i)
    expect(fetch.mock.calls.filter(([u]) => u === `${BACKEND}/api/files`)).toHaveLength(3)
  })

  it('does not retry a proper application error (it has a JSON error envelope)', async () => {
    fetch.mockResolvedValue(res({ error: { code: 'database_unavailable', message: 'm' } }, 503))
    await expect(request('/api/files')).rejects.toMatchObject({ status: 503, code: 'database_unavailable' })
    expect(fetch.mock.calls.filter(([u]) => String(u).endsWith('/api/files'))).toHaveLength(1)
  })

  it.each([
    ['DELETE', '/api/files/abc'], ['POST', '/api/simulations'], ['POST', '/api/files'],
  ])('NEVER repeats %s %s after a network failure, and says the outcome is unknown', async (method, path) => {
    setToken(SECRET)
    fetch.mockRejectedValue(new TypeError('x'))
    const err = await request(path, { method, body: method === 'POST' ? {} : undefined }).catch((e) => e)
    expect(err.message).toMatch(/NOT repeated automatically/)
    expect(err.message).toMatch(/check the file list/i)
    expect(fetch.mock.calls.filter(([u]) => String(u) === `${BACKEND}${path}`)).toHaveLength(1)
  })

  it('NEVER repeats a mutation after a proxy-generated 503 either', async () => {
    setToken(SECRET)
    fetch.mockResolvedValue(res(undefined, 503))
    const err = await request('/api/files/abc', { method: 'DELETE' }).catch((e) => e)
    expect(err.status).toBe(503)
    expect(fetch.mock.calls.filter(([u]) => String(u) === `${BACKEND}/api/files/abc`)).toHaveLength(1)
  })

  it('an upload that fails at the network level is not retried and not reported as applied', async () => {
    setToken(SECRET)
    const xhr = { upload: {}, open: vi.fn(), setRequestHeader: vi.fn(), send: vi.fn() }
    vi.stubGlobal('XMLHttpRequest', vi.fn(() => xhr))
    const p = uploadFile(new File(['x'], 'x.bin'))
    xhr.onerror()
    await expect(p).rejects.toMatchObject({ code: 'network_error', message: expect.stringMatching(/NOT repeated automatically/) })
    expect(xhr.send).toHaveBeenCalledTimes(1)
  })

  it('a download is a read: it is not retried automatically either (the user decides)', async () => {
    setToken(SECRET)
    fetch.mockRejectedValue(new TypeError('x'))
    await expect(downloadFile('abc')).rejects.toMatchObject({ code: 'network_error' })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('local mode keeps the original behaviour: two attempts, no pause, original message', async () => {
    vi.unstubAllEnvs()
    fetch.mockRejectedValue(new TypeError('x'))
    await expect(request('/api/files')).rejects.toMatchObject({ message: 'The backend is unreachable.' })
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})

// ------------------------------------------------------------------------------------------------ redirects / cross-origin
describe('redirects and cross-origin behaviour', () => {
  beforeEach(cloud)

  it('every cloud request refuses redirects, omits cookies and sends no referrer', async () => {
    setToken(SECRET)
    fetch.mockResolvedValue(res({}))
    await request('/api/files')
    await request('/api/files/a', { method: 'DELETE' })
    URL.createObjectURL = vi.fn(() => 'blob:x'); URL.revokeObjectURL = vi.fn()
    await downloadFile('a')
    expect(fetch.mock.calls).toHaveLength(3)
    for (const [, init] of fetch.mock.calls) expect(init).toMatchObject({ redirect: 'error', credentials: 'omit', referrerPolicy: 'no-referrer' })
  })

  it('a redirect (which the browser reports as a failed fetch) is an error that never carries the token onward', async () => {
    setToken(SECRET)
    fetch.mockRejectedValue(new TypeError('Failed to fetch (redirect)'))
    const err = await request('/api/files/a', { method: 'DELETE' }).catch((e) => e)
    expect(err.code).toBe('network_error')
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(JSON.stringify(fetch.mock.calls)).toContain(BACKEND) // only the configured origin was ever contacted
    expect(fetch.mock.calls.every(([u]) => String(u).startsWith(BACKEND))).toBe(true)
  })

  it('an upload answered from a different address is discarded', async () => {
    setToken(SECRET)
    const xhr = { upload: {}, open: vi.fn(), setRequestHeader: vi.fn(), send: vi.fn(), getResponseHeader: vi.fn() }
    vi.stubGlobal('XMLHttpRequest', vi.fn(() => xhr))
    const p = uploadFile(new File(['x'], 'x.bin'))
    xhr.responseURL = 'https://evil.example/api/files'; xhr.status = 201; xhr.responseText = '{"file_id":"x"}'
    xhr.onload()
    await expect(p).rejects.toMatchObject({ code: 'unexpected_redirect' })
  })

  it('local mode does not add cloud-only fetch options', async () => {
    vi.unstubAllEnvs()
    fetch.mockResolvedValue(res({}))
    await request('/api/files')
    expect(fetch.mock.calls[0][1]).not.toHaveProperty('redirect')
    expect(fetch.mock.calls[0][1]).not.toHaveProperty('credentials')
  })
})

describe('authenticated download regressions', () => {
  beforeEach(() => { cloud(); URL.createObjectURL = vi.fn(() => 'blob:x'); URL.revokeObjectURL = vi.fn() })

  it.each([[401, /demo token/i], [403, /administrator/i], [429, /too many/i], [503, /unavailable/i], [504, /took too long/i]])(
    'a %i on download is explained and nothing is saved', async (status, re) => {
      setToken(SECRET)
      fetch.mockResolvedValue(res({ error: { code: 'x', message: 'server wording' } }, status))
      await expect(downloadFile('abc', 'a.bin')).rejects.toMatchObject({ status, message: expect.stringMatching(re) })
      expect(URL.createObjectURL).not.toHaveBeenCalled()
    })

  it('a successful download sends the token only in the header and returns the integrity digest', async () => {
    setToken(SECRET)
    fetch.mockResolvedValue(res({}, 200, { 'X-ChunkVault-SHA256': 'abcd' }))
    const r = await downloadFile('abc', 'a.bin')
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe(`${BACKEND}/api/files/abc/download`)
    expect(url).not.toContain(SECRET)
    expect(init.headers.Authorization).toBe(`Bearer ${SECRET}`)
    expect(r.sha256).toBe('abcd')
    expect(document.querySelector('a[download]')).toBeNull() // the temporary link is removed
  })

  it('a 401 clears the token so the next action asks for it again', async () => {
    setToken(SECRET)
    fetch.mockResolvedValue(res({ error: { code: 'unauthorized', message: 'x' } }, 401))
    await downloadFile('abc').catch(() => {})
    await expect(downloadFile('abc')).rejects.toMatchObject({ code: 'token_required' })
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})

// ------------------------------------------------------------------------------------------------ wake probe
describe('waking-up probe', () => {
  beforeEach(cloud)
  const fast = { intervalMs: 5, probeTimeoutMs: 200, giveUpAfterMs: 400 }

  it('polls only the read-only liveness endpoint, with no credentials, until the backend answers', async () => {
    setToken(SECRET)
    fetch.mockRejectedValueOnce(new TypeError('x')).mockResolvedValueOnce(res(undefined, 503)).mockResolvedValue(res({ status: 'ok' }))
    await wake.ensureAwake(fast)
    expect(wake.snapshot().status).toBe('ready')
    expect(fetch).toHaveBeenCalledTimes(3)
    for (const [url, init] of fetch.mock.calls) {
      expect(url).toBe(`${BACKEND}/api/health/live`)
      expect(init.method).toBe('GET')
      expect(JSON.stringify(init)).not.toContain(SECRET)
      expect(init).toMatchObject({ redirect: 'error', credentials: 'omit' })
      expect(init.headers).toBeUndefined()
    }
  })

  it('reports unreachable after giving up, and can be checked again', async () => {
    fetch.mockRejectedValue(new TypeError('x'))
    await wake.ensureAwake(fast)
    expect(wake.snapshot().status).toBe('unreachable')
    fetch.mockReset()
    fetch.mockResolvedValue(res({ status: 'ok' }))
    await wake.ensureAwake(fast)
    expect(wake.snapshot().status).toBe('ready')
  })

  it('does nothing in local mode and never starts two loops at once', async () => {
    vi.unstubAllEnvs()
    await wake.ensureAwake(fast)
    expect(fetch).not.toHaveBeenCalled()
    cloud()
    fetch.mockResolvedValue(res({ status: 'ok' }))
    await Promise.all([wake.ensureAwake(fast), wake.ensureAwake(fast), wake.ensureAwake(fast)])
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})

// ------------------------------------------------------------------------------------------------ UI separation
vi.mock('../services/api', async () => {
  const actual = await vi.importActual('../services/api')
  const { mockModule } = await import('./mockApi')
  return { ...mockModule(actual), RETRY: actual.RETRY, request: actual.request, downloadFile: actual.downloadFile, uploadFile: actual.uploadFile }
})

describe('cloud / local feature separation', () => {
  const view = async (route) => {
    const { api } = await import('../services/api')
    const { setDefaults } = await import('./mockApi')
    setDefaults(api)
    const App = (await import('../App')).default
    fetch.mockResolvedValue(res({ status: 'ok' })) // the liveness probe
    render(<MemoryRouter initialEntries={[route]}><App /></MemoryRouter>)
    return api
  }

  it('cloud: the capacity simulation is replaced by a notice and never calls the server', async () => {
    cloud()
    const api = await view('/simulation')
    expect(await screen.findByText(/capacity simulation is not available in the public demo/i)).toBeInTheDocument()
    expect(api.simulate).not.toHaveBeenCalled()
  })

  it('local: the simulation still runs', async () => {
    const api = await view('/simulation')
    await waitFor(() => expect(api.simulate).toHaveBeenCalled())
  })

  it('cloud: navigation marks local-only modules, local mode does not', async () => {
    cloud()
    await view('/')
    const nav = screen.getByRole('navigation', { name: 'Primary' })
    expect(nav.querySelectorAll('i[title="Not available in the public demo"]')).toHaveLength(2) // reliability + simulation
  })

  it('local: no module is marked', async () => {
    await view('/')
    expect(document.querySelectorAll('i[title="Not available in the public demo"]')).toHaveLength(0)
  })

  it('cloud: the performance page shows measured results but offers no benchmark controls', async () => {
    cloud()
    const api = await view('/performance')
    expect(await screen.findByText(/benchmark runs is not available in the public demo/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start benchmark' })).toBeNull()
    expect(api.startBenchmark).not.toHaveBeenCalled()
  })

  it('cloud: the API explorer does not link to the backend docs through the frontend origin', async () => {
    cloud()
    await view('/architecture')
    expect(document.querySelector('a[href="/api/docs"]')).toBeNull()
  })

  it('cloud: uploads leave nothing in browser storage', async () => {
    cloud()
    const { UploadHistoryProvider, useUploadHistory } = await import('../context/UploadHistory')
    const Probe = () => {
      const { record } = useUploadHistory()
      return <button onClick={() => record({ file_id: 'f1', filename: 'secret-name.bin' })}>rec</button>
    }
    render(<UploadHistoryProvider><Probe /></UploadHistoryProvider>)
    await userEvent.click(screen.getByText('rec'))
    expect(JSON.stringify({ ...window.sessionStorage, ...window.localStorage })).not.toContain('secret-name')
  })

  it('cloud: the waking-up notice appears while the server sleeps and disappears when it answers', async () => {
    cloud()
    const { api } = await import('../services/api')
    const { setDefaults } = await import('./mockApi')
    setDefaults(api)
    fetch.mockRejectedValue(new TypeError('x'))
    const App = (await import('../App')).default
    render(<MemoryRouter initialEntries={['/']}><App /></MemoryRouter>)
    expect(await screen.findByText(/demo server is waking up/i, {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getByRole('status')).toBeInTheDocument()
    fetch.mockResolvedValue(res({ status: 'ok' }))
    await waitFor(() => expect(screen.queryByText(/demo server is waking up/i)).toBeNull(), { timeout: 8000 })
  }, 15000)
})
