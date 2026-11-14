// Phase D1: cloud mode (public demo) vs local mode: configuration, in-memory demo token, authenticated requests, friendly errors,
// hidden administrative features, and the synthetic-data banner. Every network call is a stub: no real backend is contacted.
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { cloudConfigProblem, getMode } from '../config/mode'
import { ApiError, downloadFile, request, uploadFile } from '../services/api'
import { clearToken, getToken, hasToken, redact, setToken } from '../services/session'
import { setDefaults } from './mockApi'

vi.mock('../services/wake', async () => {
  const actual = await vi.importActual('../services/wake')
  return { ...actual, reportSleepingBackend: vi.fn() } // the probe itself is tested in wake.test.jsx
})

const BACKEND = 'https://chunkvault-api.example.test'
const SECRET = 'demo-secret-token-0123456789abcdef'

const ok = (body, status = 200, headers = {}) => ({
  ok: status < 400, status, statusText: '', json: async () => body, blob: async () => new Blob(['x']),
  headers: { get: (k) => headers[k] ?? headers[k.toLowerCase()] ?? null },
})

function cloud() {
  vi.stubEnv('VITE_CHUNKVAULT_MODE', 'cloud')
  vi.stubEnv('VITE_API_BASE_URL', BACKEND)
}

beforeEach(() => {
  clearToken()
  vi.stubGlobal('fetch', vi.fn())
})
afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  clearToken()
})

// ------------------------------------------------------------------------------------------------ configuration
describe('mode configuration', () => {
  it('defaults to local, and only the exact value "cloud" (any case) selects cloud mode', () => {
    expect(getMode()).toBe('local')
    for (const v of ['', 'local', 'prod', 'true', '1', 'clouds']) {
      vi.stubEnv('VITE_CHUNKVAULT_MODE', v)
      expect(getMode()).toBe('local')
    }
    vi.stubEnv('VITE_CHUNKVAULT_MODE', ' Cloud ')
    expect(getMode()).toBe('cloud')
  })

  it('cloud mode needs an explicit https backend address without credentials', () => {
    vi.stubEnv('VITE_CHUNKVAULT_MODE', 'cloud')
    vi.stubEnv('VITE_API_BASE_URL', '')
    expect(cloudConfigProblem()).toMatch(/VITE_API_BASE_URL/)
    for (const bad of ['http://api.example.test', 'ftp://x', 'not a url', 'https://user:pw@api.example.test']) {
      vi.stubEnv('VITE_API_BASE_URL', bad)
      expect(cloudConfigProblem()).toBeTruthy()
    }
    for (const good of [BACKEND, 'http://localhost:8000', 'http://127.0.0.1:8000']) {
      vi.stubEnv('VITE_API_BASE_URL', good)
      expect(cloudConfigProblem()).toBeNull()
    }
  })

  it('a misconfigured cloud build refuses to send any request', async () => {
    vi.stubEnv('VITE_CHUNKVAULT_MODE', 'cloud')
    vi.stubEnv('VITE_API_BASE_URL', 'http://api.example.test')
    await expect(request('/api/files')).rejects.toMatchObject({ code: 'misconfigured' })
    expect(fetch).not.toHaveBeenCalled()
  })

  it('local mode is unchanged: same-origin or configured base, never a token, never an Authorization header', async () => {
    setToken(SECRET) // even if one were somehow held
    fetch.mockResolvedValue(ok({ a: 1 }))
    await request('/api/files', { method: 'DELETE' })
    await request('/api/health')
    for (const [url, init] of fetch.mock.calls) {
      expect(url.startsWith('/api/')).toBe(true)
      expect(JSON.stringify(init?.headers ?? {})).not.toMatch(/authorization/i)
    }
  })

  it('no token or secret is ever read from the build environment', () => {
    const files = []
    const walk = (dir) => readdirSync(dir).forEach((f) => {
      const p = join(dir, f)
      if (statSync(p).isDirectory()) { if (f !== 'test') walk(p) } else if (/\.(jsx?|html)$/.test(f)) files.push(p)
    })
    walk(join(__dirname, '..'))
    files.push(join(__dirname, '..', '..', 'index.html'))
    for (const f of files) expect(readFileSync(f, 'utf8'), f).not.toMatch(/VITE_[A-Z_]*(TOKEN|SECRET|KEY|PASSWORD)/)
  })
})

