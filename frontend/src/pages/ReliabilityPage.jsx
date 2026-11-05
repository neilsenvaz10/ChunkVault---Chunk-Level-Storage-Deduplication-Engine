import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Card, ConfirmDialog, ErrorNotice, MetricTile, StatusBadge } from '../components/ui'
import { api } from '../services/api'
import { formatInt } from '../utils/format'

// What each finding means, and what an explicit reconcile would do about it.
export const FINDING_INFO = {
  missing_chunk_file: { title: 'Missing chunks', tone: 'error', repair: null,
    meaning: 'Metadata says a chunk is stored but its bytes are gone. Every file referencing it can no longer be restored.' },
  corrupt_chunk: { title: 'Corrupted chunks', tone: 'error', repair: null,
    meaning: 'Stored bytes no longer match their SHA-256 (or size). Restores of affected files fail explicitly; nothing is silently repaired.' },
  orphan_chunk_file: { title: 'Orphan chunk files', tone: 'warn', repair: 'Delete the chunk file (no metadata references it).',
    meaning: 'A chunk file exists with no metadata row, typically left by an interrupted upload or delete. Harmless, wastes space.' },
  unreferenced_chunk: { title: 'Unreferenced chunk records', tone: 'warn', repair: 'Delete the record, then its bytes (reference count is 0).',
    meaning: 'A chunk record with ref_count 0 and no manifest rows: a garbage-collection candidate.' },
  pending_chunk: { title: 'Pending chunk records', tone: 'warn', repair: null,
    meaning: 'A chunk record left in the intermediate "pending" state; the engine does not normally create these.' },
  orphan_staging_file: { title: 'Stale staging files', tone: 'warn', repair: 'Delete the staging file.',
    meaning: 'A temporary upload/chunk/restore file older than the staleness threshold, left by an interrupted operation.' },
  ref_count_mismatch: { title: 'Reference-count mismatches', tone: 'error', repair: null,
    meaning: 'A chunk\'s stored ref_count differs from the number of manifest rows referencing it. The invariant is trigger-maintained, so this means external tampering; it is reported, never "fixed" automatically.' },
  manifest_inconsistent: { title: 'Inconsistent manifests', tone: 'error', repair: null,
    meaning: 'A file\'s chunk positions have gaps or its chunk sizes do not add up to the recorded file size.' },
  file_hash_mismatch: { title: 'File hash mismatches', tone: 'error', repair: null,
    meaning: 'All chunks verify, but the reassembled file does not match the whole-file SHA-256 recorded at upload.' },
  incomplete_file: { title: 'Incomplete files', tone: 'warn', repair: 'Mark the file as failed.',
    meaning: 'A file stuck in "uploading": the upload was interrupted before its final commit. It is not restorable.' },
  stuck_operation: { title: 'Incomplete operations', tone: 'warn', repair: 'Mark the operation as aborted.',
    meaning: 'An operation still "running" long after it started, i.e. interrupted by a crash.' },
}

// Recovery dashboard: detection results (from the read-only audit) and, separately, what a repair did.
// The status hint must say whether storage was modified: a read-only audit never changes anything, a
// reconciliation does (and its counts come from the audit the server runs right after repairing).
export function statusHint(source, actions) {
  if (source !== 'reconcile') return 'read-only audit; nothing was changed'
  if (!actions) return 'reconciliation ran and changed nothing; counts are from the audit run right after it'
  return `reconciliation modified storage (${actions} action${actions === 1 ? '' : 's'}); counts are from the audit run right after the repair`
}

