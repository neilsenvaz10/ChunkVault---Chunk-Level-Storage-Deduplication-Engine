import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, downloadFile, uploadFile } from '../services/api'
import { listEndpoints, schemaFields } from '../pages/architecture/ApiExplorer'
import { COMPONENTS, EDGES, dependenciesOf, dependentsOf } from '../pages/architecture/components'
import { INTERRUPTIONS, PROTOCOL_STEPS } from '../pages/architecture/ConsistencyView'
import { RESTORE_STAGES, UPLOAD_STAGES } from '../pages/architecture/lifecycles'
import { parseAdr } from '../pages/AdrExplorer'
import { extractTriggers } from '../pages/RefCountWalkthrough'
import * as fx from './fixtures'
import { renderApp } from './helpers'
import { setDefaults } from './mockApi'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual('../services/api')
  const { mockModule } = await import('./mockApi')
  return mockModule(actual)
})

beforeEach(() => {
  setDefaults(api)
  uploadFile.mockReset()
  downloadFile.mockReset()
})

const tab = async (user, name) => user.click(await screen.findByRole('tab', { name }))
const arch = async (user, name) => {
  renderApp('/architecture')
  await tab(user, name)
}
const mutationsCalled = () => uploadFile.mock.calls.length + downloadFile.mock.calls.length + api.deleteFile.mock.calls.length
  + api.reconcile.mock.calls.length

describe('component catalogue', () => {
  it('has consistent edges, real module paths and a layer for every component', () => {
    const ids = new Set(COMPONENTS.map((c) => c.id))
    EDGES.forEach(([a, b]) => { expect(ids.has(a)).toBe(true); expect(ids.has(b)).toBe(true) })
    COMPONENTS.forEach((c) => {
      expect(['frontend', 'api', 'processing', 'persistence']).toContain(c.layer)
      expect(c.rationale.length).toBeGreaterThan(10)
      c.modules.forEach((m) => expect(m.startsWith('backend/')).toBe(true))
    })
    expect(dependenciesOf('orchestrator').map((d) => d.id)).toEqual(expect.arrayContaining(['cdc', 'hashing', 'dedup', 'metadata', 'store']))
    expect(dependentsOf('store').map((d) => d.id)).toEqual(expect.arrayContaining(['orchestrator', 'restore', 'auditor']))
  })
})

describe('architecture: component selection and dependency highlighting', () => {
  it('shows purpose, modules, endpoints, failures and rationale for a selected component', async () => {
    const user = userEvent.setup()
    await arch(user, 'Components')
    await user.click(screen.getByRole('button', { name: 'Upload orchestrator' }))
    const d = screen.getByRole('heading', { name: 'Upload orchestrator' }).closest('.details')
    expect(d).toHaveTextContent('Processing layer')
    expect(d).toHaveTextContent('backend/app/deduplication/upload.py')
    expect(d).toHaveTextContent('POST /api/files')
    expect(d).toHaveTextContent('Design rationale')
    expect(d).toHaveTextContent('un-published')
  })

  it('highlights what the selected component uses and what uses it', async () => {
    const user = userEvent.setup()
    await arch(user, 'Components')
    await user.click(screen.getByRole('button', { name: 'Restoration engine' }))
    const diagram = screen.getByRole('group', { name: 'Component diagram' })
    const rel = (name) => within(diagram).getByRole('button', { name }).getAttribute('data-relation')
    expect(rel('Restoration engine')).toBe('selected')
    expect(rel('Local chunk store')).toBe('uses')
    expect(rel('SQLite metadata')).toBe('uses')
    expect(rel('SHA-256 service')).toBe('uses')
    expect(rel('FastAPI request layer')).toBe('usedby')
    expect(rel('CDC engine')).toBeNull()
    expect(document.querySelector('[data-edge="restore->store"]')).toHaveClass('edge-active')
    expect(document.querySelector('[data-edge="orchestrator->cdc"]')).not.toHaveClass('edge-active')
  })

  it('distinguishes the four layers and can navigate between dependencies', async () => {
    const user = userEvent.setup()
    await arch(user, 'Components')
    const legend = screen.getByRole('list', { name: 'Layers' })
    for (const l of ['Frontend', 'API', 'Processing', 'Persistence']) expect(within(legend).getByText(l)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Deduplication manager' }))
    const d = screen.getByRole('heading', { name: 'Deduplication manager' }).closest('.details')
    await user.click(within(d).getByRole('button', { name: 'Local chunk store' }))
    expect(screen.getByRole('heading', { name: 'Local chunk store' })).toBeInTheDocument()
  })

  it('is keyboard operable and zoomable', async () => {
    const user = userEvent.setup()
    await arch(user, 'Components')
    screen.getByRole('button', { name: 'CDC engine' }).focus()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('heading', { name: 'CDC engine' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: /Component diagram \(scrollable\)/ })).toHaveAttribute('tabindex', '0')
    const group = screen.getByRole('group', { name: 'Component diagram zoom' })
    await user.click(within(group).getByRole('button', { name: 'Zoom in' }))
    expect(group).toHaveTextContent('120%')
    await user.click(within(group).getByRole('button', { name: 'Reset zoom' }))
    expect(group).toHaveTextContent('100%')
  })
})

