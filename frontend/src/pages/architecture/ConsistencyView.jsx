import { useState } from 'react'
import { Card } from '../../components/ui'

// Mirrors docs/architecture/consistency-protocol.md and backend/app/deduplication/upload.py.
// `lane` shows which store performs the step: the filesystem or SQLite.
export const PROTOCOL_STEPS = [
  { lane: 'sqlite', title: 'Upload operation begins', detail: 'The upload body is streamed into a file in staging/ (filesystem), then transaction T1 commits the file row as "uploading" and an operation row as "running". From now on an interrupted upload can never look complete.' },
  { lane: 'fs', title: 'Chunk bytes are staged', detail: 'Each NEW chunk is written to a uniquely named temporary file in data/staging/. Duplicate chunks are not written at all.' },
  { lane: 'fs', title: 'Staged chunks are flushed', detail: 'The file is flushed and fsynced so its bytes are on disk before anything can refer to them.' },
  { lane: 'fs', title: 'Chunks are atomically published', detail: 'The staged file is renamed to chunks/ab/<sha256> on the same volume. Readers see the whole chunk or none of it; an existing chunk is never overwritten.' },
  { lane: 'sqlite', title: 'SQLite transaction begins', detail: 'Transaction T2 starts with BEGIN IMMEDIATE, taking the write lock. Everything until COMMIT is atomic — but only for SQLite.' },
  { lane: 'sqlite', title: 'File metadata and manifest are inserted', detail: 'New chunk rows (state "stored"), every ordered file_chunks row, the file\'s SHA-256 and status "complete", and the operation "succeeded".' },
  { lane: 'sqlite', title: 'Reference-count triggers execute', detail: 'Each file_chunks insert fires a trigger that adds 1 to that chunk\'s ref_count, so a chunk used twice in one file counts twice.' },
  { lane: 'sqlite', title: 'SQLite transaction commits', detail: 'COMMIT with synchronous=FULL makes the metadata durable. If anything failed before this point the whole of T2 rolls back.' },
  { lane: 'fs', title: 'Upload becomes complete', detail: 'The staged upload file is removed and the response is returned. The file is only visible as "complete" because the commit above succeeded.' },
]

export const INTERRUPTIONS = [
  { id: 'before-publish', after: 2, title: 'Failure before chunk publication', happens: 'The process dies while receiving the upload or staging chunks.', leaves: 'A file stuck in "uploading", an operation stuck "running" and temporary staging files. No chunk is visible in the store; no complete file exists.', recovery: 'The audit reports stale staging files, the incomplete file and the stuck operation. Explicit reconciliation deletes the staging files and marks the file failed and the operation aborted.' },
  { id: 'after-publish', after: 4, title: 'Failure after publication but before the metadata commit', happens: 'Some or all chunks were published, but T2 never started or never committed.', leaves: 'Orphan chunk files that no metadata references (plus the leftovers above). The file is still not complete.', recovery: 'The audit reports orphan chunk files. A retried upload restarts and reuses valid orphan chunks ("adopt"); reconciliation deletes them.' },
  { id: 'during-txn', after: 7, title: 'Failure during the metadata transaction', happens: 'An error or crash occurs inside T2.', leaves: 'Nothing from T2: SQLite rolls the whole transaction back, including every trigger-driven ref_count change. Published chunk files remain as orphans.', recovery: 'Same as above. Because T2 is atomic there are never partial manifests or half-updated counts.' },
  { id: 'after-commit', after: 8, title: 'Failure after the commit', happens: 'The process dies after COMMIT, before cleanup or the response.', leaves: 'A consistent, complete file. At most a stale staged upload file remains.', recovery: 'The file is restorable subject to the documented durability limits: bytes were fsynced before publication, but Windows has no directory fsync, so a power loss right after a rename could lose a chunk file, which the audit would report as a missing chunk. Nothing here is crash-proof.' },
]

export default function ConsistencyView() {
  const [step, setStep] = useState(0)
  const [interruption, setInterruption] = useState(null)
  const sel = PROTOCOL_STEPS[step]
  const cut = INTERRUPTIONS.find((i) => i.id === interruption)

  return (
    <>
      <Card title="Why a protocol is needed">
        <p>
          <strong>SQLite cannot atomically commit filesystem writes.</strong> A transaction makes database changes all-or-nothing,
          but it knows nothing about files. ChunkVault therefore orders the two kinds of operation so that every crash state is either a clean
          rollback or a detectable leftover — and a file is only <em>complete</em> once all its chunk bytes are durable <em>and</em> one
          transaction has committed their metadata.
        </p>
        <p className="muted">This is a local, single-machine design. It does not claim crash-proof durability or any distributed fault tolerance.</p>
      </Card>

      <Card title="Upload protocol: filesystem steps vs SQLite steps">
        <div className="lane-legend legend">
          <span><span className="swatch lane-fs" /> Filesystem operation (not part of any transaction)</span>
          <span><span className="swatch lane-sqlite" /> SQLite operation</span>
          <span><span className="swatch lane-txn" /> Inside the atomic T2 transaction</span>
        </div>
        <ol className="timeline" aria-label="Upload protocol steps">
          {PROTOCOL_STEPS.map((s, i) => {
            const inTxn = i >= 4 && i <= 7
            const cutHere = cut && cut.after === i + 1
            return (
              <li key={s.title} className={`lane-${s.lane} ${inTxn ? 'in-txn' : ''}`}>
                <button className={i === step ? 'active' : ''} aria-current={i === step ? 'step' : undefined} onClick={() => setStep(i)}>
                  <span className="stage-no">{i + 1}</span>
                  <span>{s.title}</span>
                  <span className="lane-tag">{s.lane === 'fs' ? 'filesystem' : inTxn ? 'SQLite · T2' : 'SQLite'}</span>
                </button>
                {cutHere && <div className="cut-marker" role="note">✂ interruption: {cut.title}</div>}
              </li>
            )
          })}
        </ol>
        <div className="details" aria-live="polite">
          <h3>{step + 1}. {sel.title}</h3>
          <p>{sel.detail}</p>
        </div>
      </Card>

      <Card title="Possible interruption points">
        <p className="muted">Select one to mark it on the timeline and see the expected consequence.</p>
        <div className="row" role="group" aria-label="Interruption points">
          {INTERRUPTIONS.map((i) => (
            <button key={i.id} className={interruption === i.id ? 'active' : ''} aria-pressed={interruption === i.id}
                    onClick={() => setInterruption(interruption === i.id ? null : i.id)}>
              {i.title}
            </button>
          ))}
        </div>
        {cut ? (
          <div className="details" aria-live="polite">
            <h3>{cut.title}</h3>
            <dl className="defs">
              <dt>What happens</dt><dd>{cut.happens}</dd>
              <dt>State left behind</dt><dd>{cut.leaves}</dd>
              <dt>Detection and recovery</dt><dd>{cut.recovery}</dd>
            </dl>
          </div>
        ) : <p className="empty">No interruption selected.</p>}
        <p className="muted">
          Deletion reverses the order: metadata is committed first (reference counts drop, unreferenced chunk rows are removed), and
          chunk files are unlinked only afterwards, so a crash leaves harmless orphan files rather than dangling references.
        </p>
      </Card>
    </>
  )
}
