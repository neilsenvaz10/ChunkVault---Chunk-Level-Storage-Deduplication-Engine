# Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Repo structure, HLD/LLD docs, SQLite schema + tests, FastAPI foundation, React shell, CDC parameters | done (committed) |
| 2 | Backend storage engine: CDC, SHA-256, chunk store, repository, dedup, upload, restore, delete, audit/reconcile, stats, API | done (committed) |
| 2.5 | Hardening review (bounded request bodies, reconciliation safety) | done (see [hardening-review.md](../architecture/hardening-review.md)) |
| 3 | Frontend: upload/file UI, chunk explorer and comparison, database explorer, architecture views, audit UI, documentation viewer | done (committed) |
| 4 | System-design visualisation: interactive HLD/LLD, lifecycle walkthroughs, CDC explorer, consistency visualisation, API and ADR explorers, responsive layout | done (committed) |
| 5 | Reliability engineering (isolated fault-injection lab, recovery verification), reproducible benchmarks including CDC vs fixed-size, capacity model, performance/simulation pages | done (committed) |
| 6 | Final verification: independent end-to-end checks, database integrity, security review and fixes, documentation audit, demo guide, final report ([verification/](../verification/final-verification-report.md)) | implemented, awaiting approval |

The original numbering of this roadmap (benchmarks as phase 6, simulation as phase 7) was superseded: both were delivered in phase 5.
Each phase requires approval before the next begins.