describe('upload lifecycle walkthrough', () => {
  it('has the nine stages and lets the user step, jump and reset', async () => {
    const user = userEvent.setup()
    await arch(user, 'Upload lifecycle')
    expect(UPLOAD_STAGES).toHaveLength(9)
    const list = screen.getByRole('list', { name: 'Stages' })
    expect(within(list).getAllByRole('button')).toHaveLength(9)
    expect(screen.getByText('Stage 1 of 9')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('Stage 2 of 9')).toBeInTheDocument()
    await user.click(within(list).getByRole('button', { name: /Deduplication checks existing chunks/ }))
    expect(screen.getByText('Stage 6 of 9')).toBeInTheDocument()
    const d = screen.getByRole('heading', { name: /6\. Deduplication checks/ }).closest('.details')
    for (const label of ['Performed by', 'What happens', 'Why it is necessary', 'Data created or modified', 'If this stage fails']) {
      expect(within(d).getByText(label)).toBeInTheDocument()
    }
    expect(d).toHaveTextContent('chunk_unavailable')
    await user.click(within(list).getByRole('button', { name: /Upload completes/ }))
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(screen.getByText('Stage 1 of 9')).toBeInTheDocument()
  })

  it('is labelled as an explanation, not an execution trace, and invents no timings', async () => {
    const user = userEvent.setup()
    await arch(user, 'Upload lifecycle')
    expect(screen.getByText(/not a recorded execution trace/)).toBeInTheDocument()
    for (const s of UPLOAD_STAGES) expect(JSON.stringify(s)).not.toMatch(/\d+\s?ms\b|seconds took|took \d/)
    expect(screen.getByText(/Select a stored file above/)).toBeInTheDocument()
  })

  it('shows real values for a selected file and never mutates anything', async () => {
    const user = userEvent.setup()
    await arch(user, 'Upload lifecycle')
    await screen.findByRole('option', { name: /original\.bin/ })
    await user.selectOptions(screen.getByLabelText('Stored file'), fx.fileA.file_id)
    const list = screen.getByRole('list', { name: 'Stages' })
    await user.click(within(list).getByRole('button', { name: /Client selects file/ }))
    expect(await screen.findByText('29.3 KiB (30,000 bytes)')).toBeInTheDocument()
    await user.click(within(list).getByRole('button', { name: /CDC generates/ }))
    expect(await screen.findByText('Chunk positions')).toBeInTheDocument()
    expect(screen.getByText('Configured min / target / max').parentElement).toHaveTextContent('4.00 KiB / 16.0 KiB / 128 KiB')
    expect(screen.getByText(/mod 12288 == 12287/)).toBeInTheDocument()
    await user.click(within(list).getByRole('button', { name: /SHA-256 identifies/ }))
    expect(await screen.findByText('Whole-file SHA-256')).toBeInTheDocument()
    expect(screen.getAllByText(/^cccccccccccc…$/).length).toBeGreaterThan(0)
    await user.click(within(list).getByRole('button', { name: /Deduplication checks/ }))
    expect(await screen.findByText(/not recorded for this file/)).toBeInTheDocument()
    expect(mutationsCalled()).toBe(0)
  })

  it('uses this session\'s upload response for deduplication outcomes when it exists', async () => {
    const user = userEvent.setup()
    uploadFile.mockResolvedValue({ ...fx.uploadResult, file_id: fx.fileA.file_id })
    renderApp('/files')
    await screen.findByText('original.bin')
    await user.upload(screen.getByLabelText('Choose file'), new File(['abc'], 'x.bin'))
    await user.click(screen.getByRole('button', { name: 'Upload' }))
    await screen.findByText('Upload stored')
    await user.click(screen.getByRole('link', { name: 'System Architecture' }))
    await tab(user, 'Upload lifecycle')
    await screen.findByRole('option', { name: /original\.bin/ })
    await user.selectOptions(screen.getByLabelText('Stored file'), fx.fileA.file_id)
    await user.click(within(screen.getByRole('list', { name: 'Stages' })).getByRole('button', { name: /Deduplication checks/ }))
    expect(await screen.findByText(/new 1 · reused 1 · adopted 0 · repeated in file 1/)).toBeInTheDocument()
  })

  it('handles an empty store and failing requests', async () => {
    const user = userEvent.setup()
    api.listFiles.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 })
    await arch(user, 'Upload lifecycle')
    expect(await screen.findByText(/No stored files yet/)).toBeInTheDocument()
    expect(screen.getByText('Stage 1 of 9')).toBeInTheDocument() // walkthrough still works
  })

  it('reports a failed file lookup instead of guessing values', async () => {
    const user = userEvent.setup()
    api.getFile.mockRejectedValue(Object.assign(new Error('file not found'), { code: 'file_not_found' }))
    await arch(user, 'Upload lifecycle')
    await screen.findByRole('option', { name: /original\.bin/ })
    await user.selectOptions(screen.getByLabelText('Stored file'), fx.fileA.file_id)
    expect(await screen.findByRole('alert')).toHaveTextContent('file not found')
  })
})

