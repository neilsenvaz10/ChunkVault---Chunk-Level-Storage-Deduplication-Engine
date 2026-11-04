import { Card } from '../components/ui'

// Educational comparisons. The reasoning mirrors docs/decisions/ (ADR-0001..0006).
// Scaled-architecture notes are conceptual: ChunkVault does not implement them.
export const TRADEOFFS = [
  {
    id: 'cdc', title: 'Content-defined vs fixed-size chunking', chosen: 'Content-defined (Rabin-style)',
    pros: ['Boundaries follow content, so an insertion or deletion only disturbs nearby chunks', 'Later chunks keep their SHA-256 and are reused'],
    cons: ['More CPU than slicing every N bytes', 'Variable chunk sizes and a min/max policy to manage', 'No advantage for in-place edits, where both schemes change about one chunk'],
    why: 'The project exists to demonstrate deduplication of shifted data, which fixed-size chunking cannot do. Tests record the measured reuse for both schemes on the same data.',
    scaled: 'The same algorithm would run in parallel workers on file ranges, with the chunk index sharded by digest.',
  },
  {
    id: 'sqlite', title: 'SQLite vs distributed metadata storage', chosen: 'SQLite',
    pros: ['Real transactions, foreign keys, triggers and indexes with zero setup', 'Easy to inspect and test'],
    cons: ['One writer at a time', 'One file on one machine; no replication or horizontal scale'],
    why: 'A single-machine prototype needs correctness and inspectability, not scale. The constraints and triggers enforce the reference-count invariant in the database itself.',
    scaled: 'Metadata would move to a replicated or sharded store (for example partitioned by chunk digest) with a different transaction and consistency model.',
  },
  {
    id: 'fs', title: 'Local filesystem vs object storage', chosen: 'Local filesystem',
    pros: ['No external service; files are easy to inspect and damage in tests', 'Atomic same-volume rename gives safe publication'],
    cons: ['One disk, no replication', 'One file per chunk means many small files', 'Durability relies on fsync and platform behaviour'],
    why: 'Content addressing makes any key-value blob store a drop-in later; the prototype keeps the dependency-free option.',
    scaled: 'Chunks would live in replicated object storage keyed by digest, with erasure coding or multiple copies, and no rename-based publication.',
  },
  {
    id: 'process', title: 'Single-process vs distributed architecture', chosen: 'Single process, sequential writes',
    pros: ['Deterministic and easy to reason about', 'A simple single-writer lock removes whole classes of races'],
    cons: ['Throughput bounded by one core and one writer', 'No fault tolerance if the machine is lost'],
    why: 'The consistency protocol between SQLite and the filesystem is the interesting problem; concurrency would obscure it.',
    scaled: 'Stateless API workers, a queue of chunking jobs, and concurrency control around the shared chunk index. ChunkVault does not implement or claim any of this.',
  },
  {
    id: 'cpu', title: 'Storage savings vs CPU overhead', chosen: 'Accept CPU cost for savings',
    pros: ['Duplicate content is stored once, so payload storage can shrink'],
    cons: ['Every byte is fingerprinted and hashed, and existing chunks are re-verified on reuse', 'Pure-Python chunking is slow; savings depend entirely on how much data repeats'],
    why: 'Whether the trade pays off is an empirical question. The Performance page is reserved for real measurements (Phase 6) and shows none until they exist.',
    scaled: 'Native or vectorised chunkers, hardware SHA acceleration and caching of verified chunks would reduce the cost.',
  },
  {
    id: 'redundancy', title: 'Redundancy vs metadata complexity', chosen: 'Single copy; detect, do not repair',
    pros: ['Minimal metadata: one row per distinct chunk plus an ordered manifest', 'Deduplication removes redundancy, which is the point'],
    cons: ['One damaged chunk affects every file that references it', 'Corruption is detected (audit, verified restore) but cannot be repaired'],
    why: 'Deduplication concentrates risk in shared chunks. The prototype makes that risk visible instead of hiding it.',
    scaled: 'Replication or erasure coding per chunk, plus repair jobs, at the cost of replica tracking in the metadata.',
  },
]

export default function TradeoffsPage() {
  return (
    <>
      <p className="muted">
        Distributed alternatives are discussed <strong>conceptually only</strong>. ChunkVault is a single-machine prototype and
        implements none of them.
      </p>
      {TRADEOFFS.map((t) => (
        <Card key={t.id} title={t.title}>
          <p><strong>ChunkVault uses:</strong> {t.chosen}</p>
          <div className="two-col">
            <div><h3>Advantages</h3><ul>{t.pros.map((p) => <li key={p}>{p}</li>)}</ul></div>
            <div><h3>Disadvantages</h3><ul>{t.cons.map((c) => <li key={c}>{c}</li>)}</ul></div>
          </div>
          <h3>Why this choice</h3>
          <p>{t.why}</p>
          <h3>In a hypothetical scaled architecture</h3>
          <p>{t.scaled}</p>
        </Card>
      ))}
    </>
  )
}
