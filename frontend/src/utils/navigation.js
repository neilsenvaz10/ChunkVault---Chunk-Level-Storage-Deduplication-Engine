// Single source of truth for navigation and module status.
// `phase` is the roadmap phase in which the module becomes functional
// (see docs/roadmap/phases.md). Phase 1 only implements the shell.

export const MODULES = [
  { path: '/', label: 'Dashboard', phase: 1, implemented: true },
  { path: '/files', label: 'File Storage', phase: 3 },
  { path: '/architecture', label: 'System Architecture', phase: 1 },
  { path: '/database', label: 'Database Explorer', phase: 3 },
  { path: '/reliability', label: 'Reliability & Consistency', phase: 5 },
  { path: '/performance', label: 'Performance Engineering', phase: 6 },
  { path: '/simulation', label: 'Capacity Simulation', phase: 7 },
  { path: '/tradeoffs', label: 'Architecture Trade-offs', phase: 1 },
  { path: '/docs', label: 'System Design Documentation', phase: 1 },
]
