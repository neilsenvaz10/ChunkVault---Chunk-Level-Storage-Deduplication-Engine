import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, uploadFile, downloadFile } from '../services/api'
import * as fx from './fixtures'
import { renderApp } from './helpers'
import { setDefaults } from './mockApi'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual('../services/api')
  const { mockModule } = await import('./mockApi')
  return mockModule(actual)
})

const tile = (label) =>
  [...document.querySelectorAll('.tile-label')].find((n) => n.textContent === label)?.parentElement

beforeEach(() => {
  setDefaults(api)
  uploadFile.mockReset()
  downloadFile.mockReset()
})

const PAGES = [
  ['Dashboard', '/'], ['File Storage', '/files'], ['System Architecture', '/architecture'],
  ['Database Explorer', '/database'], ['Reliability & Consistency', '/reliability'],
  ['Performance Engineering', '/performance'], ['Capacity Simulation', '/simulation'],
  ['Architecture Trade-offs', '/tradeoffs'], ['System Design Documentation', '/docs'],
]

describe('navigation', () => {
  it('lists all nine pages and every one renders its title without errors', async () => {
    const user = userEvent.setup()
    renderApp('/')
    const nav = screen.getByRole('navigation', { name: 'Primary' })
    for (const [label] of PAGES) expect(within(nav).getByRole('link', { name: new RegExp(label.replace('&', '&')) })).toBeInTheDocument()
    for (const [label] of PAGES) {
      await user.click(within(nav).getByRole('link', { name: new RegExp(label) }))
      expect(await screen.findByRole('heading', { level: 1, name: label })).toBeInTheDocument()
    }
  })

  it('redirects unknown routes to the dashboard', async () => {
    renderApp('/nope')
    expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
  })

  it('collapses and expands the sidebar', async () => {
    const user = userEvent.setup()
    renderApp('/')
    const btn = screen.getByRole('button', { name: 'Collapse sidebar' })
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    await user.click(btn)
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('shows backend connection state in the top bar', async () => {
    renderApp('/')
    expect(await screen.findByText('Backend healthy')).toBeInTheDocument()
  })

  it('shows an unreachable backend instead of fabricated status', async () => {
    api.health.mockRejectedValue(Object.assign(new Error('The backend is unreachable.'), { code: 'network_error' }))
    renderApp('/')
    expect((await screen.findAllByText('Backend unreachable')).length).toBeGreaterThan(0)
  })
})

describe('dashboard', () => {
  it('renders the real numbers returned by the API', async () => {
    renderApp('/')
    await screen.findAllByText('Stored files')
    expect(tile('Stored files')).toHaveTextContent('2')
    expect(tile('Logical storage')).toHaveTextContent('2.00 MiB')
    expect(tile('Unique chunk storage')).toHaveTextContent('1.00 MiB')
    expect(tile('Unique chunks')).toHaveTextContent('49')
    expect(tile('Chunk references')).toHaveTextContent('98')
    expect(tile('Storage saved')).toHaveTextContent('50.0%')
    expect(tile('Deduplication ratio')).toHaveTextContent('2.00×')
    expect(screen.getByRole('img', { name: /Logical 2.00 MiB, unique chunk payload 1.00 MiB/ })).toBeInTheDocument()
  })

  it('handles empty storage without inventing numbers', async () => {
    api.storageStats.mockResolvedValue(fx.emptyStats)
    renderApp('/')
    expect(await screen.findByText(/No files stored yet/)).toBeInTheDocument()
    expect(tile('Storage saved')).toHaveTextContent('n/a')
    expect(tile('Deduplication ratio')).toHaveTextContent('n/a')
    expect(screen.getByText(/nothing to compare/)).toBeInTheDocument()
  })

  it('shows an error state with retry when the stats request fails', async () => {
    const user = userEvent.setup()
    api.storageStats.mockRejectedValueOnce(Object.assign(new Error('The server reported an internal error.'), { code: 'internal_error' }))
    renderApp('/')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The server reported an internal error.')
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect((await screen.findAllByText('Stored files')).length).toBeGreaterThan(0)
  })

  it('links the pipeline to the architecture page', async () => {
    const user = userEvent.setup()
    renderApp('/')
    await user.click(screen.getByRole('link', { name: /CDC/ }))
    expect(await screen.findByRole('heading', { level: 1, name: 'System Architecture' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'CDC engine' })).toBeInTheDocument() // focus preselects the component
  })
})

describe('file storage', () => {
  it('lists real files with their metadata', async () => {
    renderApp('/files')
    const row = (await screen.findByText('original.bin')).closest('tr')
    expect(row).toHaveTextContent('29.3 KiB')
    expect(row).toHaveTextContent('3')
    expect(row).toHaveTextContent('complete')
    expect(within(row).getByRole('link', { name: 'Inspect' })).toHaveAttribute('href', `/files/${fx.fileA.file_id}`)
  })

  it('shows an empty state when there are no files', async () => {
    api.listFiles.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 })
    renderApp('/files')
    expect(await screen.findByText(/No files stored yet/)).toBeInTheDocument()
  })

  it('shows a list error state', async () => {
    api.listFiles.mockRejectedValue(Object.assign(new Error('Backend down'), { code: 'network_error' }))
    renderApp('/files')
    expect(await screen.findByRole('alert')).toHaveTextContent('Backend down')
  })

  it('uploads a file and shows the actual result fields', async () => {
    const user = userEvent.setup()
    uploadFile.mockImplementation(async (file, { onProgress }) => {
      onProgress({ loaded: 30000, total: 30000 })
      return fx.uploadResult
    })
    renderApp('/files')
    await screen.findByText('original.bin')
    const file = new File([new Uint8Array(30000)], 'new.bin')
    await user.upload(screen.getByLabelText('Choose file'), file)
    expect(screen.getByText('new.bin')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Upload' }))
    const ok = await screen.findByText('Upload stored')
    const box = ok.closest('.notice')
    expect(box).toHaveTextContent(fx.uploadResult.file_id)
    expect(box).toHaveTextContent('Chunks3')
    expect(box).toHaveTextContent('New chunks1')
    expect(box).toHaveTextContent('Reused chunk occurrences2')
    expect(box).toHaveTextContent('Newly stored bytes9.77 KiB')
    expect(uploadFile).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(api.listFiles.mock.calls.length).toBeGreaterThan(1)) // refreshed after mutation
  })

  it('reports an upload failure with the server message and does not retry', async () => {
    const user = userEvent.setup()
    uploadFile.mockRejectedValue(Object.assign(new Error('Request body exceeds the size limit.'), { code: 'upload_too_large', status: 413 }))
    renderApp('/files')
    await screen.findByText('original.bin')
    await user.upload(screen.getByLabelText('Choose file'), new File(['abc'], 'x.bin'))
    await user.click(screen.getByRole('button', { name: 'Upload' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Request body exceeds the size limit.')
    expect(uploadFile).toHaveBeenCalledTimes(1)
  })

  it('validates file size against the server limit before sending', async () => {
    const user = userEvent.setup()
    api.health.mockResolvedValue({ ...fx.health, max_upload_bytes: 1000 })
    renderApp('/files')
    await screen.findByText('original.bin')
    await screen.findByText(/Server limit/)
    await user.upload(screen.getByLabelText('Choose file'), new File([new Uint8Array(2000)], 'big.bin'))
    expect(screen.getByRole('alert')).toHaveTextContent(/accepts at most/)
    expect(screen.getByRole('button', { name: 'Upload' })).toBeDisabled()
    expect(uploadFile).not.toHaveBeenCalled()
  })

  it('downloads through the verified endpoint and surfaces integrity errors', async () => {
    const user = userEvent.setup()
    downloadFile.mockResolvedValueOnce({ size: 30000, sha256: fx.fileA.sha256 })
    renderApp('/files')
    const row = (await screen.findByText('original.bin')).closest('tr')
    await user.click(within(row).getByRole('button', { name: 'Download' }))
    expect(downloadFile).toHaveBeenCalledWith(fx.fileA.file_id, 'original.bin')
    expect(await screen.findByText(/Restored "original.bin"/)).toBeInTheDocument()

    downloadFile.mockRejectedValueOnce(Object.assign(new Error('chunk 1 failed verification'), { code: 'chunk_corrupt', status: 500 }))
    await user.click(within(row).getByRole('button', { name: 'Download' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('chunk 1 failed verification')
  })

  it('asks for confirmation before deleting and refreshes afterwards', async () => {
    const user = userEvent.setup()
    renderApp('/files')
    const row = (await screen.findByText('original.bin')).closest('tr')
    await user.click(within(row).getByRole('button', { name: 'Delete' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete file?' })
    expect(dialog).toHaveTextContent('original.bin')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(api.deleteFile).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    const before = api.listFiles.mock.calls.length
    await user.click(within(row).getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete file' }))
    expect(api.deleteFile).toHaveBeenCalledWith(fx.fileA.file_id)
    expect(await screen.findByText(/3 chunk reference\(s\) released, 1 chunk file\(s\)/)).toBeInTheDocument()
    await waitFor(() => expect(api.listFiles.mock.calls.length).toBeGreaterThan(before))
  })
})

describe('file inspection and chunk explorer', () => {
  it('shows the ordered manifest and inspects a chunk from the map', async () => {
    const user = userEvent.setup()
    renderApp(`/files/${fx.fileA.file_id}`)
    await screen.findByRole('heading', { name: 'original.bin' })
    const manifest = screen.getByText('Ordered chunk manifest').closest('section')
    const rows = within(manifest).getAllByRole('row').slice(1)
    expect(rows.map((r) => r.cells[0].textContent)).toEqual(['0', '1', '2'])
    expect(rows[1]).toHaveTextContent('2') // ref_count of the shared chunk
    expect(rows[1].cells[4]).toHaveTextContent('yes')
    expect(rows[0].cells[4]).toHaveTextContent('no')

    await user.click(screen.getByRole('button', { name: /Chunk 1, 9.77 KiB/ }))
    const inspector = (await screen.findByRole('heading', { name: 'Chunk #1' })).closest('section')
    expect(api.getChunk).toHaveBeenCalledWith('d'.repeat(64), expect.anything())
    expect(inspector).toHaveTextContent('stored')
    expect(inspector).toHaveTextContent('modified.bin')
    expect(inspector).toHaveTextContent('2 file(s)')
  })

  it('does not infer upload-time decisions for a historical file', async () => {
    renderApp(`/files/${fx.fileA.file_id}`)
    expect(await screen.findByText(/not stored by the backend/)).toBeInTheDocument()
    expect(screen.getByText(/Currently referenced more than once/)).toBeInTheDocument()
    expect(screen.queryByText(/Reused: chunk already stored/)).not.toBeInTheDocument()
  })

  it('uses the upload response for new/reused/repeated evidence after an upload', async () => {
    const user = userEvent.setup()
    uploadFile.mockResolvedValue({ ...fx.uploadResult, file_id: fx.fileA.file_id })
    api.listFiles.mockResolvedValue(fx.fileList)
    renderApp('/files')
    await screen.findByText('original.bin')
    await user.upload(screen.getByLabelText('Choose file'), new File(['abc'], 'x.bin'))
    await user.click(screen.getByRole('button', { name: 'Upload' }))
    await user.click(await screen.findByRole('link', { name: /Inspect chunk map/ }))
    await screen.findByRole('heading', { name: 'original.bin' })
    expect(screen.getByText(/Reused: chunk already stored/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Chunk 0, 9.77 KiB, new/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Chunk 1, 9.77 KiB, reused/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Chunk 2, 9.77 KiB, repeated_in_file/ })).toBeInTheDocument()
  })

  it('shows an error for a missing file', async () => {
    api.getFile.mockRejectedValue(Object.assign(new Error('file not found'), { code: 'file_not_found', status: 404 }))
    renderApp(`/files/${'0'.repeat(32)}`)
    expect(await screen.findByRole('alert')).toHaveTextContent('file not found')
  })
})

describe('file comparison', () => {
  it('compares two stored manifests using real chunk hashes', async () => {
    renderApp(`/files/compare?a=${fx.fileA.file_id}&b=${fx.fileB.file_id}`)
    const result = (await screen.findByText(/Result \(from the two stored manifests\)/)).closest('section')
    expect(result.querySelector('.tile-label')).toBeTruthy()
    const tile = (label) => within(result).getByText(label).parentElement
    expect(tile('Chunks in common')).toHaveTextContent('2')
    expect(tile('Only in original.bin')).toHaveTextContent('1')
    expect(tile('Only in modified.bin')).toHaveTextContent('1')
    expect(tile('Shared payload')).toHaveTextContent('19.5 KiB')
    expect(result).toHaveTextContent('keep their relative order')
    expect(result).toHaveTextContent('Reuse: modified.bin from original.bin')
  })

  it('asks for two files when none are selected', async () => {
    renderApp('/files/compare')
    expect(await screen.findByText(/Select two files to compare/)).toBeInTheDocument()
  })
})