export function RecoveryDashboard({ report, actions, source = 'audit' }) {
  const k = report?.counts_by_kind || {}
  const n = (...names) => names.reduce((t, x) => t + (k[x] || 0), 0)
  const v = (x) => (report ? formatInt(x) : '—')
  return (
    <div className="tiles" role="group" aria-label="Recovery dashboard">
      <MetricTile label="Files checked" value={v(report?.files_checked)} />
      <MetricTile label="Chunks checked" value={v(report?.chunks_checked)} />
      <MetricTile label="Missing chunks" value={v(n('missing_chunk_file'))} tone={n('missing_chunk_file') ? 'bad' : undefined} />
      <MetricTile label="Hash mismatches" value={v(n('corrupt_chunk', 'file_hash_mismatch'))} tone={n('corrupt_chunk', 'file_hash_mismatch') ? 'bad' : undefined} />
      <MetricTile label="Reference-count mismatches" value={v(n('ref_count_mismatch'))} tone={n('ref_count_mismatch') ? 'bad' : undefined} />
      <MetricTile label="Orphaned chunks" value={v(n('orphan_chunk_file', 'unreferenced_chunk'))} />
      <MetricTile label="Recovery actions" value={actions === null || actions === undefined ? 'none run' : formatInt(actions)} hint="from the last explicit reconciliation" />
      <MetricTile label="Verification status" value={!report ? 'not audited' : report.healthy ? 'healthy' : 'findings'} tone={!report ? undefined : report.healthy ? 'good' : 'bad'} hint={statusHint(source, actions)} />
    </div>
  )
}

function Findings({ report }) {
  const groups = Object.entries(report.counts_by_kind)
  return (
    <>
      <div className="tiles">
        <MetricTile label="Chunks checked" value={formatInt(report.chunks_checked)} />
        <MetricTile label="Files checked" value={formatInt(report.files_checked)} />
        <MetricTile label="Findings" value={formatInt(report.findings.length)} tone={report.healthy ? 'good' : 'bad'} />
      </div>
      {report.healthy ? (
        <div className="notice notice-ok" role="status">
          <strong>No inconsistencies detected.</strong> Every stored chunk matches its digest, reference counts match the
          manifests, and no crash leftovers were found.
        </div>
      ) : (
        groups.map(([kind]) => {
          const info = FINDING_INFO[kind] || { title: kind, tone: 'warn', meaning: '' }
          const items = report.findings.filter((f) => f.kind === kind)
          return (
            <section key={kind} className="finding-group">
              <h3>
                {info.title} <StatusBadge tone={info.tone}>{items.length}</StatusBadge>
              </h3>
              <p className="muted">{info.meaning}</p>
              <ul className="plain">
                {items.slice(0, 50).map((f) => (
                  <li key={`${f.kind}-${f.subject}`}><code>{f.subject.length > 20 ? `${f.subject.slice(0, 16)}…` : f.subject}</code> — {f.detail}</li>
                ))}
              </ul>
              {items.length > 50 && <p className="muted">…and {items.length - 50} more.</p>}
            </section>
          )
        })
      )}
    </>
  )
}

