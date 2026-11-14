// Phase D1: the cloud-mode user interface (banner, in-memory token entry, hidden administrative features) and its local-mode counterpart.
// Page-level API calls are mocked; no backend is contacted.
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { clearToken, getToken, hasToken } from '../services/session'
import { setDefaults } from './mockApi'

const BACKEND = 'https://chunkvault-api.example.test'
const SECRET = 'demo-secret-token-0123456789abcdef'

function cloud() {
  vi.stubEnv('VITE_CHUNKVAULT_MODE', 'cloud')
  vi.stubEnv('VITE_API_BASE_URL', BACKEND)
}

beforeEach(() => clearToken())
afterEach(() => {
  vi.unstubAllEnvs()
  clearToken()
})

vi.mock('../services/api', async () => {
  const actual = await vi.importActual('../services/api')
  const { mockModule } = await import('./mockApi')
  const m = mockModule(actual)
  // keep the real request/error plumbing for the tests above; replace only the page-level calls
  return { ...m, request: actual.request, ApiError: actual.ApiError, getHealth: actual.getHealth, friendlyMessage: actual.friendlyMessage }
})

describe('cloud UI', () => {
  const view = async (route = '/') => {
    const { api } = await import('../services/api')
    setDefaults(api)
    return render(<MemoryRouter initialEntries={[route]}><App /></MemoryRouter>)
  }

  it('shows the synthetic-data banner and the token form in cloud mode', async () => {
    cloud()
    await view('/files')
    const banner = screen.getByRole('note', { name: 'Public demo notice' })
    expect(banner).toHaveTextContent(/synthetic, non-sensitive data only/i)
    const field = screen.getByLabelText('Demo token')
    expect(field).toHaveAttribute('type', 'password')
    expect(field).toHaveAttribute('autocomplete', 'off')
  })

  it('local mode shows no banner and no token form', async () => {
    await view('/files')
    expect(screen.queryByRole('note', { name: 'Public demo notice' })).toBeNull()
    expect(screen.queryByLabelText('Demo token')).toBeNull()
  })

  it('entering the token stores it in memory only, clears the field and offers to forget it', async () => {
    cloud()
    const user = userEvent.setup()
    await view('/files')
    await user.type(screen.getByLabelText('Demo token'), SECRET)
    expect(screen.getByRole('button', { name: 'Use token' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Use token' }))
    expect(getToken()).toBe(SECRET)
    expect(screen.queryByLabelText('Demo token')).toBeNull()
    expect(document.body.innerHTML).not.toContain(SECRET) // the value is nowhere in the page
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain(SECRET)
    await user.click(screen.getByRole('button', { name: 'Forget token' }))
    expect(hasToken()).toBe(false)
    expect(screen.getByLabelText('Demo token')).toBeInTheDocument()
  })

  it('the submit button stays disabled for an empty or blank token', async () => {
    cloud()
    const user = userEvent.setup()
    await view('/files')
    expect(screen.getByRole('button', { name: 'Use token' })).toBeDisabled()
    await user.type(screen.getByLabelText('Demo token'), '   ')
    expect(screen.getByRole('button', { name: 'Use token' })).toBeDisabled()
  })

  it.each([
    ['/reliability', /Integrity audit and reconciliation is not available in the public demo/],
    ['/architecture?tab=faults', null],
  ])('administrative controls are hidden on %s', async (route, text) => {
    cloud()
    await view(route.split('?')[0])
    if (text) expect(await screen.findByText(text)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Run integrity audit/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /reconcile/i })).toBeNull()
    expect(screen.queryByLabelText(/admin/i)).toBeNull()
  })

  it('local mode keeps the reliability controls', async () => {
    await view('/reliability')
    expect(await screen.findByRole('button', { name: /Run integrity audit/i })).toBeInTheDocument()
  })

  it('no admin token field exists anywhere in the interface', async () => {
    cloud()
    await view('/reliability')
    expect(document.querySelector('input[name*="admin" i], input[id*="admin" i]')).toBeNull()
    expect(within(document.body).queryByText(/admin token/i)).toBeNull()
  })
})

describe('administrative components in cloud mode', () => {
  it('the fault lab and the benchmark runner render only a notice and make no admin request', async () => {
    cloud()
    const { api } = await import('../services/api')
    setDefaults(api)
    const FaultLab = (await import('../pages/architecture/FaultLab')).default
    const Runner = (await import('../pages/perf/Runner')).default
    for (const C of [FaultLab, Runner]) {
      const { unmount } = render(<MemoryRouter><C /></MemoryRouter>)
      expect(screen.getByText(/is not available in the public demo/)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /run|start/i })).toBeNull()
      unmount()
    }
    for (const f of [api.runExperiment, api.startBenchmark, api.cancelBenchmark, api.audit, api.reconcile]) expect(f).not.toHaveBeenCalled()
  })

  it('in local mode the runner still offers its controls', async () => {
    const { api } = await import('../services/api')
    setDefaults(api)
    const Runner = (await import('../pages/perf/Runner')).default
    render(<MemoryRouter><Runner /></MemoryRouter>)
    expect(await screen.findByRole('button', { name: 'Start benchmark' })).toBeInTheDocument()
  })
})