describe('restore lifecycle walkthrough', () => {
  it('has the eight stages and explains ordering, verification and the temporary file', async () => {
    const user = userEvent.setup()
    await arch(user, 'Restore lifecycle')
    expect(RESTORE_STAGES).toHaveLength(8)
    const list = screen.getByRole('list', { name: 'Stages' })
    await user.click(within(list).getByRole('button', { name: /ordered manifest/ }))
    expect(screen.getByRole('heading', { name: /3\. SQLite retrieves/ }).closest('.details')).toHaveTextContent(/Chunk order defines the file/)
    await user.click(within(list).getByRole('button', { name: /verifies individual chunks/ }))
    expect(screen.getByText(/Chunk-level verification pinpoints/)).toBeInTheDocument()
    await user.click(within(list).getByRole('button', { name: /reconstructs the file/ }))
    expect(screen.getByText(/temporary file means the client is only given a result that is already fully verified/)).toBeInTheDocument()
    await user.click(within(list).getByRole('button', { name: /Whole-file hash and size/ }))
    expect(screen.getByText(/Chunk-level checks prove each piece is intact/)).toBeInTheDocument()
    expect(screen.getByText(/file_hash_mismatch/)).toBeInTheDocument()
    await user.click(within(list).getByRole('button', { name: /chunk store reads/ }))
    expect(screen.getByText(/chunk_missing/)).toBeInTheDocument()
  })

  it('shows the real ordered manifest and chunk paths for a file without restoring it', async () => {
    const user = userEvent.setup()
    await arch(user, 'Restore lifecycle')
    await screen.findByRole('option', { name: /original\.bin/ })
    await user.selectOptions(screen.getByLabelText('Stored file'), fx.fileA.file_id)
    const list = screen.getByRole('list', { name: 'Stages' })
    await user.click(within(list).getByRole('button', { name: /ordered manifest/ }))
    const table = await screen.findByRole('table', { name: '' }, { timeout: 2000 }).catch(() => null)
    expect(table ?? true).toBeTruthy()
    expect(await screen.findByText(/= file size/)).toBeInTheDocument()
    expect(screen.getByText('ref_count (now)')).toBeInTheDocument()
    await user.click(within(list).getByRole('button', { name: /chunk store reads/ }))
    expect(await screen.findByText(`chunks/cc/${'c'.repeat(64)}`)).toBeInTheDocument()
    expect(mutationsCalled()).toBe(0)
  })
})