export default function ReliabilityPage() {
  const [audit, setAudit] = useState({ status: 'idle', report: null, error: null })
  const [confirm, setConfirm] = useState(false)
  const [rec, setRec] = useState({ status: 'idle', result: null, error: null })

  const run = async () => {
    setAudit({ status: 'running', report: null, error: null })
    try {
      setAudit({ status: 'done', report: await api.audit({}), error: null })
    } catch (e) {
      setAudit({ status: 'failed', report: null, error: e })
    }
  }

  const reconcile = async () => {
    setRec({ status: 'running', result: null, error: null })
    try {
      const result = await api.reconcile()
      setRec({ status: 'done', result, error: null })
      setAudit({ status: 'done', report: result.remaining, error: null, source: 'reconcile' })
    } catch (e) {
      setRec({ status: 'failed', result: null, error: e })
    } finally {
      setConfirm(false)
    }
  }

  const preview = audit.report
    ? audit.report.findings.filter((f) => FINDING_INFO[f.kind]?.repair).map((f) => ({ ...f, repair: FINDING_INFO[f.kind].repair }))
    : []

  return (
    <>
      <Card title="Recovery dashboard" aside={<StatusBadge tone="ok">detection and repair are separate</StatusBadge>}>
        <RecoveryDashboard report={audit.report} actions={rec.result ? rec.result.actions.length : null} source={audit.source} />
        {!audit.report && <p className="empty">Run the read-only audit below to fill this in; nothing is shown until it has actually been run.</p>}
      </Card>

      <Card title="Read-only integrity audit" className="panel-audit" aside={<StatusBadge tone="ok">changes nothing</StatusBadge>}>
        <span className="panel-label">Inspect · read-only</span>
        <p>
          The audit re-hashes every stored chunk, checks every manifest and reference count, and looks for crash leftovers.
          It never modifies the database or the chunk store. Large stores take longer because every chunk is re-read.
        </p>
        <button className="primary" onClick={run} disabled={audit.status === 'running'}>
          {audit.status === 'running' ? 'Auditing…' : 'Run integrity audit'}
        </button>
        {audit.status === 'failed' && <ErrorNotice error={audit.error} onRetry={run} />}
        {audit.report && <Findings report={audit.report} />}
      </Card>

      <Card title="Reconciliation (explicit repair)" className="panel-repair" aside={<StatusBadge tone="warn">modifies storage after confirmation</StatusBadge>}>
        <span className="panel-label">Repair · needs your confirmation</span>
        <p>
          Reconciliation cleans up what a crash can leave behind: stale staging files, orphan chunk files, unreferenced chunk
          records, files stuck in &ldquo;uploading&rdquo; and operations stuck in &ldquo;running&rdquo;. It never deletes a chunk that any
          file references, and never rewrites missing or corrupt chunks — those stay reported.
        </p>
        {!audit.report ? (
          <p className="empty">Run the audit first: the action preview is derived from its findings.</p>
        ) : preview.length === 0 ? (
          <p className="empty">The latest audit found nothing reconciliation would change.</p>
        ) : (
          <button onClick={() => setConfirm(true)} disabled={rec.status === 'running'}>Review and reconcile…</button>
        )}
        {rec.status === 'failed' && <ErrorNotice error={rec.error} />}
        {rec.result && (
          <div className="notice notice-ok" role="status">
            <strong>Reconciliation finished: {rec.result.actions.length} action(s)</strong>
            <ul className="plain">
              {rec.result.actions.map((a, i) => <li key={i}><code>{a.action}</code> {a.subject.slice(0, 20)} — {a.detail}</li>)}
            </ul>
            {rec.result.actions.length === 0 && <span>No changes were required: storage was not modified.</span>}
            <span>The audit above was refreshed with what remains.</span>
          </div>
        )}
        {confirm && (
          <ConfirmDialog title="Run reconciliation?" confirmLabel="Run reconciliation" busy={rec.status === 'running'}
                         onConfirm={reconcile} onCancel={() => setConfirm(false)}>
            <p>Based on the latest audit, reconciliation is expected to perform these actions (the final list is reported afterwards):</p>
            <ul className="plain">
              {preview.slice(0, 12).map((f) => <li key={`${f.kind}-${f.subject}`}><code>{f.subject.slice(0, 16)}</code> — {f.repair}</li>)}
            </ul>
            {preview.length > 12 && <p className="muted">…and {preview.length - 12} more.</p>}
            <p className="muted">Anything created since that audit is reviewed again by the server under its writer lock.</p>
          </ConfirmDialog>
        )}
      </Card>

      <Card title="Fault injection">
        <p>
          Controlled experiments that break an <strong>isolated temporary store</strong> (never your data) and verify recovery live in the{' '}
          <Link to="/architecture">System Architecture</Link> page under <em>Fault injection &amp; recovery</em>. There are no fault controls here: this page only
          audits and repairs your real store, and only when you ask.
        </p>
        <p className="muted"><Link to="/docs">Read the consistency protocol →</Link></p>
      </Card>
    </>
  )
}
