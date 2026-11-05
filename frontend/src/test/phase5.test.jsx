import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Chart from '../components/Chart'
import { RecoveryDashboard, statusHint } from '../pages/ReliabilityPage'
import { toRequest, DEFAULTS } from '../pages/SimulationPage'
import { api } from '../services/api'
import { formatCompact, formatSeconds } from '../utils/format'
import * as fx from './fixtures'
import { renderApp } from './helpers'
import { setDefaults } from './mockApi'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual('../services/api')
  const { mockModule } = await import('./mockApi')
  return mockModule(actual)
})

beforeEach(() => setDefaults(api))

const lab = async (user) => {
  renderApp('/architecture')
  await user.click(await screen.findByRole('tab', { name: 'Fault injection & recovery' }))
}
const pick = async (user, title) => user.click(await screen.findByRole('radio', { name: new RegExp(title) }))

// ---------------------------------------------------------------------------------------- charts
describe('Chart', () => {
  const series = [{ name: 'Upload time', points: [{ x: 100, y: 1 }, { x: 200, y: 2.5 }, { x: 400, y: 4 }] }]

  it('plots exactly the points it is given and exposes them as a table', async () => {
    const user = userEvent.setup()
    const { container } = render(<Chart title="Size vs time" series={series} xLabel="bytes" yLabel="seconds" formatY={(v) => `${v}s`} />)
    expect(screen.getByRole('img', { name: 'Size vs time' })).toBeInTheDocument()
    expect(container.querySelectorAll('circle')).toHaveLength(3)
    expect(container.querySelector('polyline').getAttribute('points').split(' ')).toHaveLength(3)
    expect(screen.getAllByText('bytes').length).toBeGreaterThan(0)
    expect(screen.getAllByText('seconds').length).toBeGreaterThan(0)
    await user.click(screen.getByText('Show the plotted data'))
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1)
    expect(rows.map((r) => r.cells[2].textContent)).toEqual(['1s', '2.5s', '4s'])
  })

  it('draws grouped bars and a legend entry per series', () => {
    const { container } = render(<Chart title="Bars" type="bar" xLabel="case" yLabel="%"
      series={[{ name: 'CDC', points: [{ x: 'a', y: 50 }, { x: 'b', y: 0 }] }, { name: 'Fixed', points: [{ x: 'a', y: 10 }, { x: 'b', y: 20 }] }]} />)
    expect(container.querySelectorAll('rect.bar')).toHaveLength(4)
    expect(screen.getAllByText('CDC').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Fixed').length).toBeGreaterThan(0)
    const heights = [...container.querySelectorAll('rect.bar')].map((r) => Number(r.getAttribute('height')))
    expect(heights[0]).toBeGreaterThan(heights[1]) // CDC 50 > Fixed 10 in category a
    expect(heights[2]).toBe(0) // CDC 0 in category b
  })

  it('shows an empty state instead of inventing data, and ignores non-finite values', () => {
    const { rerender, container } = render(<Chart title="Empty" series={[]} xLabel="x" yLabel="y" empty="Nothing measured." />)
    expect(screen.getByText('Nothing measured.')).toBeInTheDocument()
    expect(container.querySelector('svg')).toBeNull()
    rerender(<Chart title="Bad" xLabel="x" yLabel="y" empty="Nothing measured." series={[{ name: 's', points: [{ x: 1, y: null }, { x: 2, y: NaN }, { x: 3, y: Infinity }] }]} />)
    expect(screen.getByText('Nothing measured.')).toBeInTheDocument()
  })

  it('tolerates repeated x values and repeated categories without React key warnings', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<Chart title="dups" series={[{ name: 'a', points: [{ x: 5, y: 1 }, { x: 5, y: 2 }, { x: 9, y: 3 }] }]} xLabel="x" yLabel="y" />)
    render(<Chart title="dup bars" type="bar" series={[{ name: 'a', points: [{ x: 'same', y: 1 }, { x: 'same', y: 2 }] }]} xLabel="x" yLabel="y" />)
    expect(spy.mock.calls.filter((c) => String(c[0]).includes('same key'))).toEqual([])
    spy.mockRestore()
  })

  it('scales to its container (viewBox, no fixed pixel size)', () => {
    const { container } = render(<Chart title="t" series={series} xLabel="x" yLabel="y" />)
    const svg = container.querySelector('svg')
    expect(svg.getAttribute('viewBox')).toBe('0 0 640 300')
    expect(svg.hasAttribute('width')).toBe(false)
  })
})