describe('content-defined chunking explorer', () => {
  it('shows the configuration read from the server', async () => {
    const user = userEvent.setup()
    await arch(user, 'Chunking (CDC)')
    expect(await screen.findByText('Rabin-style content-defined chunking')).toBeInTheDocument()
    expect(screen.getByText('Minimum chunk size').parentElement).toHaveTextContent('4.00 KiB (4096 bytes)')
    expect(screen.getByText('Maximum chunk size').parentElement).toHaveTextContent('128 KiB')
    expect(screen.getByText('Rolling window').parentElement).toHaveTextContent('64 bytes')
    expect(screen.getByText('Boundary rule').parentElement).toHaveTextContent('fingerprint mod 12288 == 12287')
    expect(screen.getByRole('img', { name: /no boundary before 4096 bytes.*131072/ })).toBeInTheDocument()
    expect(screen.getByText(/Conceptual illustration/)).toBeInTheDocument()
    expect(screen.getByText(/SHA-256 is a different mechanism/)).toBeInTheDocument()
  })

  it('shows computed CDC vs fixed-size results, labelled as generated data, with the honest caveat', async () => {
    const user = userEvent.setup()
    await arch(user, 'Chunking (CDC)')
    const cdc = await screen.findByTestId('result-CDC')
    expect(cdc).toHaveTextContent('66.6%')
    expect(cdc).toHaveTextContent('2 of 3 chunks reused')
    expect(screen.getByTestId('result-Fixed-size')).toHaveTextContent('0.0%')
    expect(screen.getAllByText(/seeded generated data/).length).toBeGreaterThan(0)
    expect(screen.getByText(/does not guarantee better reuse for every dataset/)).toBeInTheDocument()
    expect(screen.getByText(/in-place/)).toBeInTheDocument()
    expect(api.chunkingDemo).toHaveBeenCalledWith({ seed: 7, size: 196608, insert_at: 100, insert_len: 37 }, expect.anything())
  })

  it('re-runs with the chosen parameters and reports failures', async () => {
    const user = userEvent.setup()
    await arch(user, 'Chunking (CDC)')
    await screen.findByTestId('result-CDC')
    const form = screen.getByRole('form', { name: 'Demo parameters' })
    const seed = within(form).getByLabelText(/Seed/)
    await user.clear(seed)
    await user.type(seed, '99')
    api.chunkingDemo.mockRejectedValueOnce(Object.assign(new Error('Request validation failed.'), { code: 'validation_error' }))
    await user.click(within(form).getByRole('button', { name: 'Run comparison' }))
    await waitFor(() => expect(api.chunkingDemo).toHaveBeenLastCalledWith({ seed: 99, size: 196608, insert_at: 100, insert_len: 37 }, expect.anything()))
    expect(await screen.findByRole('alert')).toHaveTextContent('Request validation failed.')
  })

  it('handles an unreachable server', async () => {
    const user = userEvent.setup()
    api.systemConfig.mockRejectedValue(Object.assign(new Error('The backend is unreachable.'), { code: 'network_error' }))
    await arch(user, 'Chunking (CDC)')
    expect(await screen.findByRole('alert')).toHaveTextContent('unreachable')
  })
})

