import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ComparisonView, PipelineView } from '../pages/perf/Views'
import { formatSecPerMiB } from '../utils/format'
import { comparisonResult, pipelineResult } from './fixtures5'

const stat = (m) => ({ n: 2, mean: m, median: m, min: m * 0.9, max: m * 1.1, stdev: m * 0.05 })

// A result as the current benchmark engine saves it: the three new fields are present.
const newResult = () => {
  const r = pipelineResult()
  r.conditions = { cache_state: 'unknown', cache_note: 'The OS file cache was not flushed, inspected or controlled; runs are not described as cold or warm.' }
  r.summary.upload_seconds_per_mib = stat(1.2)
  r.summary.restore_seconds_per_mib = stat(0.16)
  r.iterations.forEach((it) => {
    const footprint = it.storage.chunk_store_bytes + it.storage.database_bytes // 131072 + 73728
    Object.assign(it.storage, {
      footprint_bytes: footprint, footprint_ratio: it.storage.logical_bytes / footprint,
      footprint_savings_percent: 100 * (it.storage.logical_bytes - footprint) / it.storage.logical_bytes,
    })
  })
  return r
}

describe('benchmark reporting additions', () => {
  it('formats seconds per MiB', () => {
    expect(formatSecPerMiB(0.1234)).toBe('0.123 s/MiB')
    expect(formatSecPerMiB(2.5)).toBe('2.50 s/MiB')
    expect(formatSecPerMiB(12.34)).toBe('12.3 s/MiB')
    expect(formatSecPerMiB(null)).toBe('—')
  })

  it('shows seconds per MiB for upload and restore when the result recorded them', () => {
    render(<PipelineView r={newResult()} />)
    const table = screen.getByRole('table', { name: 'Summary statistics' })
    expect(within(table).getByText('Upload seconds per MiB').closest('tr')).toHaveTextContent('1.20 s/MiB')
    expect(within(table).getByText('Restore seconds per MiB').closest('tr')).toHaveTextContent('0.160 s/MiB')
    expect(screen.queryByText(/Seconds per MiB was not recorded/)).not.toBeInTheDocument()
  })

  it('shows the footprint ratio with its numerator, denominator and components', () => {
    render(<PipelineView r={newResult()} />)
    const t = screen.getByRole('table', { name: 'Storage results' })
    const ratioRow = within(t).getByText('On-disk footprint ratio').closest('tr')
    expect(ratioRow).toHaveTextContent('1.28×') // 262144 / (131072 + 73728)
    expect(ratioRow).toHaveTextContent('logical bytes 256 KiB ÷ footprint 200 KiB')
    const footprintRow = within(t).getByText(/On-disk footprint \(SQLite file \+ chunk files\)/).closest('tr')
    expect(footprintRow).toHaveTextContent('72.0 KiB database + 128 KiB chunk files')
    expect(within(t).getByText('Footprint savings').closest('tr')).toHaveTextContent('21.9%')
    // the payload-only figure is still reported separately
    expect(screen.getByText('Deduplication ratio')).toBeInTheDocument()
  })

  it('states the cache state exactly as the result declares it (unknown)', () => {
    render(<PipelineView r={newResult()} />)
    expect(screen.getByText('Cache state').closest('tr')).toHaveTextContent(/^Cache stateunknown — The OS file cache was not flushed/)
  })

  it('does not back-fill results saved before these fields existed', () => {
    render(<PipelineView r={pipelineResult()} />)
    expect(screen.getByText(/Seconds per MiB was not recorded in this saved result/)).toBeInTheDocument()
    expect(screen.queryByText('Upload seconds per MiB')).not.toBeInTheDocument()
    expect(screen.getByText(/not recorded in this saved result \(saved before this figure existed\)/)).toBeInTheDocument()
    expect(screen.queryByText('Footprint savings')).not.toBeInTheDocument()
    expect(screen.getByText('Cache state').closest('tr')).toHaveTextContent('not recorded in this saved result')
    expect(screen.getByText('Cache state').closest('tr')).not.toHaveTextContent('unknown')
  })

  it('adds seconds per MiB to the CDC vs fixed-size table only where it was recorded', () => {
    const r = comparisonResult()
    r.cases[0].cdc.processing_seconds_per_mib = stat(0.9)
    render(<ComparisonView r={r} />)
    const rows = within(screen.getByRole('table', { name: 'CDC versus fixed-size results' })).getAllByRole('row')
    expect(rows[0]).toHaveTextContent('Seconds per MiB (median)')
    expect(rows[1]).toHaveTextContent('0.900 s/MiB') // CDC row of the first case
    expect(rows[2]).toHaveTextContent('—') // fixed-size row: not recorded, not invented
    expect(rows[2]).not.toHaveTextContent('s/MiB')
  })
})