// ------------------------------------------------------------------------------------- fault lab
describe('fault injection and recovery lab', () => {
  it('lists the scenarios and safety rules, and has run nothing yet', async () => {
    const user = userEvent.setup()
    await lab(user)
    expect(await screen.findByText(/Only the fixed built-in scenarios/)).toBeInTheDocument()
    expect(screen.getAllByRole('radio')).toHaveLength(3)
    expect(screen.getByText('enabled')).toBeInTheDocument()
    expect(screen.queryByText(/Recovery verified by the backend/)).not.toBeInTheDocument()
    expect(api.runExperiment).not.toHaveBeenCalled()
    expect(screen.getByText(/Select a scenario to see what it does/)).toBeInTheDocument()
  })

  it('is clearly disabled by default and cannot run anything', async () => {
    const user = userEvent.setup()
    api.reliabilityScenarios.mockResolvedValue(fx.scenariosDisabled)
    await lab(user)
    expect(await screen.findByText('The lab is disabled by default.')).toBeInTheDocument()
    expect(screen.getByText(/CHUNKVAULT_ENABLE_FAULT_LAB=1/)).toBeInTheDocument()
    await pick(user, 'Failure after chunk creation')
    expect(screen.getByRole('button', { name: 'Run experiment' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Run all 3 scenarios/ })).toBeDisabled()
    expect(screen.getByText(/Not run yet: no observed result exists/)).toBeInTheDocument()
    expect(api.runExperiment).not.toHaveBeenCalled()
  })

  it('shows the selected failure point, operation and declared expectations', async () => {
    const user = userEvent.setup()
    await lab(user)
    await pick(user, 'Failure after chunk creation')
    const d = screen.getByRole('heading', { name: /Failure after chunk creation/, level: 3 }).closest('.details')
    expect(d).toHaveTextContent('Operation executed')
    expect(d).toHaveTextContent('Upload')
    expect(d).toHaveTextContent('fault point of crash_before_metadata_commit')
    expect(d).toHaveTextContent('Expected behaviour (declared before the run)')
    expect(d).toHaveTextContent('Orphan chunk files')
  })

  it('runs an experiment and shows the step timeline, expected vs observed, states, recovery and invariants', async () => {
    const user = userEvent.setup()
    await lab(user)
    await pick(user, 'Failure after chunk creation')
    await user.click(screen.getByRole('button', { name: 'Run experiment' }))
    expect(api.runExperiment).toHaveBeenCalledWith('crash_before_metadata_commit')
    const steps = within(await screen.findByRole('list', { name: 'Experiment steps' })).getAllByRole('listitem')
    expect(steps.map((s) => s.textContent.replace(/\s+/g, ' ').trim().replace(/^\d+/, ''))).toEqual([
      'Upload started', 'Chunks generated', 'Chunks persisted', 'Fault injected ⚡ fault injected', 'Recovery', 'Integrity check'])
    expect(steps[3]).toHaveAttribute('aria-current', 'step')
    expect(steps[0]).toHaveClass('fs-before_fault')
    expect(steps[4]).toHaveClass('fs-after_fault')

    const cmp = screen.getByRole('table', { name: 'Expected versus observed' })
    const rows = within(cmp).getAllByRole('row').slice(1)
    expect(rows[0]).toHaveTextContent('Orphan chunk files> 01✓ yes')
    expect(screen.getByText('observed = expected')).toBeInTheDocument()

    const sqlite = screen.getByRole('table', { name: 'SQLite state' })
    expect(sqlite).toHaveTextContent('control.bin: complete · victim.bin: uploading')
    expect(sqlite).toHaveTextContent('upload: running')
    expect(screen.getByText(/T2 never committed/)).toBeInTheDocument()
    const fsTable = screen.getByRole('table', { name: 'Filesystem state' })
    expect(fsTable).toHaveTextContent('Remaining temporary / orphan chunks1')
    expect(fsTable).toHaveTextContent('Staging files left1')
    expect(fsTable).not.toHaveTextContent('0123456789abcdef') // random names are shortened

    expect(screen.getByText('delete_orphan_chunk')).toBeInTheDocument()
    expect(screen.getByText(/second time performed/)).toHaveTextContent('0')
    const invariants = within(screen.getByRole('table', { name: 'Invariants' })).getAllByRole('row').slice(1)
    expect(invariants).toHaveLength(8)
    expect(invariants.every((r) => /holds/.test(r.textContent))).toBe(true)
    expect(screen.getByText(/Recovery verified by the backend: audit clean and all invariants hold/)).toBeInTheDocument()
    expect(screen.getByText(/temporary store \(removed afterwards: true\)/)).toBeInTheDocument()
  })

  it('reports violated invariants and never calls unrepairable damage a clean recovery', async () => {
    const user = userEvent.setup()
    await lab(user)
    await pick(user, 'Failure during file restoration')
    await user.click(screen.getByRole('button', { name: 'Run experiment' }))
    expect(await screen.findByText(/Verified with unrepairable damage/)).toBeInTheDocument()
    expect(screen.queryByText(/Recovery verified by the backend: audit clean/)).not.toBeInTheDocument()
    const inv = within(screen.getByRole('table', { name: 'Invariants' })).getAllByRole('row').slice(1)
    expect(inv[0]).toHaveTextContent('VIOLATED (reported)')
    expect(inv[1]).toHaveTextContent('detected; not repairable')
    expect(inv[4]).toHaveTextContent('holds')
    expect(screen.getByText('victim.bin: refused (chunk_corrupt)')).toBeInTheDocument()
    expect(screen.getByText('The recovery pass had nothing to do.')).toBeInTheDocument()
  })

  it('shows unmet expectations and NOT RECOVERED exactly as the backend reports them', async () => {
    const user = userEvent.setup()
    await lab(user)
    await pick(user, 'Failure during CDC')
    await user.click(screen.getByRole('button', { name: 'Run experiment' }))
    expect(await screen.findByText('1 expectation(s) not met')).toBeInTheDocument()
    const row = within(screen.getByRole('table', { name: 'Expected versus observed' })).getAllByRole('row')[1]
    expect(row).toHaveClass('row-bad')
    expect(row).toHaveTextContent('✗ no')
    expect(screen.getByText(/NOT recovered/)).toBeInTheDocument()
  })

  it('shows a request failure as an error and no result', async () => {
    const user = userEvent.setup()
    api.runExperiment.mockRejectedValue(Object.assign(new Error('The fault lab is disabled.'), { code: 'fault_lab_disabled', status: 403 }))
    await lab(user)
    await pick(user, 'Failure after chunk creation')
    await user.click(screen.getByRole('button', { name: 'Run experiment' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The fault lab is disabled.')
    expect(screen.queryByRole('table', { name: 'Invariants' })).not.toBeInTheDocument()
  })

  it('runs every scenario in turn and summarises the backend results', async () => {
    const user = userEvent.setup()
    await lab(user)
    await pick(user, 'Failure after chunk creation')
    await user.click(screen.getByRole('button', { name: 'Run all 3 scenarios' }))
    const table = await screen.findByRole('table', { name: 'All scenarios summary' })
    await waitFor(() => expect(within(table).getAllByRole('row')).toHaveLength(4))
    expect(api.runExperiment.mock.calls.map((c) => c[0])).toEqual(['crash_before_metadata_commit', 'restore_failure', 'cdc_failure'])
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows[0]).toHaveTextContent('✓')
    expect(rows[2]).toHaveTextContent('✗')
    expect(rows[2]).toHaveTextContent('not_recovered')
  })

  it('scenario choice and run are keyboard operable and the wide tables scroll', async () => {
    const user = userEvent.setup()
    await lab(user)
    screen.getAllByRole('radio')[1].focus()
    await user.keyboard('{Enter}')
    expect(screen.getAllByRole('radio')[1]).toHaveAttribute('aria-checked', 'true')
    await user.tab()
    await user.tab()
    await user.keyboard('{Enter}') // focus moved to the run button inside the details
    await screen.findByRole('table', { name: 'Expected versus observed' }).catch(() => null)
    const run = screen.getByRole('button', { name: 'Run experiment' })
    run.focus()
    await user.keyboard('{Enter}')
    expect(await screen.findByRole('table', { name: 'Expected versus observed' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Expected versus observed' }).closest('.table-wrap')).toBeTruthy()
    expect(document.querySelector('.scenarios')).toBeTruthy()
  })

  it('shows an error state when the scenario list cannot be loaded', async () => {
    const user = userEvent.setup()
    api.reliabilityScenarios.mockRejectedValue(Object.assign(new Error('The backend is unreachable.'), { code: 'network_error' }))
    await lab(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('unreachable')
  })
})

// --------------------------------------------------------------------------- recovery dashboard
describe('recovery dashboard', () => {
  it('shows placeholders until an audit has actually run', async () => {
    renderApp('/reliability')
    const dash = await screen.findByRole('group', { name: 'Recovery dashboard' })
    expect(dash).toHaveTextContent('Files checked—')
    expect(dash).toHaveTextContent('Verification statusnot audited')
    expect(dash).toHaveTextContent('Recovery actionsnone run')
    expect(screen.getByText(/nothing is shown until it has actually been run/)).toBeInTheDocument()
  })

  it('fills in every counter from the real audit and keeps detection separate from repair', async () => {
    const user = userEvent.setup()
    api.audit.mockResolvedValue({
      healthy: false, chunks_checked: 49, files_checked: 3, findings: [], counts_by_kind: { missing_chunk_file: 2, corrupt_chunk: 1, file_hash_mismatch: 1, ref_count_mismatch: 4, orphan_chunk_file: 2, unreferenced_chunk: 1 } })
    renderApp('/reliability')
    await user.click(await screen.findByRole('button', { name: 'Run integrity audit' }))
    const dash = screen.getByRole('group', { name: 'Recovery dashboard' })
    await waitFor(() => expect(dash).toHaveTextContent('Chunks checked49'))
    expect(dash).toHaveTextContent('Files checked3')
    expect(dash).toHaveTextContent('Missing chunks2')
    expect(dash).toHaveTextContent('Hash mismatches2')
    expect(dash).toHaveTextContent('Reference-count mismatches4')
    expect(dash).toHaveTextContent('Orphaned chunks3')
    expect(dash).toHaveTextContent('Verification statusfindings')
    expect(dash).toHaveTextContent('read-only audit; nothing was changed')
    expect(api.reconcile).not.toHaveBeenCalled()
  })

  it('says storage was modified only after a reconciliation that acted, never for a read-only audit', async () => {
    const user = userEvent.setup()
    api.audit.mockResolvedValue(fx.damagedAudit)
    api.reconcile.mockResolvedValue({
      actions: [{ action: 'delete_staging_file', subject: 'upload-1.part', detail: 'removed leftover staging file' }],
      remaining: { ...fx.damagedAudit, findings: [], healthy: true, counts_by_kind: {} },
    })
    renderApp('/reliability')
    await user.click(await screen.findByRole('button', { name: 'Run integrity audit' }))
    const dash = screen.getByRole('group', { name: 'Recovery dashboard' })
    await waitFor(() => expect(dash).toHaveTextContent('read-only audit; nothing was changed'))
    expect(dash).not.toHaveTextContent('modified storage')

    await user.click(await screen.findByRole('button', { name: /Review and reconcile/ }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Run reconciliation' }))
    await waitFor(() => expect(dash).toHaveTextContent('reconciliation modified storage (1 action)'))
    expect(dash).not.toHaveTextContent('nothing was changed')
    expect(dash).toHaveTextContent('Recovery actions1')

    // a fresh read-only audit afterwards is a read-only audit again
    await user.click(screen.getByRole('button', { name: 'Run integrity audit' }))
    await waitFor(() => expect(dash).toHaveTextContent('read-only audit; nothing was changed'))
    expect(dash).not.toHaveTextContent('modified storage')
  })

  it('does not claim a reconciliation changed anything when it performed no actions', () => {
    expect(statusHint('audit', null)).toBe('read-only audit; nothing was changed')
    expect(statusHint('audit', 3)).toBe('read-only audit; nothing was changed')
    expect(statusHint('reconcile', 0)).toMatch(/changed nothing/)
    expect(statusHint('reconcile', 0)).not.toMatch(/modified storage/)
    expect(statusHint('reconcile', 1)).toMatch(/modified storage \(1 action\)/)
    expect(statusHint('reconcile', 4)).toMatch(/modified storage \(4 actions\)/)
  })

  it('counts recovery actions only after an explicit reconciliation', () => {
    render(<RecoveryDashboard report={{ healthy: true, chunks_checked: 1, files_checked: 1, counts_by_kind: {} }} actions={5} />)
    expect(screen.getByRole('group', { name: 'Recovery dashboard' })).toHaveTextContent('Recovery actions5')
  })
})

// ------------------------------------------------------------------------------- performance page
const savedList = [
  { job_id: 'aaaaaaaaaaaa', kind: 'pipeline', spec: { kind: 'pipeline', workload: 'identical', size_bytes: 131072, iterations: 2, seed: 7 }, finished_at: '2026-01-01T10:00:00+00:00',
    upload_throughput_mib_s: 0.83, restore_throughput_mib_s: 6.25, dedup_ratio: 2, savings_percent: 50, total_execution_seconds: 0.68 },
  { job_id: 'bbbbbbbbbbbb', kind: 'chunking_comparison', spec: { kind: 'chunking_comparison', workload: null, size_bytes: 196608, iterations: 1, seed: 11 }, finished_at: '2026-01-01T11:00:00+00:00' },
]
const withSaved = () => {
  api.listBenchmarks.mockResolvedValue({ running: null, results: savedList })
  api.getBenchmark.mockImplementation(async (id) => ({ job_id: id, status: 'done', progress: null, error: null, result: id === 'bbbbbbbbbbbb' ? fx.comparisonResult() : fx.pipelineResult() }))
}

describe('performance & benchmarking page', () => {
  it('has no numbers and empty charts when nothing was measured', async () => {
    renderApp('/performance')
    expect(await screen.findByText(/No benchmark has been run yet/)).toBeInTheDocument()
    expect(screen.getAllByText('No saved pipeline benchmark yet.').length).toBe(4)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByText('real measurements only')).toBeInTheDocument()
    expect(api.getBenchmark).not.toHaveBeenCalled()
  })

  it('offers bounded benchmark controls from the catalog', async () => {
    renderApp('/performance')
    const form = await screen.findByRole('form', { name: 'Benchmark parameters' })
    expect(within(form).getAllByRole('option').map((o) => o.textContent)).toEqual(
      ['Upload / restore pipeline', 'CDC vs fixed-size chunking', 'small', 'identical', 'inserted bytes'])
    expect(within(form).getByLabelText('File size in KiB')).toHaveAttribute('min', '16')
    expect(within(form).getByLabelText('File size in KiB')).toHaveAttribute('max', '4096')
    expect(within(form).getByLabelText('Iterations')).toHaveAttribute('max', '10')
    expect(screen.getByText(/bounded to/)).toHaveTextContent('16.0 KiB–4.00 MiB')
    expect(screen.getByText(/never your stored files/)).toBeInTheDocument()
  })

  it('starts a benchmark with the chosen parameters, shows progress, and loads the result when it finishes', async () => {
    const user = userEvent.setup()
    api.startBenchmark.mockResolvedValue({ job_id: 'aaaaaaaaaaaa', status: 'running' })
    api.getBenchmark.mockResolvedValueOnce({ job_id: 'aaaaaaaaaaaa', status: 'running', progress: { fraction: 0.5, message: 'iteration 2 of 2: uploading and restoring' }, result: null, error: null })
    renderApp('/performance')
    const form = await screen.findByRole('form', { name: 'Benchmark parameters' })
    await user.selectOptions(within(form).getByLabelText('Workload'), 'inserted_bytes')
    await user.clear(within(form).getByLabelText('Iterations'))
    await user.type(within(form).getByLabelText('Iterations'), '2')
    await user.clear(within(form).getByLabelText('Seed'))
    await user.type(within(form).getByLabelText('Seed'), '7')
    await user.click(within(form).getByRole('button', { name: 'Start benchmark' }))
    expect(api.startBenchmark).toHaveBeenCalledWith({ kind: 'pipeline', workload: 'inserted_bytes', size_bytes: 131072, iterations: 2, seed: 7 })
    expect(await screen.findByLabelText('Benchmark progress')).toHaveProperty('value', 0) // before the first poll: nothing reported yet
    await waitFor(() => expect(screen.getByLabelText('Benchmark progress')).toHaveProperty('value', 50), { timeout: 4000 })
    expect(screen.getByText(/iteration 2 of 2/)).toBeInTheDocument()
    expect(within(form).getByRole('button', { name: 'Start benchmark' })).toBeDisabled()

    withSaved() // the next poll reports the finished result
    expect(await screen.findByText('Result overview', {}, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.getByText(/Finished\. The result is shown below/)).toBeInTheDocument()
  }, 15000)

  it('can cancel a running benchmark', async () => {
    const user = userEvent.setup()
    api.listBenchmarks.mockResolvedValue({ running: { job_id: 'aaaaaaaaaaaa', status: 'running', progress: { fraction: 0.2, message: 'warming up' }, spec: {} }, results: [] })
    api.cancelBenchmark.mockResolvedValue({ job_id: 'aaaaaaaaaaaa', status: 'cancelled' })
    renderApp('/performance')
    await user.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(api.cancelBenchmark).toHaveBeenCalledWith('aaaaaaaaaaaa')
    expect(await screen.findByText(/Benchmark cancelled; its temporary store was removed/)).toBeInTheDocument()
  })

  it('rejects an invalid request with the server message and does not start polling', async () => {
    const user = userEvent.setup()
    api.startBenchmark.mockRejectedValue(Object.assign(new Error('iterations must be between 1 and 10'), { code: 'invalid_benchmark', status: 422 }))
    renderApp('/performance')
    await user.click(await screen.findByRole('button', { name: 'Start benchmark' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('iterations must be between 1 and 10')
    expect(api.getBenchmark).not.toHaveBeenCalled()
  })

  it('shows the measured overview, details, statistics, stages and storage for the latest result', async () => {
    withSaved()
    renderApp('/performance')
    const table = await screen.findByRole('table', { name: 'Saved benchmarks' })
    expect(within(table).getAllByRole('row')).toHaveLength(3)
    const tile = (label) => [...document.querySelectorAll('.tile-label')].find((n) => n.textContent === label).parentElement
    expect(await screen.findByText('Result overview')).toBeInTheDocument()
    expect(tile('Upload throughput (median)')).toHaveTextContent('0.83 MiB/s')
    expect(tile('Upload throughput (median)')).toHaveTextContent('min 0.75')
    expect(tile('Restore throughput (median)')).toHaveTextContent('6.25 MiB/s')
    expect(tile('Deduplication ratio')).toHaveTextContent('2.00×')
    expect(tile('Storage savings')).toHaveTextContent('50.0%')
    expect(tile('Total execution time')).toHaveTextContent('680.0 ms')
    expect(tile('Restored files verified')).toHaveTextContent('all')

    const params = screen.getByRole('table', { name: 'Benchmark parameters' })
    expect(params).toHaveTextContent('identical')
    expect(params).toHaveTextContent('Dataset seed7')
    expect(params).toHaveTextContent('File size128 KiB')
    expect(params).toHaveTextContent('Iterations2')
    expect(params).toHaveTextContent('min 4.00 KiB · target 16.0 KiB · max 128 KiB · window 64 B')
    expect(params).toHaveTextContent('CPython 3.14.0 · TestOS · 8 CPUs · SQLite 3.45.0')
    const stats = within(screen.getByRole('table', { name: 'Summary statistics' })).getAllByRole('row').slice(1)
    expect(stats[0]).toHaveTextContent('Upload time')
    expect(stats[0].cells[2]).toHaveTextContent('300.0 ms') // median
    expect(stats[0].cells[3]).toHaveTextContent('270.0 ms') // min
    expect(within(screen.getByRole('table', { name: 'Individual runs' })).getAllByRole('row')).toHaveLength(3)
    const stages = within(screen.getByRole('table', { name: 'Upload stage breakdown' })).getAllByRole('row').slice(1)
    expect(stages[0]).toHaveTextContent('cdc')
    expect(stages[0]).toHaveTextContent('50.0%')
    expect(screen.getByRole('table', { name: 'Storage results' })).toHaveTextContent('Chunks generated / unique / reused16 / 8 / 8')
    expect(screen.getByText('Measurement methodology')).toBeInTheDocument()
    expect(screen.getByText(/Stage timings are exclusive/)).toBeInTheDocument()
  })

  it('renders the six chart types from real saved results only', async () => {
    withSaved()
    renderApp('/performance')
    await screen.findByText('Result overview')
    for (const name of ['Logical vs physical storage', 'Benchmark iteration comparison', 'Upload size vs upload time', 'Restore size vs restore time',
      'Chunk count vs file size', 'Logical vs physical storage (all saved runs)', 'Storage savings: CDC vs fixed-size (latest comparison)']) {
      expect(await screen.findByRole('img', { name })).toBeInTheDocument()
    }
    const upload = screen.getByRole('img', { name: 'Upload size vs upload time' }).closest('figure')
    expect(upload.querySelectorAll('circle')).toHaveLength(1) // one saved pipeline result
    await userEvent.setup().click(within(upload).getByText('Show the plotted data'))
    expect(upload).toHaveTextContent('256 KiB') // 2 uploads x 128 KiB
    expect(upload).toHaveTextContent('300.0 ms') // the measured median, not a preset
    const iter = screen.getByRole('img', { name: 'Benchmark iteration comparison' }).closest('figure')
    expect(iter.querySelectorAll('rect.bar')).toHaveLength(4) // 2 iterations x upload/restore
  })

  it('shows the CDC vs fixed-size comparison with computed verdicts and honest explanations', async () => {
    const user = userEvent.setup()
    withSaved()
    renderApp('/performance')
    await screen.findByText('Result overview')
    await user.click(within(screen.getByRole('table', { name: 'Saved benchmarks' })).getAllByRole('button', { name: 'View' })[1])
    const t = await screen.findByRole('table', { name: 'CDC versus fixed-size results' })
    const rows = within(t).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(4)
    expect(rows[0]).toHaveTextContent('Small insertion near the beginning')
    expect(rows[0]).toHaveTextContent('CDC saves more')
    expect(rows[0]).toHaveTextContent('47.0%')
    expect(rows[1]).toHaveTextContent('Fixed-size')
    expect(rows[1]).toHaveTextContent('0.0%')
    expect(rows[2]).toHaveTextContent('fixed-size saves more') // in-place edit: CDC does not win
    expect(screen.getByText(/Why CDC can win after an insertion or deletion/)).toBeInTheDocument()
    expect(screen.getByText(/Where it gives little or no advantage/)).toBeInTheDocument()
    expect(screen.getByText(/computed from the measured savings, not assumed/)).toBeInTheDocument()
  })

  it('reports a failed result load without inventing values', async () => {
    api.listBenchmarks.mockResolvedValue({ running: null, results: savedList.slice(0, 1) })
    api.getBenchmark.mockRejectedValue(Object.assign(new Error('The backend is unreachable.'), { code: 'network_error' }))
    renderApp('/performance')
    expect(await screen.findByRole('alert')).toHaveTextContent('unreachable')
    expect(screen.queryByText('Result overview')).not.toBeInTheDocument()
  })

  it('wide result tables scroll instead of overflowing', async () => {
    withSaved()
    renderApp('/performance')
    await screen.findByText('Result overview')
    for (const name of ['Saved benchmarks', 'Summary statistics', 'Individual runs']) {
      expect(screen.getByRole('table', { name }).closest('.table-wrap')).toBeTruthy()
    }
  })
})

// -------------------------------------------------------------------------------- capacity simulator
describe('capacity & scalability simulator', () => {
  it('is labelled as a mathematical model and requests the model with the default assumptions', async () => {
    renderApp('/simulation')
    expect((await screen.findAllByText(/mathematical model/)).length).toBeGreaterThan(0)
    expect(screen.getByText(/run a load test, read your stored data/)).toBeInTheDocument()
    await screen.findByText('Estimated results (model output)')
    expect(api.simulate).toHaveBeenCalledWith(toRequest(DEFAULTS), expect.anything())
    expect(api.simulate.mock.calls[0][0]).toMatchObject({ file_count: 100000, avg_file_size_bytes: 5242880, avg_chunks_per_file: null, duplicate_percent: 30 })
  })

  it('shows the outputs returned by the model', async () => {
    renderApp('/simulation')
    const tile = async (label) => (await screen.findAllByText(label)).find((n) => n.classList.contains('tile-label')).parentElement
    expect(await tile('Total logical storage')).toHaveTextContent('488 GiB') // 100,000 x 5 MiB
    expect(await tile('Estimated physical chunk storage')).toHaveTextContent('342 GiB') // x (1 - 0.30)
    expect(await tile('Estimated storage savings')).toHaveTextContent('30.0%')
    expect(await tile('Estimated storage savings')).toHaveTextContent('1.43×')
    expect(await tile('Estimated metadata overhead')).toBeInTheDocument()
    expect(await tile('Estimated total disk usage')).toBeInTheDocument()
    expect(await tile('Estimated unique chunks')).toHaveTextContent('22.40 M')
  })

  it('recomputes from the server when an assumption changes', async () => {
    const user = userEvent.setup()
    renderApp('/simulation')
    await screen.findByText('Estimated results (model output)')
    const dup = screen.getByLabelText('Duplicate-content percentage')
    await user.clear(dup)
    await user.type(dup, '50')
    await waitFor(() => expect(api.simulate).toHaveBeenLastCalledWith(expect.objectContaining({ duplicate_percent: 50 }), expect.anything()))
    await waitFor(() => expect(within((screen.getAllByText('Estimated storage savings').find((n) => n.classList.contains('tile-label'))).parentElement).getByText('50.0%')).toBeInTheDocument())
    const growth = screen.getByLabelText('Storage growth rate')
    await user.clear(growth)
    await user.type(growth, '10')
    await waitFor(() => expect(api.simulate).toHaveBeenLastCalledWith(expect.objectContaining({ duplicate_percent: 50, monthly_growth_percent: 10 }), expect.anything()))
  })

  it('shows the formulas and assumptions the model reports', async () => {
    renderApp('/simulation')
    expect(await screen.findByText('Formulas and assumptions used')).toBeInTheDocument()
    expect(screen.getByText('logical storage L = files x average file size')).toBeInTheDocument()
    expect(screen.getByText(/duplicate fraction applies to chunk references/)).toBeInTheDocument()
  })

  it('draws the five scalability charts from the projection', async () => {
    renderApp('/simulation')
    for (const name of ['File count vs storage consumption', 'Deduplication rate vs storage savings', 'File count vs metadata overhead', 'Storage growth over time',
      'Logical vs estimated physical storage']) {
      expect(await screen.findByRole('img', { name })).toBeInTheDocument()
    }
    expect(screen.getByRole('img', { name: 'Storage growth over time' }).closest('figure').querySelectorAll('circle')).toHaveLength(6) // 2 series x 3 months
  })

  it('explains architectural limitations and future options without claiming them', async () => {
    renderApp('/simulation')
    for (const t of ['SQLite write concurrency', 'Single-machine filesystem storage', 'Local disk capacity', 'Metadata database growth', 'Chunk lookup performance',
      'Hash computation overhead', 'Garbage collection and orphan cleanup', 'Backup and disaster recovery']) {
      expect(await screen.findByText(t)).toBeInTheDocument()
    }
    expect(screen.getByText(/PostgreSQL/)).toBeInTheDocument()
    expect(screen.getByText(/None of the options above is implemented/)).toBeInTheDocument()
  })

  it('handles 100% duplication, server validation errors and reset', async () => {
    const user = userEvent.setup()
    renderApp('/simulation')
    await screen.findByText('Estimated results (model output)')
    api.simulate.mockImplementation(async (b) => {
      if (b.duplicate_percent > 100) throw Object.assign(new Error('Request validation failed.'), { code: 'validation_error', status: 422 })
      return fx.simulation(b)
    })
    const dup = screen.getByLabelText('Duplicate-content percentage')
    await user.clear(dup)
    await user.type(dup, '100')
    expect(await screen.findByText(/deduplication ratio is undefined/)).toBeInTheDocument()
    await user.clear(dup)
    await user.type(dup, '150')
    expect(await screen.findByRole('alert')).toHaveTextContent('Request validation failed.')
    await user.click(screen.getByRole('button', { name: 'Reset to defaults' }))
    await waitFor(() => expect(screen.getByLabelText('Duplicate-content percentage')).toHaveValue(30))
    expect(await screen.findByText('Estimated results (model output)')).toBeInTheDocument()
  })

  it('every assumption has an accessible label', async () => {
    renderApp('/simulation')
    const form = await screen.findByRole('form', { name: 'Simulation assumptions' })
    expect(within(form).getAllByRole('spinbutton')).toHaveLength(10)
    for (const input of within(form).getAllByRole('spinbutton')) expect(input).toHaveAccessibleName()
  })

  it('converts the form to the API request', () => {
    expect(toRequest({ ...DEFAULTS, avg_file_mib: 0.5, avg_chunks_per_file: '12', retention_months: '6' })).toMatchObject({
      avg_file_size_bytes: 524288, avg_chunks_per_file: 12, retention_months: 6 })
    expect(toRequest({ ...DEFAULTS, avg_file_mib: 0 }).avg_file_size_bytes).toBe(1)
  })

  it('formats very large model outputs', () => {
    expect(formatCompact(999)).toBe('999')
    expect(formatCompact(1.5e9)).toBe('1.50 G')
    expect(formatCompact(2e18)).toBe('2.00 E')
    expect(formatSeconds(0.0003)).toBe('300 µs')
  })
})

describe('navigation after phase 5', () => {
  it('no longer marks any page as planned', async () => {
    renderApp('/')
    const nav = await screen.findByRole('navigation', { name: 'Primary' })
    expect(within(nav).queryByTitle(/Planned for phase/)).not.toBeInTheDocument()
    expect(within(nav).getAllByRole('link')).toHaveLength(9)
  })
})
