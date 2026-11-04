import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, downloadFile, request, uploadFile } from '../services/api'
import { compareManifests } from '../utils/compare'
import { formatBytes, formatPercent, formatRatio } from '../utils/format'

const m = (list) => list.map(([i, h, s]) => ({ chunk_index: i, chunk_hash: h, chunk_size: s }))

describe('formatting', () => {
  it('formats sizes, percentages and ratios, and never invents values for null', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1536)).toBe('1.50 KiB')
    expect(formatBytes(2097152)).toBe('2.00 MiB')
    expect(formatBytes(null)).toBe('—')
    expect(formatPercent(null)).toBe('n/a')
    expect(formatPercent(50)).toBe('50.0%')
    expect(formatRatio(null)).toBe('n/a')
    expect(formatRatio(2)).toBe('2.00×')
  })
})

describe('compareManifests', () => {
  it('computes shared, unique and reuse figures from manifests', () => {
    const a = m([[0, 'x', 100], [1, 'y', 200], [2, 'z', 300]])
    const b = m([[0, 'n', 50], [1, 'y', 200], [2, 'z', 300], [3, 'y', 200]])
    const r = compareManifests(a, b)
    expect(r.commonDistinct).toBe(2)
    expect(r.onlyADistinct).toBe(1)
    expect(r.onlyBDistinct).toBe(1)
    expect(r.sharedPayloadBytes).toBe(500)
    expect(r.totalA).toBe(600)
    expect(r.totalB).toBe(750)
    expect(r.reusedBytesB).toBe(700) // positions 1,2,3 of B
    expect(r.reusePctB).toBeCloseTo((700 / 750) * 100)
    expect(r.reusePctA).toBeCloseTo((500 / 600) * 100)
    expect(r.commonChunks.find((c) => c.hash === 'y').indexesB).toEqual([1, 3])
    expect(r.shifts).toEqual([0])
    expect(r.isCommon('x')).toBe(false)
  })

  it('reports a uniform shift when content moved by an insertion', () => {
    const a = m([[0, 'a', 10], [1, 'b', 10], [2, 'c', 10]])
    const b = m([[0, 'new', 10], [1, 'a', 10], [2, 'b', 10], [3, 'c', 10]])
    const r = compareManifests(a, b)
    expect(r.shifts).toEqual([1])
    expect(r.sharedChunksKeepRelativeOrder).toBe(true)
  })

  it('handles empty and disjoint manifests', () => {
    expect(compareManifests([], []).reusePctA).toBeNull()
    const r = compareManifests(m([[0, 'a', 5]]), m([[0, 'b', 5]]))
    expect(r.commonDistinct).toBe(0)
    expect(r.reusePctA).toBe(0)
    expect(r.shifts).toEqual([])
  })
})

describe('api client', () => {
  const ok = (body, status = 200) => ({ ok: status < 400, status, statusText: 'x', json: async () => body })
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()))
  afterEach(() => vi.unstubAllGlobals())

  it('parses the backend error envelope', async () => {
    fetch.mockResolvedValue(ok({ error: { code: 'file_not_found', message: 'file not found', details: null } }, 404))
    await expect(request('/api/files/x')).rejects.toMatchObject({ name: 'ApiError', status: 404, code: 'file_not_found', message: 'file not found' })
  })

  it('falls back to a generic message for non-JSON failures and never leaks bodies', async () => {
    fetch.mockResolvedValue({ ok: false, status: 500, statusText: 'Internal Server Error', json: async () => { throw new Error('not json') } })
    await expect(request('/api/x')).rejects.toMatchObject({ code: 'http_500', message: 'The server reported an internal error.' })
  })

  it('retries a GET once after a network failure', async () => {
    fetch.mockRejectedValueOnce(new TypeError('failed')).mockResolvedValueOnce(ok({ a: 1 }))
    await expect(request('/api/files')).resolves.toEqual({ a: 1 })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('reports an unreachable backend after the GET retry is exhausted', async () => {
    fetch.mockRejectedValue(new TypeError('failed'))
    await expect(request('/api/files')).rejects.toMatchObject({ code: 'network_error', status: 0 })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('never retries mutations', async () => {
    fetch.mockRejectedValue(new TypeError('failed'))
    await expect(request('/api/files/x', { method: 'DELETE' })).rejects.toBeInstanceOf(ApiError)
    expect(fetch).toHaveBeenCalledTimes(1)
    fetch.mockClear()
    await expect(request('/api/integrity/reconcile', { method: 'POST' })).rejects.toBeInstanceOf(ApiError)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('does not retry an aborted request', async () => {
    fetch.mockRejectedValue(Object.assign(new Error('aborted'), { name: 'AbortError' }))
    await expect(request('/api/files')).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('downloads via the verified endpoint and does not save when the server refuses', async () => {
    fetch.mockResolvedValue(ok({ error: { code: 'chunk_corrupt', message: 'chunk 0 failed verification', details: null } }, 500))
    const click = vi.fn()
    const create = vi.spyOn(document, 'createElement')
    await expect(downloadFile('a'.repeat(32), 'f.bin')).rejects.toMatchObject({ code: 'chunk_corrupt' })
    expect(create).not.toHaveBeenCalledWith('a')
    expect(click).not.toHaveBeenCalled()
    create.mockRestore()
  })
})

describe('uploadFile (XMLHttpRequest)', () => {
  let instance
  beforeEach(() => {
    class FakeXHR {
      constructor() { instance = this; this.upload = {}; this.status = 0; this.responseText = '' }
      open(method, url) { this.method = method; this.url = url }
      send(form) { this.form = form }
      abort() { this.onabort?.() }
    }
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('reports transfer progress and resolves with the parsed body', async () => {
    const progress = vi.fn()
    const p = uploadFile(new File(['abc'], 'a.bin'), { onProgress: progress })
    instance.upload.onprogress({ lengthComputable: true, loaded: 1, total: 3 })
    instance.status = 201
    instance.responseText = JSON.stringify({ file_id: 'x' })
    instance.onload()
    await expect(p).resolves.toEqual({ file_id: 'x' })
    expect(progress).toHaveBeenCalledWith({ loaded: 1, total: 3 })
    expect(instance.method).toBe('POST')
    expect(instance.form.get('file').name).toBe('a.bin')
  })

  it('turns server errors into ApiError using the envelope', async () => {
    const p = uploadFile(new File(['abc'], 'a.bin'))
    instance.status = 413
    instance.responseText = JSON.stringify({ error: { code: 'upload_too_large', message: 'Request body exceeds the size limit.' } })
    instance.onload()
    await expect(p).rejects.toMatchObject({ status: 413, code: 'upload_too_large' })
  })

  it('reports network failure and supports cancellation', async () => {
    const p = uploadFile(new File(['abc'], 'a.bin'))
    instance.onerror()
    await expect(p).rejects.toMatchObject({ code: 'network_error' })
    const controller = new AbortController()
    const q = uploadFile(new File(['abc'], 'a.bin'), { signal: controller.signal })
    controller.abort()
    await expect(q).rejects.toMatchObject({ name: 'AbortError' })
  })
})