// ------------------------------------------------------------------------------------------------ token store
describe('demo token handling', () => {
  it('is held in memory only: nothing is written to any browser storage, cookie or URL', () => {
    const spies = [vi.spyOn(Storage.prototype, 'setItem'), vi.spyOn(document, 'cookie', 'set')]
    const before = window.location.href
    setToken(`  ${SECRET}  `)
    expect(getToken()).toBe(SECRET) // trimmed
    expect(hasToken()).toBe(true)
    spies.forEach((s) => expect(s).not.toHaveBeenCalled())
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(SECRET)
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(SECRET)
    expect(document.cookie).not.toContain(SECRET)
    expect(window.location.href).toBe(before)
    expect(window.location.href).not.toContain(SECRET)
  })

  it('can be forgotten, and an empty value means no token', () => {
    setToken(SECRET)
    clearToken()
    expect(getToken()).toBeNull()
    setToken('   ')
    expect(hasToken()).toBe(false)
  })

  it('redacts itself from any text about to be shown', () => {
    setToken(SECRET)
    expect(redact(`bad header Bearer ${SECRET}!`)).toBe('bad header Bearer [token hidden]!')
    expect(redact('nothing to hide')).toBe('nothing to hide')
  })

  it('is never written to the console by the API layer, even when requests fail', async () => {
    cloud()
    const logs = ['log', 'info', 'warn', 'error', 'debug'].map((k) => vi.spyOn(console, k).mockImplementation(() => {}))
    setToken(SECRET)
    fetch.mockResolvedValue(ok({ error: { code: 'bad', message: `rejected ${SECRET}` } }, 500))
    const err = await request('/api/files', { method: 'DELETE' }).catch((e) => e)
    expect(err.message).not.toContain(SECRET)
    expect(JSON.stringify(err)).not.toContain(SECRET)
    logs.forEach((l) => expect(JSON.stringify(l.mock.calls)).not.toContain(SECRET))
  })
})

// ------------------------------------------------------------------------------------------------ authenticated requests
describe('authenticated requests (cloud mode)', () => {
  beforeEach(cloud)

  it('sends the token on mutations, and sends nothing on public reads', async () => {
    setToken(SECRET)
    fetch.mockResolvedValue(ok({ ok: true }))
    await request('/api/files/abc', { method: 'DELETE' })
    await request('/api/simulations', { method: 'POST', body: { a: 1 } })
    const [del, sim] = fetch.mock.calls
    expect(del[0]).toBe(`${BACKEND}/api/files/abc`)
    expect(del[1].headers.Authorization).toBe(`Bearer ${SECRET}`)
    expect(sim[1].headers.Authorization).toBe(`Bearer ${SECRET}`)
    expect(sim[1].headers['Content-Type']).toBe('application/json')
    fetch.mockClear()
    await request('/api/files')
    await request('/api/storage/stats')
    for (const [, init] of fetch.mock.calls) expect(JSON.stringify(init.headers ?? {})).not.toMatch(/authorization/i)
  })

  it('never puts the token in a URL', async () => {
    setToken(SECRET)
    fetch.mockResolvedValue(ok({}))
    await request('/api/files/x', { method: 'DELETE' })
    await request('/api/files?limit=5')
    for (const [url] of fetch.mock.calls) expect(url).not.toContain(SECRET)
  })

  it('refuses a protected request without a token, before touching the network, with a clear message', async () => {
    await expect(request('/api/files/abc', { method: 'DELETE' })).rejects.toMatchObject({ status: 401, code: 'token_required', message: expect.stringMatching(/demo token/i) })
    await expect(downloadFile('abc')).rejects.toMatchObject({ status: 401 })
    await expect(uploadFile(new File(['x'], 'x.bin'))).rejects.toMatchObject({ status: 401 })
    expect(fetch).not.toHaveBeenCalled()
  })

  it('only talks to the configured backend: external addresses are refused', async () => {
    setToken(SECRET)
    for (const evil of ['https://evil.example/api/x', '//evil.example/api/x', 'http://evil.example', '/other', 'api/files']) {
      await expect(request(evil, { method: 'DELETE' })).rejects.toBeDefined()
    }
    expect(fetch).not.toHaveBeenCalled()
  })

  it('download carries the token in a header and saves the file', async () => {
    setToken(SECRET)
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    fetch.mockResolvedValue(ok(null, 200, { 'X-ChunkVault-SHA256': 'ab' }))
    const r = await downloadFile('f 1', 'a.bin')
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe(`${BACKEND}/api/files/f%201/download`)
    expect(init.headers.Authorization).toBe(`Bearer ${SECRET}`)
    expect(r.sha256).toBe('ab')
  })

  describe('upload (XHR)', () => {
    let xhr
    beforeEach(() => {
      xhr = { headers: {}, upload: {}, open: vi.fn(), setRequestHeader: vi.fn((k, v) => { xhr.headers[k] = v }), send: vi.fn(), getResponseHeader: vi.fn(() => null) }
      vi.stubGlobal('XMLHttpRequest', vi.fn(() => xhr))
    })

    it('posts to the backend with the token in a header only', async () => {
      setToken(SECRET)
      const p = uploadFile(new File(['hello'], 'h.txt'))
      expect(xhr.open).toHaveBeenCalledWith('POST', `${BACKEND}/api/files`)
      expect(xhr.headers).toEqual({ Authorization: `Bearer ${SECRET}` })
      xhr.status = 201; xhr.responseText = '{"file_id":"1"}'; xhr.onload()
      await expect(p).resolves.toEqual({ file_id: '1' })
    })

    it('reports a rate limit with the wait time', async () => {
      setToken(SECRET)
      const p = uploadFile(new File(['h'], 'h.txt'))
      xhr.getResponseHeader = vi.fn(() => '12')
      xhr.status = 429; xhr.responseText = '{"error":{"code":"rate_limited","message":"x"}}'; xhr.onload()
      await expect(p).rejects.toMatchObject({ status: 429, message: expect.stringContaining('12 seconds') })
    })

    it('local mode uploads without any header', async () => {
      vi.unstubAllEnvs()
      const p = uploadFile(new File(['h'], 'h.txt'))
      expect(xhr.open).toHaveBeenCalledWith('POST', '/api/files')
      expect(xhr.setRequestHeader).not.toHaveBeenCalled()
      xhr.status = 201; xhr.responseText = '{}'; xhr.onload()
      await p
    })
  })
})

