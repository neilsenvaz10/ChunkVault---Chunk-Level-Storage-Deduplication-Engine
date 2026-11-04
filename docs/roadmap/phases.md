# Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Repo structure, HLD/LLD docs, SQLite schema + tests, FastAPI foundation, React shell, CDC parameters | done (committed) |
| 2 | Backend storage engine: CDC, SHA-256, chunk store, repository, dedup, upload, restore, delete, audit/reconcile, stats, API | implemented, awaiting approval |
| 2.5 | Hardening review (bounded request bodies, reconciliation safety) | done (see [hardening-review.md](../architecture/hardening-review.md)) |
| 3 | Frontend: upload/file UI, chunk explorer and comparison, database explorer, architecture views, audit UI, documentation viewer | implemented, awaiting approval |
| 4 | System-design visualisation: interactive HLD/LLD, lifecycle walkthroughs, CDC explorer, consistency visualisation, API and ADR explorers, responsive layout | implemented |
| 5 | Interactive fault-injection demonstrations and reliability UI | not started |
| 6 | Benchmark runner (fixed-size vs CDC, measured on this machine) | not started |
| 7 | Analytical capacity simulation | not started |

Phase numbers in the planned-endpoint table and UI badges refer to this table.
Each phase requires approval before the next begins.
