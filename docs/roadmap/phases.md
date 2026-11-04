# Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Repo structure, HLD/LLD docs, SQLite schema + tests, FastAPI foundation, React shell, CDC parameters | done (committed) |
| 2 | Backend storage engine: CDC, SHA-256, chunk store, repository, dedup, upload, restore, delete, audit/reconcile, stats, API | implemented, awaiting approval |
| 2.5 | Hardening review (bounded request bodies, reconciliation safety) | done (see [hardening-review.md](../architecture/hardening-review.md)) |
| 3 | Frontend: upload/file UI, chunk explorer and comparison, database explorer, architecture views, audit UI, documentation viewer | implemented, awaiting approval |
| 4 | System-design visualisation: interactive HLD/LLD, lifecycle walkthroughs, CDC explorer, consistency visualisation, API and ADR explorers, responsive layout | implemented |
| 5 | Reliability engineering (isolated fault-injection lab, recovery verification), reproducible benchmarks including CDC vs fixed-size, capacity model, performance/simulation pages | implemented, awaiting approval |

Phase numbers in the planned-endpoint table and UI badges refer to this table.
Each phase requires approval before the next begins.
