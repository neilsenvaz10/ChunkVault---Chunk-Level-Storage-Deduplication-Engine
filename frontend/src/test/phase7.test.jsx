import { render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmDialog, EmptyState, Hash, Notice, SourceChip } from '../components/ui'
import { api, uploadFile, downloadFile } from '../services/api'
import { NAV_GROUPS, MODULES } from '../utils/navigation'
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

describe('application shell and navigation (phase 7)', () => {
  it('groups the unchanged routes into four sections', () => {
    expect(NAV_GROUPS.map((g) => g.title)).toEqual(['Overview', 'System Design', 'Engineering', 'Resources'])
    expect(NAV_GROUPS.flatMap((g) => g.items.map((m) => m.path)).sort()).toEqual(MODULES.map((m) => m.path).sort())
    expect(MODULES.map((m) => m.path).sort()).toEqual(
      ['/', '/architecture', '/database', '/docs', '/files', '/performance', '/reliability', '/simulation', '/tradeoffs'])
  })

  it('shows the section and page in the top bar and marks the active link', async () => {
    renderApp('/database')
    const bar = await screen.findByLabelText('Current section')
    expect(bar).toHaveTextContent('System Design')
    expect(bar).toHaveTextContent('Database Explorer')
    const nav = screen.getByRole('navigation', { name: 'Primary' })
    expect(within(nav).getByRole('link', { name: /Database Explorer/ })).toHaveClass('active')
    expect(within(nav).getByRole('link', { name: /Dashboard/ })).not.toHaveClass('active')
  })

  it('keeps icons decorative and the prototype status truthful', async () => {
    renderApp('/')
    const nav = screen.getByRole('navigation', { name: 'Primary' })
    nav.querySelectorAll('svg').forEach((svg) => expect(svg).toHaveAttribute('aria-hidden', 'true'))
    expect(await screen.findByText(/Prototype · single machine · sequential writes/)).toBeInTheDocument()
  })

  it('opens and closes the small-screen menu', async () => {
    const user = userEvent.setup()
    renderApp('/')
    const btn = screen.getByRole('button', { name: 'Open navigation menu' })
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    await user.click(btn)
    expect(screen.getByRole('button', { name: 'Close navigation menu' })).toHaveAttribute('aria-expanded', 'true')
    await user.click(screen.getByRole('button', { name: 'Close navigation menu' }))
    expect(screen.getByRole('button', { name: 'Open navigation menu' })).toBeInTheDocument()
  })
})

describe('dashboard (phase 7)', () => {
  it('offers the five quick actions as links to existing routes', async () => {
    renderApp('/')
    const qa = await screen.findByRole('navigation', { name: 'Quick actions' })
    const hrefs = within(qa).getAllByRole('link').map((a) => [a.querySelector('strong').textContent.trim(), a.getAttribute('href')])
    expect(hrefs).toEqual([
      ['Upload a file', '/files'], ['Explore chunks', '/database'], ['Run integrity audit', '/reliability'],
      ['View architecture', '/architecture'], ['Run benchmark', '/performance'],
    ])
  })

  it('never shows an unrun audit as healthy and labels where numbers come from', async () => {
    renderApp('/')
    const label = await screen.findByText('Integrity')
    expect(label.parentElement).toHaveTextContent('not audited')
    expect(label.parentElement).not.toHaveTextContent(/healthy/i)
    expect(screen.getByText('Where the numbers in ChunkVault come from')).toBeInTheDocument()
    expect(screen.getAllByText('Measured on this machine').length).toBe(2)
    expect(screen.getByText('Mathematical model')).toBeInTheDocument()
  })
})

