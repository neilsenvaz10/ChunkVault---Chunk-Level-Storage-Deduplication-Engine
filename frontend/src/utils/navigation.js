// Single source of truth for navigation, page titles and implementation status.
// `phase` is the roadmap phase in which a module becomes functional (docs/roadmap/phases.md).

export const MODULES = [
  {
    path: '/', label: 'Dashboard', implemented: true,
    description: 'Live overview of the storage engine: real totals, deduplication savings and backend health.',
  },
  {
    path: '/files', label: 'File Storage', implemented: true,
    description: 'Upload, inspect, restore and delete files, and see exactly how each one was divided into chunks.',
  },
  {
    path: '/architecture', label: 'System Architecture', implemented: true,
    description: 'Interactive component, sequence and data-flow diagrams of the upload and restore pipelines.',
  },
  {
    path: '/database', label: 'Database Explorer', implemented: true,
    description: 'Read-only view of the SQLite metadata model: tables, keys, relationships and the reference-count invariant.',
  },
  {
    path: '/reliability', label: 'Reliability & Consistency', implemented: true,
    description: 'Read-only integrity audit and explicit reconciliation of crash leftovers.',
  },
  {
    path: '/performance', label: 'Performance Engineering', implemented: false, phase: 6,
    description: 'Planned benchmarking methodology. No measurements exist yet.',
  },
  {
    path: '/simulation', label: 'Capacity Simulation', implemented: false, phase: 7,
    description: 'Planned hypothetical workload model. Not implemented, and never measured prototype data.',
  },
  {
    path: '/tradeoffs', label: 'Architecture Trade-offs', implemented: true,
    description: 'Why ChunkVault chose each design alternative, and what would change at scale.',
  },
  {
    path: '/docs', label: 'System Design Documentation', implemented: true,
    description: 'The project documentation (requirements, HLD, LLD, schema, API, consistency, decisions).',
  },
]

export const moduleFor = (pathname) =>
  MODULES.find((m) => (m.path === '/' ? pathname === '/' : pathname === m.path || pathname.startsWith(`${m.path}/`)))
