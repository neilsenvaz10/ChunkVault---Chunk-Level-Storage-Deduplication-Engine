import { useState } from 'react'
import { AsyncState, Card, ErrorNotice, MetricTile, StatusBadge } from '../../components/ui'
import { useApi } from '../../hooks/useApi'
import { api } from '../../services/api'

// Honest wording for the backend-computed verification status.
export const STATUS_TEXT = {
  verified_clean: ['ok', 'Recovery verified by the backend: audit clean and all invariants hold'],
  verified_with_unrepairable_damage: ['warn', 'Verified with unrepairable damage: leftovers cleaned, but damaged data is only detected, not repaired'],
  not_recovered: ['error', 'NOT recovered: the audit or an invariant still fails'],
}

function Timeline({ steps }) {
  return (
    <ol className="fault-steps" aria-label="Experiment steps">
      {steps.map((s, i) => (
        <li key={s.label} className={`fs-${s.phase}`} aria-current={s.phase === 'fault' ? 'step' : undefined}>
          <span className="stage-no">{i + 1}</span>
          <span>{s.label}</span>
          {s.phase === 'fault' && <strong className="fault-mark"> ⚡ fault injected</strong>}
        </li>
      ))}
    </ol>
  )
}

function transactionText(r) {
  const b = r.before_recovery
  if (r.operation === 'Upload') {
    if (b.victim_status === 'complete') return 'T2 committed: the file is visible as complete with its manifest.'
    if (b.victim_status === 'failed') return `T2 rolled back (no manifest rows visible: ${b.victim_manifest_rows}); the engine marked the file failed in a separate transaction.`
    if (b.victim_status === 'uploading') return `T2 never committed: ${b.victim_manifest_rows} manifest rows visible; T1's 'uploading' row and a 'running' operation remain.`
    return 'No metadata was written for the victim file (the failure happened before T1).'
  }
  if (r.operation === 'Delete') {
    return b.victim_visible ? 'The delete transaction rolled back: the file and its manifest are still present.' : 'The delete transaction committed: the file and its unshared chunk rows are gone.'
  }
  return 'No metadata transaction was open at the end of the experiment.'
}