describe('file storage upload zone (phase 7)', () => {
  it('shows the drop instruction, the server limit and a disabled Upload button before a file is chosen', async () => {
    renderApp('/files')
    expect(await screen.findByText('Drag and drop a file here')).toBeInTheDocument()
    expect(await screen.findByText(/Server limit: 64\.0 MiB/)).toBeInTheDocument()
    expect(screen.getByText('Browse files')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload' })).toBeDisabled()
  })

  it('shows the selected file, lets the user change or remove it, and uploads only on request', async () => {
    const user = userEvent.setup()
    renderApp('/files')
    await screen.findByText('Drag and drop a file here')
    await user.upload(screen.getByLabelText('Choose file'), new File(['abcdef'], 'notes.txt'))
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
    expect(screen.getByText(/6 B \(6 bytes\)/)).toBeInTheDocument()
    expect(screen.getByText('Change file')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload' })).toBeEnabled()
    expect(uploadFile).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Remove' }))
    expect(screen.queryByText('notes.txt')).not.toBeInTheDocument()
    expect(screen.getByText('Drag and drop a file here')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload' })).toBeDisabled()
  })

  it('accepts a dropped file', async () => {
    renderApp('/files')
    const zone = (await screen.findByText('Drag and drop a file here')).closest('.dropzone')
    const file = new File(['xyz'], 'dropped.bin')
    const { fireEvent } = await import('@testing-library/react')
    fireEvent.drop(zone, { dataTransfer: { files: [file] } })
    expect(await screen.findByText('dropped.bin')).toBeInTheDocument()
  })

  it('shows an upload failure as an alert and keeps the file selected for a retry', async () => {
    const user = userEvent.setup()
    uploadFile.mockRejectedValue(Object.assign(new Error('The upload was rejected.'), { code: 'upload_failed' }))
    renderApp('/files')
    await screen.findByText('Drag and drop a file here')
    await user.upload(screen.getByLabelText('Choose file'), new File(['abc'], 'x.bin'))
    await user.click(screen.getByRole('button', { name: 'Upload' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The upload was rejected.')
    expect(screen.getByText('x.bin')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload' })).toBeEnabled()
  })

  it('shows an explanatory empty state when no files are stored', async () => {
    api.listFiles.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 })
    renderApp('/files')
    expect(await screen.findByText(/No files stored yet/)).toBeInTheDocument()
    expect(screen.getByText(/split it into content-defined chunks/)).toBeInTheDocument()
  })

  it('keeps Delete behind a confirmation and never deletes before it', async () => {
    const user = userEvent.setup()
    renderApp('/files')
    await screen.findByText('original.bin')
    await user.click(screen.getAllByRole('button', { name: 'Delete' })[0])
    expect(screen.getByRole('dialog', { name: 'Delete file?' })).toBeInTheDocument()
    expect(api.deleteFile).not.toHaveBeenCalled()
  })
})

describe('shared components (phase 7)', () => {
  it('moves focus to Cancel in a destructive dialog, traps Tab, closes on Escape and restores focus', async () => {
    const user = userEvent.setup()
    const onCancel = vi.fn()
    const Host = () => {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button onClick={() => setOpen(true)}>open</button>
          {open && <ConfirmDialog title="Delete?" danger confirmLabel="Delete it" onConfirm={() => {}} onCancel={() => { onCancel(); setOpen(false) }}>body</ConfirmDialog>}
        </>
      )
    }
    render(<Host />)
    await user.click(screen.getByRole('button', { name: 'open' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete?' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus() // not the destructive button
    await user.tab()
    expect(screen.getByRole('button', { name: 'Delete it' })).toHaveFocus()
    await user.tab() // wraps instead of leaving the dialog
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await user.tab({ shift: true })
    expect(screen.getByRole('button', { name: 'Delete it' })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalled()
    await waitFor(() => expect(screen.getByRole('button', { name: 'open' })).toHaveFocus())
  })

  it('disables both buttons while busy', () => {
    render(<ConfirmDialog title="T" busy confirmLabel="Go" onConfirm={() => {}} onCancel={() => {}}>x</ConfirmDialog>)
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Working…' })).toBeDisabled()
  })

  it('announces errors with role=alert and information with role=status', () => {
    render(<><Notice tone="error" title="Bad">e</Notice><Notice tone="warn" title="Careful">w</Notice></>)
    expect(screen.getByRole('alert')).toHaveTextContent('Bad')
    expect(screen.getByRole('status')).toHaveTextContent('Careful')
  })

  it('renders an empty state with its action and labelled data sources', () => {
    render(<><EmptyState title="Nothing here" action={<button>Do it</button>}>Because.</EmptyState><SourceChip kind="model" /></>)
    expect(screen.getByText('Nothing here')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Do it' })).toBeInTheDocument()
    expect(screen.getByText('Mathematical model')).toBeInTheDocument()
  })

  it('copies the full hash while showing a shortened one', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const full = 'ab'.repeat(32)
    render(<Hash value={full} length={8} />)
    expect(screen.getByText(/^abababab/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: `Copy ${full}` }))
    expect(writeText).toHaveBeenCalledWith(full)
    expect(await screen.findByText('copied')).toBeInTheDocument()
  })
})

describe('architecture page (phase 7)', () => {
  it('keeps every section reachable from wrapped tabs', async () => {
    const user = userEvent.setup()
    renderApp('/architecture')
    const tabs = await screen.findByRole('tablist', { name: 'Architecture views' })
    for (const name of ['Components', 'Upload lifecycle', 'Restore lifecycle', 'Chunking (CDC)', 'Consistency protocol', 'Fault injection & recovery', 'API explorer']) {
      const tab = within(tabs).getByRole('tab', { name })
      await user.click(tab)
      expect(tab).toHaveAttribute('aria-selected', 'true')
    }
  })

  it('shows a structured detail panel for the selected component and highlights its relations', async () => {
    const user = userEvent.setup()
    renderApp('/architecture')
    await user.click(await screen.findByRole('button', { name: 'Deduplication manager' }))
    const panel = screen.getByText('Deduplication manager', { selector: 'h3' }).closest('.details')
    for (const term of ['Purpose', 'Inputs', 'Outputs', 'Depends on', 'Used by', 'Python modules', 'API endpoints', 'Failure scenarios', 'Design rationale']) {
      expect(within(panel).getByText(term)).toBeInTheDocument()
    }
    expect(document.querySelector('[aria-label="Deduplication manager"]').getAttribute('aria-pressed')).toBe('true')
    expect(document.querySelectorAll('[data-relation="uses"]').length).toBeGreaterThan(0)
  })

  it('separates the layers without adding components', async () => {
    renderApp('/architecture')
    await screen.findByRole('button', { name: 'Deduplication manager' })
    const bands = [...document.querySelectorAll('.layer-band text')].map((t) => t.textContent)
    expect(bands).toEqual(['FRONTEND', 'API', 'PROCESSING', 'PERSISTENCE'])
    expect(document.querySelectorAll('.node').length).toBe(11)
  })

  it('zooms the diagram within its limits', async () => {
    const user = userEvent.setup()
    renderApp('/architecture')
    const group = await screen.findByRole('group', { name: 'Component diagram zoom' })
    expect(within(group).getByText('100%')).toBeInTheDocument()
    await user.click(within(group).getByRole('button', { name: 'Zoom in' }))
    expect(within(group).getByText('120%')).toBeInTheDocument()
    await user.click(within(group).getByRole('button', { name: 'Reset zoom' }))
    expect(within(group).getByText('100%')).toBeInTheDocument()
    for (let i = 0; i < 4; i += 1) await user.click(within(group).getByRole('button', { name: 'Zoom out' }))
    expect(within(group).getByRole('button', { name: 'Zoom out' })).toBeDisabled()
  })
})

describe('reliability page (phase 7)', () => {
  it('keeps the read-only audit and the modifying reconciliation visibly separate', async () => {
    renderApp('/reliability')
    expect(await screen.findByText('Inspect · read-only')).toBeInTheDocument()
    expect(screen.getByText('Repair · needs your confirmation')).toBeInTheDocument()
    expect(screen.getByText('changes nothing')).toBeInTheDocument()
    expect(screen.getByText('modifies storage after confirmation')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Recovery dashboard' })).toHaveTextContent('Verification statusnot audited')
  })

  it('says no changes were required when a reconciliation performs no actions', async () => {
    const user = userEvent.setup()
    api.audit.mockResolvedValue(fx.damagedAudit)
    api.reconcile.mockResolvedValue({ actions: [], remaining: { ...fx.damagedAudit, findings: [], healthy: true, counts_by_kind: {} } })
    renderApp('/reliability')
    await user.click(await screen.findByRole('button', { name: 'Run integrity audit' }))
    await user.click(await screen.findByRole('button', { name: /Review and reconcile/ }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Run reconciliation' }))
    expect(await screen.findByText(/No changes were required: storage was not modified/)).toBeInTheDocument()
    const dash = screen.getByRole('group', { name: 'Recovery dashboard' })
    await waitFor(() => expect(dash).toHaveTextContent('changed nothing'))
  })
})

describe('capacity simulation (phase 7)', () => {
  it('always shows the model disclaimer and groups assumptions into Dataset, Metadata and Growth', async () => {
    renderApp('/simulation')
    expect(await screen.findByRole('note')).toHaveTextContent('Mathematical model — not a measurement or load test.')
    const form = screen.getByRole('form', { name: 'Simulation assumptions' })
    const groups = [...form.querySelectorAll('fieldset')].map((f) => [f.querySelector('legend').textContent, [...f.querySelectorAll('input')].length])
    expect(groups).toEqual([['Dataset', 4], ['Metadata', 3], ['Growth', 3]])
  })

  it('keeps every input labelled with its unit and puts the formulas in a disclosure', async () => {
    renderApp('/simulation')
    const input = await screen.findByLabelText('Number of files')
    expect(input).toHaveValue(100000)
    expect(screen.getByLabelText('Metadata per chunk reference (estimate)')).toBeInTheDocument()
    const summary = await screen.findByText('Formulas and assumptions used')
    expect(summary.tagName).toBe('SUMMARY')
    expect(summary.closest('details')).not.toHaveAttribute('open')
  })
})

describe('trade-offs page (phase 7)', () => {
  it('labels the chosen approach as implemented and every alternative as not implemented', async () => {
    renderApp('/tradeoffs')
    expect((await screen.findAllByText('Implemented')).length).toBe(6)
    expect(screen.getAllByText('Not implemented').length).toBe(6)
    expect(screen.getAllByText('In a hypothetical scaled architecture').length).toBe(6)
    expect(screen.getByText(/implements none of them/)).toBeInTheDocument()
  })
})

describe('documentation reader (phase 7)', () => {
  it('states truthfully that the original reports are not in the repository', async () => {
    renderApp('/docs')
    const note = await screen.findByText('Original report alignment: not established')
    expect(note.closest('.notice')).toHaveTextContent('not in the repository')
  })

  it('lists the verification documents and opens one from the navigation', async () => {
    const user = userEvent.setup()
    renderApp('/docs')
    const nav = await screen.findByRole('navigation', { name: 'Documentation' })
    for (const label of ['Final verification report', 'Requirements traceability matrix', 'Security review', 'Demonstration guide']) {
      expect(within(nav).getByRole('button', { name: label })).toBeInTheDocument()
    }
    await user.click(within(nav).getByRole('button', { name: 'Security review' }))
    expect(await screen.findByText('docs/verification/security-review.md')).toBeInTheDocument()
    expect(within(nav).getByRole('button', { name: 'Security review' })).toHaveAttribute('aria-current', 'page')
  })

  it('filters the document list and says when nothing matches', async () => {
    const user = userEvent.setup()
    renderApp('/docs')
    const nav = await screen.findByRole('navigation', { name: 'Documentation' })
    await user.type(within(nav).getByLabelText('Filter documents'), 'sequence')
    expect(within(nav).getByRole('button', { name: 'Sequence diagrams' })).toBeInTheDocument()
    expect(within(nav).queryByRole('button', { name: 'Security review' })).not.toBeInTheDocument()
    await user.clear(within(nav).getByLabelText('Filter documents'))
    await user.type(within(nav).getByLabelText('Filter documents'), 'zzzz')
    expect(within(nav).getByText(/No document matches/)).toBeInTheDocument()
  })

  it('moves between documents with previous and next', async () => {
    const user = userEvent.setup()
    renderApp('/docs')
    await screen.findByText('docs/architecture/requirements.md')
    await user.click(await screen.findByRole('button', { name: /High-level design \(HLD\) →/ }))
    expect(await screen.findByText('docs/architecture/hld.md')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /← Functional & non-functional/ })).toBeInTheDocument()
  })
})

describe('benchmark controls (phase 7)', () => {
  it('shows the resource limits beside the form and keeps the controls labelled', async () => {
    renderApp('/performance')
    const form = await screen.findByRole('form', { name: 'Benchmark parameters' })
    expect(within(form).getByLabelText('Benchmark kind')).toBeInTheDocument()
    expect(within(form).getByLabelText('File size in KiB')).toBeInTheDocument()
    expect(within(form).getByLabelText('Iterations')).toBeInTheDocument()
    expect(within(form).getByLabelText('Seed')).toBeInTheDocument()
    expect(document.querySelector('.notice-info')).toHaveTextContent(/separate worker process/)
  })
})
