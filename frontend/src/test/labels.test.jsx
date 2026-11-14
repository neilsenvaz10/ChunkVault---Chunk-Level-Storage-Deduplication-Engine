// Storage/architecture labels follow the deployment mode: cloud shows Supabase wording, local keeps the original SQLite wording.
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PipelineStrip from '../components/PipelineStrip'
import App from '../App'
import { setDefaults } from './mockApi'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual('../services/api')
  const { mockModule } = await import('./mockApi')
  const m = mockModule(actual)
  return { ...m, request: actual.request, ApiError: actual.ApiError, getHealth: actual.getHealth, friendlyMessage: actual.friendlyMessage }
})

afterEach(() => vi.unstubAllEnvs())

const cloud = () => {
  vi.stubEnv('VITE_CHUNKVAULT_MODE', 'cloud')
  vi.stubEnv('VITE_API_BASE_URL', 'https://chunkvault-api.example.test')
}

const strip = () => render(<MemoryRouter><PipelineStrip /></MemoryRouter>)

describe('pipeline storage stage', () => {
  it('local mode keeps "SQLite + Chunk Store" / "metadata + bytes"', () => {
    strip()
    expect(screen.getByText('SQLite + Chunk Store')).toBeInTheDocument()
    expect(screen.getByText('metadata + bytes')).toBeInTheDocument()
    expect(screen.queryByText(/Supabase/)).toBeNull()
  })

  it('cloud mode shows "Supabase PostgreSQL + Storage" / "metadata + chunk objects"', () => {
    cloud()
    strip()
    expect(screen.getByText('Supabase PostgreSQL + Storage')).toBeInTheDocument()
    expect(screen.getByText('metadata + chunk objects')).toBeInTheDocument()
    expect(screen.queryByText('SQLite + Chunk Store')).toBeNull()
    expect(screen.queryByText('metadata + bytes')).toBeNull()
  })

  it('the stage still links to the same architecture focus in both modes', () => {
    cloud()
    strip()
    expect(screen.getByText('Supabase PostgreSQL + Storage').closest('a')).toHaveAttribute('href', '/architecture?focus=SQLite%20%2B%20Chunk%20Store')
  })
})

describe('top bar note and dashboard wording', () => {
  const view = async () => {
    const { api } = await import('../services/api')
    setDefaults(api)
    return render(<MemoryRouter initialEntries={['/']}><App /></MemoryRouter>)
  }

  it('local mode keeps the single-machine note', async () => {
    await view()
    expect(await screen.findByText('Prototype · single machine · sequential writes')).toBeInTheDocument()
    expect(screen.getByText('SQLite + Chunk Store')).toBeInTheDocument()
  })

  it('cloud mode drops the single-machine claim and the SQLite pipeline label', async () => {
    cloud()
    await view()
    expect(await screen.findByText('Public demo · hosted backend · one writer at a time')).toBeInTheDocument()
    expect(screen.queryByText(/single machine/)).toBeNull()
    expect(screen.getByText('Supabase PostgreSQL + Storage')).toBeInTheDocument()
    expect(screen.queryByText('SQLite + Chunk Store')).toBeNull()
  })
})
