# Requirements Traceability (Phase 6)

> ## ⛔ Alignment with the original reports: **BLOCKED**
>
> The brief asks for every requirement in the original **System Design Report** and **Feasibility Report**
> to be traced to implementation and evidence. **Neither report exists in this repository.** The repository was
> searched (all of `docs/`, a `docs/references/` folder, the README, the root and `scripts/`); no such file or
> folder was found, and no file was supplied in any phase brief.
>
> Consequently:
> - **No claim is made that the implementation matches the original reports.**
> - Their requirements, constraints and success criteria have **not** been reconstructed or guessed.
> - The matrix below traces only the project's own requirement list, `docs/architecture/requirements.md`,
>   which was derived from the phase briefs. It is an *interim* traceability matrix, not the requested one.
>
> **Phase 6.1 re-check (2026-11-04):** the repository was searched again (including `docs/references/` and any `.pdf`/`.docx` files); the reports are still absent, so the status remains **BLOCKED**.
>
> **To unblock:** add the two reports (for example under `docs/references/`) and re-run this step. Each
> requirement they contain needs a row: requirement → component → test/script → status.

## Interim matrix (against `docs/architecture/requirements.md`)

Evidence legend: `T:` pytest file in `backend/tests/`, `V2`/`V6` = `scripts/verify_phase2.py` / `verify_phase6.py`
(independent end-to-end scripts on throw-away stores), `UI:` Vitest suites in `frontend/src/test/`, `B:` manual
browser check on 2026-11-04 (see the final verification report).

| ID | Requirement | Implementation | Evidence | Status |
|---|---|---|---|---|
| FR1 | Variable-sized content-defined chunking | `chunking/rabin.py` | T:test_cdc; V6 Test A (re-chunked independently with a separate `RabinChunker`) | Verified |
| FR2 | SHA-256 chunk identity | `hashing/`, `storage/filesystem.py` | T:test_hashing_and_store; V6 (hashlib recomputation of every stored chunk) | Verified |
| FR3 | Each distinct chunk stored once | `storage/`, `deduplication/` | T:test_storage_engine; V2, V6 Tests A–B (identical upload adds 0 new chunk files) | Verified |
| FR4 | SQLite metadata, ordered manifests, ref counts | `database/schema.sql`, triggers | T:test_database_schema; V6 DB checks (`integrity_check`, `foreign_key_check`, ref_count invariant) | Verified |
| FR5 | Byte-for-byte verified restore | `restoration/` | T:test_storage_engine, test_hardening; V6 Test D | Verified |
| FR6 | Delete without harming shared chunks | `deduplication/`, `api/files` | T:test_storage_api; V6 Test E; B (UI delete: 4→3 chunks, other file restored 300,000 B) | Verified |
| FR7 | Read-only detection of damage | `reliability/auditor.py` | T:test_hardening; V6 DB "audit is read-only (files unchanged)"; V6 Test F | Verified |
| FR8 | Explicit, reported repair | `reliability/reconciler.py` | T:test_faultlab; V6 Test G (9 scenarios, second reconcile = 0 actions) | Verified |
| FR9 | Real storage statistics | `api/storage` | T:test_storage_api; B (dashboard text equals `/api/storage/stats`) | Verified |
| FR10 | Documented API and interactive UI | `api/`, `frontend/` | T:test_docs_consistency (docs ⇄ OpenAPI); UI 128 tests; B at 375/768/1280 px | Verified |
| FR11 | Benchmarks on this machine | `benchmarking/` | T:test_benchmarks; V6 benchmark checks; B (benchmark started from the UI, saved, shown) | Verified |
| FR12 | Analytical capacity model | `simulation/model.py` | T:test_simulation; V6 hand-computed example and extremes | Verified (model, not measurement) |
| FR13 | Isolated fault injection | `reliability/faultlab.py` | T:test_faultlab; V6 Test G | Verified |
| NFR1 | Integrity | T2 single transaction | T:test_storage_engine, test_faultlab (I1–I8) | Verified |
| NFR2 | Detectable and repairable crash states | consistency protocol | V6 Test G: all 9 scenarios end verified; 1 ends with *unrepairable damage reported honestly* (`restore_failure`) | Verified |
| NFR3 | Local durability | fsync + `synchronous=FULL` | Reviewed in code; **not** testable by power-cut here (documented limit, Windows directory fsync) | Reviewed only |
| NFR4 | Determinism | fixed polynomial/tables | T:test_cdc; V6 (independent chunker agrees) | Verified |
| NFR5 | Bounded resources | body-limit middleware, upload cap | T:test_hardening, test_security | Verified |
| NFR6 | Testability | temp stores | 401 backend + 128 frontend tests | Verified |
| NFR7 | Honesty (no fabricated metrics) | empty states, labels | T:test_docs_consistency; UI tests; B (Performance page empty until a benchmark ran) | Verified |

Rows marked *Reviewed only* have no automated evidence and are stated as such.
