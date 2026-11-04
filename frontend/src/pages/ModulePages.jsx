import PlaceholderPage from '../components/PlaceholderPage'

export const FileStorage = () => (
  <PlaceholderPage
    title="File Storage" phase={3}
    purpose="Upload files, list stored files, restore and delete them."
    will={['Upload with per-file chunk and deduplication summary', 'File list with status', 'Restore and delete actions']}
    docs="docs/api/planned-contracts.md"
  />
)

export const SystemArchitecture = () => (
  <PlaceholderPage
    title="System Architecture" phase={1}
    purpose="Interactive view of the component architecture. In Phase 1 the diagrams exist as Mermaid sources only."
    will={['Rendered HLD component diagram', 'Upload and restore sequence views']}
    docs="docs/architecture/hld.md"
  />
)

export const DatabaseExplorer = () => (
  <PlaceholderPage
    title="Database Explorer" phase={3}
    purpose="Read-only inspection of the files, chunks, file_chunks and operations tables."
    will={['Table browser', 'Reference-count invariant check results']}
    docs="docs/database/schema.md"
  />
)

export const Reliability = () => (
  <PlaceholderPage
    title="Reliability & Consistency" phase={5}
    purpose="Integrity audits, crash-recovery reconciliation and corruption detection."
    will={['Integrity audit runner and findings', 'Reconciliation report']}
    docs="docs/architecture/consistency-protocol.md"
  />
)

export const Performance = () => (
  <PlaceholderPage
    title="Performance Engineering" phase={6}
    purpose="Local benchmarks of chunking throughput and deduplication ratio. Results will be measured, never preset."
    will={['Benchmark runs with recorded parameters', 'Fixed-size vs content-defined chunking comparison']}
    docs="docs/roadmap/phases.md"
  />
)

export const CapacitySimulation = () => (
  <PlaceholderPage
    title="Capacity Simulation" phase={7}
    purpose="Analytical capacity model driven by user-supplied assumptions. Output will be labelled as model output."
    will={['Assumption inputs', 'Projected physical size and metadata overhead']}
    docs="docs/roadmap/phases.md"
  />
)

export const Tradeoffs = () => (
  <PlaceholderPage
    title="Architecture Trade-offs" phase={1}
    purpose="Rationale for the main design choices. The written decision records already exist in the repository."
    will={['CDC vs fixed-size chunking', 'SQLite vs distributed database', 'Local filesystem vs object storage']}
    docs="docs/decisions/"
  />
)

export const Documentation = () => (
  <PlaceholderPage
    title="System Design Documentation" phase={1}
    purpose="In-app rendering of the design documents. In Phase 1 read them directly in the docs/ folder."
    will={['HLD, LLD, database and API documents']}
    docs="docs/"
  />
)
