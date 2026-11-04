import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../services/api'
import * as fx from './fixtures'
import { renderApp } from './helpers'
import { setDefaults } from './mockApi'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual('../services/api')
  const { mockModule } = await import('./mockApi')
  return mockModule(actual)
})

beforeEach(() => setDefaults(api))

describe('database explorer', () => {
  it('shows tables, key indicators and the live invariant', async () => {
    renderApp('/database')
    expect(await screen.findByRole('button', { name: 'Table files' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Table file_chunks' })).toBeInTheDocument()
    expect(screen.getByText(/Holds for all 3 chunk/)).toBeInTheDocument()
    expect(screen.getByText(/COUNT\(committed manifest occurrences/)).toBeInTheDocument()
    expect(await screen.findByText('original.bin')).toBeInTheDocument()
  })

  it('marks primary and foreign keys when browsing file_chunks', async () => {
    const user = userEvent.setup()
    api.inspectRows.mockResolvedValue({
      table: 'file_chunks', columns: ['file_id', 'chunk_index', 'chunk_hash'], total: 1, limit: 15, offset: 0,
      rows: [{ file_id: fx.fileA.file_id, chunk_index: 0, chunk_hash: 'c'.repeat(64) }],
    })
    renderApp('/database')
    await screen.findByRole('button', { name: 'Table files' })
    await user.click(screen.getByRole('tab', { name: 'file_chunks' }))
    expect(await screen.findByText('FK→files.file_id')).toBeInTheDocument()
    expect(screen.getByText('FK→chunks.hash')).toBeInTheDocument()
    expect(screen.getAllByText(/^PK[12]$/).map((n) => n.textContent)).toEqual(['PK1', 'PK2'])
    expect(api.inspectRows).toHaveBeenLastCalledWith('file_chunks', { limit: 15, offset: 0 }, expect.anything())
  })

  it('follows relationships: file -> its chunks -> a chunk -> the files that reference it', async () => {
    const user = userEvent.setup()
    renderApp('/database')
    const cell = await screen.findByText('original.bin')
    await user.click(cell.closest('tr'))
    expect(await screen.findByText(/its ordered chunk references/)).toBeInTheDocument()
    const nav = screen.getByText('Relationship navigation').closest('section')
    await user.click(within(nav).getByRole('button', { name: 'd'.repeat(12) + '…' }))
    expect(await screen.findByText(/files that reference it/)).toBeInTheDocument()
    expect(within(nav).getByText('invariant holds')).toBeInTheDocument()
    await user.click(within(nav).getByRole('button', { name: 'modified.bin' }))
    expect(await screen.findByText(/its ordered chunk references/)).toBeInTheDocument()
    expect(api.getFile).toHaveBeenCalledWith(fx.fileB.file_id, expect.anything())
  })

  it('shows record details with column descriptions and an empty state', async () => {
    const user = userEvent.setup()
    renderApp('/database')
    await user.click((await screen.findByText('original.bin')).closest('tr'))
    const detail = screen.getByText('Record detail').closest('section')
    expect(detail).toHaveTextContent('filename description')
    api.inspectRows.mockResolvedValue({ table: 'operations', columns: ['operation_id'], total: 0, limit: 15, offset: 0, rows: [] })
    await user.click(screen.getByRole('tab', { name: 'operations' }))
    expect(await screen.findByText(/operations table is empty/)).toBeInTheDocument()
  })

  it('flags an invariant violation reported by the backend', async () => {
    api.inspectSchema.mockResolvedValue({ ...fx.schema, ref_count_invariant: { statement: 's', chunks_checked: 3, mismatches: 2 } })
    renderApp('/database')
    expect(await screen.findByText(/2 of 3 chunk\(s\) violate it/)).toBeInTheDocument()
  })
})

describe('system architecture', () => {
  it('selecting a component shows its responsibility, interfaces and failure modes', async () => {
    const user = userEvent.setup()
    renderApp('/architecture')
    expect(await screen.findByText(/Select a component/, { selector: 'p.empty' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Deduplication manager' }))
    const d = screen.getByRole('heading', { name: 'Deduplication manager' }).closest('.details')
    expect(d).toHaveTextContent('NEW / DUPLICATE / ADOPT')
    expect(d).toHaveTextContent('chunk_unavailable')
    expect(d).toHaveTextContent('backend/app/deduplication/manager.py')
    await user.click(screen.getByRole('button', { name: 'FastAPI request layer' }))
    expect(screen.getByRole('heading', { name: 'FastAPI request layer' }).closest('.details')).toHaveTextContent('POST /api/files')
  })

  it('walks the upload and restore sequences step by step', async () => {
    const user = userEvent.setup()
    renderApp('/architecture')
    await user.click(await screen.findByRole('tab', { name: 'Upload sequence' }))
    await user.click(screen.getByRole('button', { name: /6\. stage, fsync, atomic publish/ }))
    expect(screen.getByText(/atomic rename to chunks\/ab/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /T2: chunks, manifest/ }))
    expect(screen.getByText(/One transaction inserts/)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Restore sequence' }))
    await user.click(screen.getByRole('button', { name: /6\. whole-file size/ }))
    expect(screen.getByText(/compared with the size and SHA-256 recorded at upload/)).toBeInTheDocument()
  })

  it('filters the data-flow diagram by data type', async () => {
    const user = userEvent.setup()
    renderApp('/architecture')
    await user.click(await screen.findByRole('tab', { name: 'Data flow' }))
    expect(screen.getByText('bytes of NEW chunks only')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: /File \/ chunk bytes/ }))
    expect(screen.queryByText('bytes of NEW chunks only')).not.toBeInTheDocument()
    expect(screen.getByText('chunk digest (identity)')).toBeInTheDocument()
  })

  it('explains the architecture concepts', async () => {
    const user = userEvent.setup()
    renderApp('/architecture')
    await user.click(await screen.findByRole('tab', { name: 'Concepts' }))
    for (const t of ['Client-server architecture', 'Content-addressed storage', 'Metadata versus payload storage', 'Transaction boundaries', 'Failure recovery']) {
      expect(screen.getByText(t)).toBeInTheDocument()
    }
  })
})

describe('reliability & consistency', () => {
  it('runs the read-only audit and reports a healthy store', async () => {
    const user = userEvent.setup()
    renderApp('/reliability')
    await user.click(await screen.findByRole('button', { name: 'Run integrity audit' }))
    expect(await screen.findByText('No inconsistencies detected.')).toBeInTheDocument()
    expect(api.audit).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Reconciliation (explicit repair)').closest('section')).toHaveTextContent(/found nothing reconciliation would change/)
  })

  it('shows each kind of finding with its meaning', async () => {
    const user = userEvent.setup()
    api.audit.mockResolvedValue(fx.damagedAudit)
    renderApp('/reliability')
    await user.click(await screen.findByRole('button', { name: 'Run integrity audit' }))
    expect(await screen.findByRole('heading', { name: /Corrupted chunks/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Stale staging files/ })).toBeInTheDocument()
    expect(screen.getByText(/no longer match their SHA-256/)).toBeInTheDocument()
    expect(screen.getByText(/upload-1\.part/)).toBeInTheDocument()
  })

  it('requires confirmation, previews actions and reports what reconcile did', async () => {
    const user = userEvent.setup()
    api.audit.mockResolvedValue(fx.damagedAudit)
    api.reconcile.mockResolvedValue({
      actions: [{ action: 'delete_staging_file', subject: 'upload-1.part', detail: 'removed leftover staging file' }],
      remaining: { ...fx.damagedAudit, findings: [fx.damagedAudit.findings[0]], counts_by_kind: { corrupt_chunk: 1 } },
    })
    renderApp('/reliability')
    await user.click(await screen.findByRole('button', { name: 'Run integrity audit' }))
    await user.click(await screen.findByRole('button', { name: /Review and reconcile/ }))
    const dialog = screen.getByRole('dialog', { name: 'Run reconciliation?' })
    expect(dialog).toHaveTextContent('Delete the staging file')
    expect(dialog).not.toHaveTextContent('corrupt') // corrupt chunks are never touched
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(api.reconcile).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: /Review and reconcile/ }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Run reconciliation' }))
    expect(await screen.findByText(/Reconciliation finished: 1 action/)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('heading', { name: /Stale staging files/ })).not.toBeInTheDocument())
  })

  it('surfaces an audit failure safely', async () => {
    const user = userEvent.setup()
    api.audit.mockRejectedValue(Object.assign(new Error('The server reported an internal error.'), { code: 'internal_error' }))
    renderApp('/reliability')
    await user.click(await screen.findByRole('button', { name: 'Run integrity audit' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The server reported an internal error.')
  })

  it('does not offer fault injection', async () => {
    renderApp('/reliability')
    expect(await screen.findByText(/Controlled fault-injection experiments are planned/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /inject|corrupt|delete chunk/i })).not.toBeInTheDocument()
  })
})

describe('planned and educational pages', () => {
  it('performance page claims no measurements', async () => {
    renderApp('/performance')
    expect(await screen.findByText(/No benchmark has been run/)).toBeInTheDocument()
    expect(screen.getAllByText(/Not implemented/).length).toBeGreaterThan(0)
    expect(document.body.textContent).not.toMatch(/\d+(\.\d+)?\s*(MiB\/s|MB\/s|ms\b)/)
    expect(await screen.findByText('/api/benchmarks')).toBeInTheDocument()
  })

  it('simulation page is labelled hypothetical and not implemented', async () => {
    renderApp('/simulation')
    expect((await screen.findAllByText(/hypothetical workload model/)).length).toBeGreaterThan(0)
    expect(screen.getByText(/never be presented as/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /run|simulate/i })).not.toBeInTheDocument()
  })

  it('trade-offs page covers each comparison and keeps distributed designs conceptual', async () => {
    renderApp('/tradeoffs')
    for (const t of ['Content-defined vs fixed-size chunking', 'SQLite vs distributed metadata storage', 'Local filesystem vs object storage',
      'Single-process vs distributed architecture', 'Storage savings vs CPU overhead', 'Redundancy vs metadata complexity']) {
      expect(await screen.findByRole('heading', { name: t })).toBeInTheDocument()
    }
    expect(screen.getAllByText('Advantages').length).toBe(6)
    expect(screen.getAllByText('In a hypothetical scaled architecture').length).toBe(6)
    expect(screen.getByText(/conceptually only/)).toBeInTheDocument()
  })

  it('documentation page renders the repository docs', async () => {
    const user = userEvent.setup()
    renderApp('/docs')
    expect(await screen.findByRole('heading', { name: 'Functional requirements' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Security & scalability limitations' }))
    expect(await screen.findByRole('heading', { name: 'Scalability limitations (stated, not solved)' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Database schema' }))
    expect(await screen.findByText(/Reference-count invariant/, { selector: 'h2' })).toBeInTheDocument()
  })
})