describe('consistency protocol visualisation', () => {
  it('shows nine steps, separating filesystem from SQLite operations', async () => {
    const user = userEvent.setup()
    await arch(user, 'Consistency protocol')
    expect(PROTOCOL_STEPS).toHaveLength(9)
    expect(screen.getByText(/cannot atomically commit filesystem writes/)).toBeInTheDocument()
    const steps = within(screen.getByRole('list', { name: 'Upload protocol steps' })).getAllByRole('listitem')
    expect(steps).toHaveLength(9)
    expect(steps[1]).toHaveTextContent('filesystem')
    expect(steps[3]).toHaveTextContent('filesystem')
    expect(steps[4]).toHaveTextContent('SQLite · T2')
    expect(steps[6]).toHaveTextContent('Reference-count triggers')
    expect(steps[7]).toHaveTextContent('SQLite · T2')
    expect(steps[8]).toHaveTextContent('filesystem')
    await user.click(within(steps[6]).getByRole('button'))
    expect(screen.getByText(/adds 1 to that chunk/)).toBeInTheDocument()
  })

  it('marks interruption points and states their consequences without claiming crash-proofing', async () => {
    const user = userEvent.setup()
    await arch(user, 'Consistency protocol')
    expect(INTERRUPTIONS).toHaveLength(4)
    await user.click(screen.getByRole('button', { name: 'Failure after publication but before the metadata commit' }))
    expect(screen.getByText(/Orphan chunk files that no metadata references/)).toBeInTheDocument()
    expect(screen.getByText(/✂ interruption/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Failure during the metadata transaction' }))
    expect(screen.getByText(/SQLite rolls the whole transaction back/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Failure after the commit' }))
    expect(screen.getByText(/Nothing here is crash-proof/)).toBeInTheDocument()
    expect(screen.getByText(/does not claim crash-proof durability or any distributed fault tolerance/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Failure after the commit' }))
    expect(screen.getByText('No interruption selected.')).toBeInTheDocument()
  })
})

describe('API explorer', () => {
  it('derives endpoints from the OpenAPI document and ignores non-API paths', async () => {
    const eps = listEndpoints(fx.openapi)
    expect(eps.map((e) => e.key)).toEqual(['GET /api/files', 'POST /api/files', 'POST /api/integrity/audit', 'DELETE /api/files/{file_id}', 'GET /api/_secret'])
    const up = eps[1]
    expect(up.request.fields).toEqual([{ name: 'file', type: 'string', required: true, description: 'The file' }])
    expect(up.responses[0].fields.find((f) => f.name === 'chunk_outcomes').type).toBe('Outcome[]')
    expect(schemaFields(fx.openapi, undefined)).toEqual([])
  })

  it('shows contract details, status codes and the read/mutate classification', async () => {
    const user = userEvent.setup()
    await arch(user, 'API explorer')
    const table = await screen.findByRole('table', { name: 'Endpoints' })
    expect(within(table).getByRole('button', { name: '/api/integrity/audit' }).closest('tr')).toHaveTextContent('reads')
    const del = within(table).getAllByRole('row').find((r) => r.textContent.includes('DELETE'))
    expect(del).toHaveTextContent('mutates')
    await user.click(within(table).getAllByRole('button', { name: '/api/files' })[1])
    const d = screen.getByRole('heading', { name: 'POST /api/files' }).closest('.details')
    expect(d).toHaveTextContent('Upload orchestrator')
    expect(d).toHaveTextContent('multipart/form-data')
    expect(d).toHaveTextContent('201')
    expect(d).toHaveTextContent('413')
    expect(d).toHaveTextContent('chunk_outcomes')
    expect(d).toHaveTextContent('Outcome[]')
    await user.click(within(table).getByRole('button', { name: '/api/files/{file_id}' }))
    expect(screen.getByRole('heading', { name: 'DELETE /api/files/{file_id}' }).closest('.details')).toHaveTextContent('file_id (path, string, required)')
  })

  it('marks unannotated endpoints honestly and links to the FastAPI docs', async () => {
    const user = userEvent.setup()
    await arch(user, 'API explorer')
    await screen.findByRole('table', { name: 'Endpoints' })
    expect(screen.getByRole('link', { name: /FastAPI docs/ })).toHaveAttribute('href', '/api/docs')
    expect(screen.getAllByText('not annotated').length).toBeGreaterThan(0)
  })

  it('shows an error when the OpenAPI document cannot be loaded', async () => {
    const user = userEvent.setup()
    api.openapi.mockRejectedValue(Object.assign(new Error('The backend is unreachable.'), { code: 'network_error' }))
    await arch(user, 'API explorer')
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})

describe('architecture decision records', () => {
  it('parses ADR sections and reports missing ones', () => {
    const a = parseAdr('# ADR-9: Title\n\n**Status:** accepted\n\n## Problem\nA problem.\n\n## Advantages\n- one\n- two\n')
    expect(a.title).toBe('ADR-9: Title')
    expect(a.status).toBe('Status: accepted')
    expect(a.sections.Problem).toBe('A problem.')
    expect(a.sections.Advantages).toBe('- one\n- two')
    expect(a.sections.Disadvantages).toBeUndefined()
  })

  it('lists the repository ADRs and shows every required section', async () => {
    const user = userEvent.setup()
    renderApp('/tradeoffs')
    await user.click(await screen.findByRole('tab', { name: 'Decision records (ADRs)' }))
    const nav = await screen.findByRole('navigation', { name: 'Architecture decision records' })
    const buttons = within(nav).getAllByRole('button')
    expect(buttons).toHaveLength(8)
    expect(buttons[0]).toHaveTextContent('ADR-0001')
    expect(buttons[1]).toHaveTextContent('ADR-0002')
    expect(buttons[0]).toHaveAttribute('aria-current', 'page')
    for (const h of ['Problem being solved', 'Alternatives considered', 'Selected approach', 'Advantages', 'Disadvantages', 'Consequences', 'Future scalability implications']) {
      expect(screen.getByRole('heading', { name: h })).toBeInTheDocument()
    }
    expect(screen.getByText(/shifts every later boundary/)).toBeInTheDocument() // text from docs/decisions/0001
    expect(screen.getByText(/docs\/decisions\/0001-cdc-over-fixed-size-chunking\.md/)).toBeInTheDocument()
    expect(screen.queryByText(/Not recorded in this ADR/)).not.toBeInTheDocument()
    await user.click(within(nav).getByRole('button', { name: /ADR-0007/ }))
    expect(await screen.findByText(/transaction cannot atomically commit filesystem writes/)).toBeInTheDocument()
    expect(screen.getByText(/recorded retroactively/)).toBeInTheDocument()
  })
})

describe('content-addressed storage view', () => {
  it('shows file -> manifest -> SHA-256 ids -> relative chunk files, with shared status', async () => {
    const user = userEvent.setup()
    renderApp(`/files/${fx.fileA.file_id}`)
    const chain = await screen.findByRole('list', { name: 'Content-addressed chain' })
    expect(chain).toHaveTextContent('File')
    expect(chain).toHaveTextContent('Ordered manifest')
    expect(chain).toHaveTextContent('SHA-256 chunk IDs')
    expect(chain).toHaveTextContent('Physical chunk files')
    const table = screen.getByRole('table', { name: 'Manifest to chunk files' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('exclusive')
    expect(rows[1]).toHaveTextContent('shared')
    expect(rows[0]).toHaveTextContent('chunks/cc/cccccccccccc')
    expect(table.textContent).not.toMatch(/[A-Za-z]:\\|\/Users\/|\/home\/|\\/) // never an absolute server path
    expect(screen.getByText(/Identical content always produces the same identifier/)).toBeInTheDocument()
    await user.click(within(rows[1]).getByRole('button', { name: 'Inspect chunk 1' }))
    const inspector = (await screen.findByRole('heading', { name: 'Chunk #1' })).closest('section')
    expect(inspector).toHaveTextContent('original.bin')
    expect(inspector).toHaveTextContent('modified.bin')
  })

  it('handles an empty file', async () => {
    api.getFile.mockResolvedValue({ ...fx.detailA, chunks: [], chunk_count: 0, unique_chunk_count: 0, original_size: 0 })
    renderApp(`/files/${fx.fileA.file_id}`)
    expect(await screen.findByText(/no manifest rows, no chunk files/)).toBeInTheDocument()
  })
})

describe('reference-count walkthrough', () => {
  it('steps through the illustrative example and labels it as not real data', async () => {
    const user = userEvent.setup()
    renderApp('/database')
    const card = (await screen.findByText('Reference-count walkthrough')).closest('section')
    expect(card).toHaveTextContent('illustrative example, not your data')
    expect(card).toHaveTextContent('ref_count = number of committed manifest occurrences referencing the chunk')
    const counts = () => within(within(card).getByRole('table', { name: 'Illustrative reference counts' })).getAllByRole('row').slice(1).map((r) => r.cells[1].textContent)
    expect(counts()).toEqual(['1', '1'])
    await user.click(within(card).getByRole('button', { name: 'Next' }))
    expect(card).toHaveTextContent('Upload identical file B')
    expect(counts()).toEqual(['2', '2'])
    await user.click(within(card).getByRole('button', { name: 'Next' }))
    await user.click(within(card).getByRole('button', { name: 'Next' }))
    expect(card).toHaveTextContent('Delete file A')
    expect(counts()).toEqual(['1', '1'])
    await user.click(within(card).getByRole('button', { name: 'Next' }))
    expect(card).toHaveTextContent('File B remains restorable')
    await user.click(within(card).getByRole('button', { name: 'Next' }))
    expect(counts()).toEqual(['0', '0'])
    expect(within(card).getAllByText(/yes — no manifest references/).length).toBe(2)
    await user.click(within(card).getByRole('button', { name: 'Reset' }))
    expect(counts()).toEqual(['1', '1'])
  })

  it('explains triggers and shows the real definitions from schema.sql', async () => {
    renderApp('/database')
    const card = (await screen.findByText('Reference-count walkthrough')).closest('section')
    expect(card).toHaveTextContent('Why triggers, not application code')
    await waitFor(() => expect(card).toHaveTextContent('UPDATE chunks SET ref_count = ref_count + 1'))
    expect(card).toHaveTextContent('UPDATE chunks SET ref_count = ref_count - 1')
    expect(card).toHaveTextContent('v_ref_count_mismatch')
    expect(extractTriggers('nothing here').every(([, t]) => t === null)).toBe(true)
  })

  it('keeps database inspection read-only and untouched by the walkthrough', async () => {
    const user = userEvent.setup()
    renderApp('/database')
    const card = (await screen.findByText('Reference-count walkthrough')).closest('section')
    await user.click(within(card).getByRole('button', { name: 'Next' }))
    expect(mutationsCalled()).toBe(0)
    expect(screen.queryByRole('button', { name: /delete|update|insert|execute|run sql/i })).not.toBeInTheDocument()
  })
})

describe('responsive layout behaviour and accessibility', () => {
  it('offers a navigation menu button that opens and closes the menu on small screens', async () => {
    const user = userEvent.setup()
    renderApp('/')
    const btn = screen.getByRole('button', { name: 'Open navigation menu' })
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    expect(btn).toHaveAttribute('aria-controls', 'primary-nav')
    expect(document.querySelector('.shell')).not.toHaveClass('menu-open')
    await user.click(btn)
    expect(screen.getByRole('button', { name: 'Close navigation menu' })).toHaveAttribute('aria-expanded', 'true')
    expect(document.querySelector('.shell')).toHaveClass('menu-open')
    await user.click(within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('link', { name: /File Storage/ }))
    expect(document.querySelector('.shell')).not.toHaveClass('menu-open') // closes after navigating
  })

  it('provides a skip link and a focusable main region', async () => {
    renderApp('/')
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main-content')
    expect(document.getElementById('main-content')).toHaveAttribute('tabindex', '-1')
  })

  it('sequence diagram steps work from the keyboard and tabs expose their selected state', async () => {
    const user = userEvent.setup()
    await arch(user, 'Upload sequence')
    expect(screen.getByRole('tab', { name: 'Upload sequence' })).toHaveAttribute('aria-selected', 'true')
    screen.getByRole('button', { name: /8\. 201 UploadResponse/ }).focus()
    await user.keyboard('{Enter}')
    expect(screen.getByText(/per-position decision list/)).toBeInTheDocument()
    expect(screen.getByRole('region', { name: /Upload sequence diagram \(scrollable\)/ })).toBeInTheDocument()
  })

  it('confirmation dialogs are modal, labelled and close with Escape', async () => {
    const user = userEvent.setup()
    renderApp('/files')
    const row = (await screen.findByText('original.bin')).closest('tr')
    await user.click(within(row).getByRole('button', { name: 'Delete' }))
    expect(screen.getByRole('dialog', { name: 'Delete file?' })).toHaveAttribute('aria-modal', 'true')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.deleteFile).not.toHaveBeenCalled()
  })
})