// ------------------------------------------------------------------------------------------------ error states
describe('friendly error messages', () => {
  const body = (code) => ({ error: { code, message: 'server wording' } })

  it.each([
    [401, /demo token/i], [403, /administrator/i], [429, /too many requests/i], [503, /temporarily unavailable/i], [504, /took too long/i],
  ])('cloud mode explains %i', async (status, re) => {
    cloud()
    setToken(SECRET)
    fetch.mockResolvedValue(ok(body('x'), status))
    const err = await request('/api/files/a', { method: 'DELETE' }).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(status)
    expect(err.message).toMatch(re)
    expect(err.message).not.toContain('server wording')
    expect(err.message).not.toContain(SECRET)
  })

  it('keeps the machine-readable error code from the server', async () => {
    cloud()
    setToken(SECRET)
    fetch.mockResolvedValue(ok({ error: { code: 'upload_timeout', message: 'm' } }, 504))
    await expect(request('/api/files/a', { method: 'DELETE' })).rejects.toMatchObject({ code: 'upload_timeout' })
  })

  it('429 uses Retry-After; without it the wording is still clear', async () => {
    cloud()
    setToken(SECRET)
    fetch.mockResolvedValue(ok(body('rate_limited'), 429, { 'Retry-After': '1' }))
    await expect(request('/api/files/a', { method: 'DELETE' })).rejects.toMatchObject({ message: expect.stringContaining('1 second.'), retryAfter: 1 })
    fetch.mockResolvedValue(ok(body('rate_limited'), 429))
    await expect(request('/api/files/a', { method: 'DELETE' })).rejects.toMatchObject({ message: expect.stringMatching(/wait a moment/i) })
  })

  it('a rejected token is forgotten so the visitor is asked again', async () => {
    cloud()
    setToken(SECRET)
    fetch.mockResolvedValue(ok(body('unauthorized'), 401))
    await request('/api/files/a', { method: 'DELETE' }).catch(() => {})
    expect(hasToken()).toBe(false)
  })

  it('other statuses keep the server message, and local mode never rewrites any message', async () => {
    cloud()
    setToken(SECRET)
    fetch.mockResolvedValue(ok(body('file_not_found'), 404))
    await expect(request('/api/files/a', { method: 'DELETE' })).rejects.toMatchObject({ message: 'server wording' })
    vi.unstubAllEnvs()
    for (const status of [401, 403, 429, 503, 504]) {
      fetch.mockResolvedValue(ok(body('x'), status))
      await expect(request('/api/files/a', { method: 'DELETE' })).rejects.toMatchObject({ message: 'server wording' })
    }
    expect(hasToken()).toBe(true) // local mode does not touch the token store
  })

  it('a health 503 still returns its degraded body in cloud mode', async () => {
    cloud()
    const { getHealth } = await import('../services/api')
    fetch.mockResolvedValue(ok({ status: 'degraded', database: { connected: false } }, 503))
    await expect(getHealth()).resolves.toMatchObject({ status: 'degraded' })
  })
})