function Result({ r }) {
  const [tone, statusText] = STATUS_TEXT[r.recovery_status] || ['muted', r.recovery_status]
  const b = r.before_recovery
  const a = r.after_recovery
  const unmet = r.expectations.filter((e) => !e.matches).length
  const show = (v) => (v === null || v === undefined ? '—' : String(v))
  return (
    <>
      <Card title={`Result: ${r.title}`} aside={<StatusBadge tone={r.all_expectations_met ? 'ok' : 'warn'}>{r.all_expectations_met ? 'observed = expected' : `${unmet} expectation(s) not met`}</StatusBadge>}>
        <Timeline steps={r.steps} />
        <p className="muted">
          Operation: <strong>{r.operation}</strong> · fault: {r.fault_point} ({r.fault_kind === 'crash' ? 'process death: engine cleanup does not run' : 'ordinary error: the engine handles it'}) ·{' '}
          run in {r.duration_ms} ms.
        </p>
        <div className={`notice ${tone === 'ok' ? 'notice-ok' : tone === 'error' ? 'notice-error' : ''}`} role="status">
          <strong>{statusText}</strong>
          <span>Computed by the backend from the real audit and by restoring every complete file. This label is never set by the UI.</span>
        </div>
      </Card>

      <Card title="Expected outcome vs observed outcome">
        <div className="table-wrap">
          <table aria-label="Expected versus observed">
            <thead><tr><th>Check</th><th>Expected</th><th>Observed</th><th>Match</th></tr></thead>
            <tbody>
              {r.expectations.map((e) => (
                <tr key={e.key} className={e.matches ? '' : 'row-bad'}>
                  <td>{e.label}</td><td>{e.expected}</td><td>{show(e.observed)}</td>
                  <td>{e.matches ? <span className="ok-mark">✓ yes</span> : <strong className="bad-mark">✗ no</strong>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted">&ldquo;Expected&rdquo; was declared in the scenario definition before the run; &ldquo;observed&rdquo; was read back from SQLite, the filesystem and the real auditor.</p>
      </Card>

      <div className="two-col">
        <Card title="SQLite state after the fault">
          <p>{transactionText(r)}</p>
          <table aria-label="SQLite state">
            <tbody>
              <tr><th>Files</th><td>{b.files.map((f) => `${f.filename}: ${f.status}`).join(' · ')}</td></tr>
              <tr><th>Operations</th><td>{b.operations.map((o) => `${o.type}: ${o.status}`).join(' · ') || 'none'}</td></tr>
              <tr><th>Chunk rows / manifest rows</th><td>{b.chunk_rows} / {b.manifest_rows}</td></tr>
              <tr><th>Reference-count mismatches</th><td>{b.ref_count_mismatches}</td></tr>
            </tbody>
          </table>
        </Card>
        <Card title="Filesystem state after the fault">
          <table aria-label="Filesystem state">
            <tbody>
              <tr><th>Chunk files on disk</th><td>{b.chunk_files}</td></tr>
              <tr><th>Remaining temporary / orphan chunks</th><td>{b.orphan_chunk_files}</td></tr>
              <tr><th>Missing chunk files</th><td>{b.missing_chunk_files}</td></tr>
              <tr><th>Staging files left</th><td>{b.staging_files}{b.staging_names.length > 0 && <small className="muted"> ({b.staging_names.map((n) => n.replace(/[0-9a-f]{12,}/g, '…')).join(', ')})</small>}</td></tr>
            </tbody>
          </table>
          <p className="muted">Audit findings before recovery: {Object.keys(b.audit.by_kind).length ? Object.entries(b.audit.by_kind).map(([k, v]) => `${k} ×${v}`).join(', ') : 'none'}</p>
        </Card>
      </div>

      <Card title="Recovery (explicit, reported)">
        {r.recovery_actions.length === 0 ? <p className="empty">The recovery pass had nothing to do.</p> : (
          <ul className="plain">{r.recovery_actions.map((x, i) => <li key={i}><code>{x.action}</code> {x.subject} — {x.detail}</li>)}</ul>
        )}
        <p className="muted">
          Running recovery a second time performed <strong>{r.second_recovery_actions}</strong> action(s). After recovery: {a.orphan_chunk_files} orphan chunk(s),
          {' '}{a.staging_files} staging file(s), audit {a.audit.healthy ? 'healthy' : `reports ${Object.entries(a.audit.by_kind).map(([k, v]) => `${k} ×${v}`).join(', ')}`}.
        </p>
        {r.retry && (
          <p className="notice">
            <strong>Client retry (response was lost):</strong> {r.retry.visible_victim_files} file records named victim.bin now exist; the retry wrote {r.retry.new_chunks} new chunks
            (the payload was deduplicated, but the API is not idempotent, so the logical record is duplicated).
          </p>
        )}
      </Card>

      <Card title="Integrity verification (invariants)">
        <div className="table-wrap">
          <table aria-label="Invariants">
            <thead><tr><th>#</th><th>Invariant</th><th>Result</th><th>Evidence</th></tr></thead>
            <tbody>
              {r.invariants.map((i) => (
                <tr key={i.id} className={i.holds ? '' : 'row-bad'}>
                  <td>{i.id}</td><td>{i.title}</td>
                  <td>{i.holds ? <span className="ok-mark">holds</span> : <strong className="bad-mark">VIOLATED (reported)</strong>}</td>
                  <td>{i.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3>Restoring every complete file afterwards</h3>
        <ul className="plain">
          {r.restores.map((x) => (
            <li key={x.filename}>{x.filename}: {x.outcome === 'restored' ? (x.matches_original ? 'restored, bytes identical to the upload' : 'restored but DIFFERENT from the upload') : `refused (${x.code})`}</li>
          ))}
        </ul>
        <p className="muted">
          Isolation: the experiment used its own temporary store (removed afterwards: {String(r.isolation?.temporary_store_removed)}) and did not touch application data
          ({String(!r.isolation?.application_data_touched)}).
        </p>
      </Card>
    </>
  )
}

export default function FaultLab() {
  const list = useApi((signal) => api.reliabilityScenarios(signal))
  const [selected, setSelected] = useState(null)
  const [state, setState] = useState({ status: 'idle', result: null, error: null })
  const [runAll, setRunAll] = useState(null)

  const run = async (id) => {
    setState({ status: 'running', result: null, error: null })
    try {
      const result = await api.runExperiment(id)
      setState({ status: 'done', result, error: null })
      return result
    } catch (error) {
      setState({ status: 'failed', result: null, error })
      return null
    }
  }

  const all = async (scenarios) => {
    const rows = []
    setRunAll({ running: true, rows })
    for (const s of scenarios) {
      try {
        const r = await api.runExperiment(s.id)
        rows.push({ id: s.id, title: s.title, ok: r.all_expectations_met, status: r.recovery_status })
      } catch (e) {
        rows.push({ id: s.id, title: s.title, ok: false, status: `error: ${e.message}` })
        break
      }
      setRunAll({ running: true, rows: [...rows] })
    }
    setRunAll({ running: false, rows: [...rows] })
  }

  return (
    <AsyncState state={list} onRetry={() => list.reload()}>
      {(data) => {
        const scenario = data.scenarios.find((s) => s.id === selected) || null
        return (
          <>
            <Card title="Fault injection & recovery lab" aside={<StatusBadge tone={data.enabled ? 'ok' : 'warn'}>{data.enabled ? 'enabled' : 'disabled'}</StatusBadge>}>
              <p>
                Each experiment injects <strong>one real failure</strong> into the real upload, restore, delete or recovery code of a <strong>private temporary store</strong>,
                then reads back what SQLite, the filesystem and the auditor say, runs the explicit recovery, and verifies the result by restoring files.
              </p>
              {!data.enabled && (
                <div className="notice" role="note">
                  <strong>The lab is disabled by default.</strong>
                  <span>Start the backend with <code>CHUNKVAULT_ENABLE_FAULT_LAB=1</code> to run experiments. Nothing below has been run.</span>
                </div>
              )}
              <ul>{data.safety.map((s) => <li key={s}>{s}</li>)}</ul>
            </Card>

            <Card title="Choose a failure scenario">
              <div className="scenarios" role="radiogroup" aria-label="Fault scenarios">
                {data.scenarios.map((s) => (
                  <button key={s.id} role="radio" aria-checked={selected === s.id} className={`scenario ${selected === s.id ? 'active' : ''}`}
                          onClick={() => { setSelected(s.id); setState({ status: 'idle', result: null, error: null }) }}>
                    <strong>{s.title}</strong>
                    <small>{s.operation} · {s.kind === 'crash' ? 'process crash' : 'handled error'}</small>
                  </button>
                ))}
              </div>
              {!scenario ? <p className="empty">Select a scenario to see what it does and what is expected.</p> : (
                <div className="details">
                  <h3>{scenario.title}</h3>
                  <p>{scenario.description}</p>
                  <dl className="defs">
                    <dt>Operation executed</dt><dd>{scenario.operation}</dd>
                    <dt>Failure point</dt><dd>{scenario.fault_point}</dd>
                    <dt>Expected behaviour (declared before the run)</dt>
                    <dd><ul className="plain">{scenario.expectations.map((e) => <li key={e.key}>{e.label}: <code>{e.expected}</code></li>)}</ul></dd>
                  </dl>
                  <div className="row">
                    <button className="primary" disabled={!data.enabled || state.status === 'running'} onClick={() => run(scenario.id)}>
                      {state.status === 'running' ? 'Running…' : 'Run experiment'}
                    </button>
                    <button disabled={!data.enabled || state.status === 'running' || runAll?.running} onClick={() => all(data.scenarios)}>Run all {data.scenarios.length} scenarios</button>
                  </div>
                </div>
              )}
              {state.status === 'failed' && <ErrorNotice error={state.error} />}
            </Card>

            {runAll && (
              <Card title="All scenarios">
                <table aria-label="All scenarios summary">
                  <thead><tr><th>Scenario</th><th>Observed = expected</th><th>Recovery status (backend)</th></tr></thead>
                  <tbody>{runAll.rows.map((x) => <tr key={x.id}><td>{x.title}</td><td>{x.ok ? '✓' : '✗'}</td><td>{x.status}</td></tr>)}</tbody>
                </table>
                {runAll.running && <p className="muted" role="status">Running…</p>}
                {!runAll.running && <p className="muted"><MetricTile label="Met expectations" value={`${runAll.rows.filter((x) => x.ok).length} / ${runAll.rows.length}`} /></p>}
              </Card>
            )}

            {state.status === 'idle' && scenario && <p className="empty">Not run yet: no observed result exists for this scenario.</p>}
            {state.result && <Result r={state.result} />}
          </>
        )
      }}
    </AsyncState>
  )
}
