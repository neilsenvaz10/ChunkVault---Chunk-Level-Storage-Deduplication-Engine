# Final Verification Report — ChunkVault (Phase 6)

Date: 2026-11-04 · Environment: Windows 11, CPython 3.14.6, SQLite 3.50.4, Node/Vite, 12 CPUs.
Nothing has been committed or pushed as part of Phase 6; the working tree holds all changes.

## Verdict

**Ready with documented limitations.**

- The system works end to end, every automated and manual check below passed after fixes, and no Critical or High issue is open.
- **Submission against the original reports is not established:** the System Design Report and Feasibility Report are absent from the repository, so report alignment is **BLOCKED** ([requirements-traceability.md](requirements-traceability.md)). If the submission requires proof of alignment, that gate is unmet until the reports are supplied.

## A. Requirement coverage

Interim matrix against `docs/architecture/requirements.md` (FR1–FR13, NFR1–NFR7): all verified except **NFR3 (durability under power loss), reviewed only** — see [requirements-traceability.md](requirements-traceability.md). Coverage of the *original* reports: **not assessed (BLOCKED)**.

## B. Test evidence (final run, 2026-11-04)

| Check | Result |
|---|---|
| `cd backend; .venv/Scripts/python -m pytest` | **401 passed** (334 before Phase 6 + 53 security + 14 docs-consistency) |
| `.venv/Scripts/python ../scripts/verify_phase2.py` | **27/27** |
| `.venv/Scripts/python ../scripts/verify_phase6.py` (new, independent) | **74/74**: Tests A–F, database integrity, 9 fault scenarios, benchmark and simulation verification |
| `cd frontend; npm test` | **128 passed** (5 files) |
| `cd frontend; npm run build` | **built**; one non-blocking warning: some bundles exceed 500 kB (Mermaid is lazy-loaded) |
| Browser run (built-in browser, isolated store) | see section D |

`verify_phase6.py` deliberately shares no code with the application's own assertions: it re-chunks with a separate `RabinChunker`, recomputes SHA-256 with `hashlib`, reads SQLite directly, and uses fresh temporary stores (never `data/`).

Brief's end-to-end tests: **A** original upload (45 chunks equal an independent re-chunk; every chunk file re-hashes to its name) ✔ · **B** exact duplicate (no new chunk files; ratio 2.0×, savings 50 %) ✔ · **C** 61-byte insertion (44 of 45 chunks reused, 1 new; boundaries resynchronise) ✔ · **D** restoration byte-for-byte, no temp leftovers ✔ · **E** safe deletion (shared chunks survive; exclusive reclaimed; repeat delete is a clean 404) ✔ · **F** corruption (audit finds exactly the damaged chunk, restore fails with `chunk_corrupt` and no partial output, reconcile does not pretend to repair) ✔ · **G** the nine fault scenarios (below) ✔ · **H** browser workflow (section D) ✔.

**Database integrity:** `PRAGMA integrity_check` = `ok`; `PRAGMA foreign_key_check` empty; foreign keys ON for every new connection; `ref_count` equals the number of manifest rows per chunk (raw SQL, including a chunk repeated inside one file); manifest rows immutable; referenced chunks cannot be deleted; duplicate positions and invalid hashes rejected; the audit is read-only (database and chunk files unchanged).

**Reliability (nine scenarios, private stores, two reconcile passes each):** `upload_stream_crash`, `cdc_failure`, `crash_before_metadata_commit`, `metadata_failure`, `crash_after_commit`, `delete_failure_rollback`, `delete_crash_after_commit`, `recovery_interrupted` → `verified_clean`, with the second reconcile performing 0 actions; `restore_failure` → `verified_with_unrepairable_damage` (corruption detected, deliberately not repaired). The lab is disabled by default and left no store or application data directory behind.

## C. Defects and security findings

| Item | Severity | Status |
|---|---|---|
| CSRF on upload/delete/reconcile/benchmark (no preflight for simple requests) | Medium | **Fixed** (`LocalAccessGuard`, Origin check) + regression tests |
| DNS rebinding (any Host accepted) | Medium | **Fixed** (Host allow-list) + regression tests |
| Temp-directory path leaked in benchmark error/log text | Low | **Fixed** (`scrub_paths`) + regression tests |
| File-detail page widened the page on a 375 px viewport (unbreakable file id in a table cell) | Low (layout) | **Fixed** (`td code { overflow-wrap: anywhere }`); re-checked at 375 px |
| ~20 stale statements in docs/comments (features described as planned/interface-only, a wrong route `/content` instead of `/download`, MB/s claim, stale status headings, missing env vars) | Low (docs) | **Fixed**; `test_docs_consistency.py` now guards routes, schema columns, CDC parameters, ADR sections and stale phase markers |

Details and the deployment limitations (no authentication, unauthenticated mutating endpoints, loopback-only use, single worker, etc.) are in [security-review.md](security-review.md). No Critical or High findings.

## D. Browser verification (built-in browser, isolated temporary store, fault lab enabled)

- **Pages:** all nine top-level routes plus file detail and compare, at **375, 768 and 1280 px**: no horizontal page overflow after the fix (the 753/768 and 1265/1280 scroll widths are the vertical scrollbar), no console errors across the whole session.
- **Keyboard:** first Tab focus lands on the "Skip to content" link with a visible outline.
- **Workflow:** two files uploaded through the UI's proxy (Origin guard let the UI through); the Dashboard text equalled `/api/storage/stats` exactly (2 files, 600,050 logical B, 4 unique chunks / 39,090 B, 58 references / 54 reused, 93.5 %, 15.35×); the file page rendered correctly at 375 px; **Delete** showed a confirmation and removed nothing until confirmed, then redirected to the list; afterwards unique chunks fell 4 → 3, no unreferenced chunks, the audit was healthy, and the other file restored to exactly 300,000 B; a benchmark started with the **Start benchmark** button completed, was saved and displayed (identical workload 2.00×, 1.88 MiB/s upload).
- **Not clicked through one by one in this run:** diagram zoom/scroll, chunking-demo controls, fault-lab buttons, reconcile button, simulation form and benchmark cancel. These are covered by the Vitest suites and the API checks above, but no human-style browser click-through of them was done in Phase 6. (The demo guide asks to rehearse them.)
- Note: test data for the browser run came from a weak JavaScript generator that repeats within 300 KB, so chunk counts there (29 positions, 3 distinct) reflect that data, not typical behaviour; ratios used elsewhere in this report come from the headless script's random data.

## E. Benchmarks and capacity model

Phase 5 measurements were **not** replaced and the benchmark/simulation engines were not rebuilt. Verification re-checked the machinery: identical input does identical work every iteration; stage timings sum exactly to wall time (no overlap); throughput = bytes ÷ seconds and ratio/savings recompute from logical and unique bytes; restored data is verified independently; timer overhead is small (instrumented vs plain median upload ×1.01–×1.08 across runs); CDC and fixed-size are compared on identical bytes. Units: binary (MiB) throughout; the model's default chunk size is 16 KiB (1 MiB → 64 chunks; 1,000,000 B → 62). Model extremes (0 %, nearly 100 % and 100 % duplicates, very large inputs, invalid parameters) behave as documented; the simulator is labelled a mathematical model and measured results are labelled as measured.

Measured Phase 5 figures still stand (`docs/architecture/benchmarking.md`): end-to-end upload ≈ 2–3 MiB/s (pure-Python CDC ≈ 61 % of upload time), identical-file ratio 2.00×, insertion/deletion near start CDC 48.1 % vs fixed 0 %, no CDC advantage for in-place edits. They describe this machine only.

## F. Limitations and open items

1. **Original reports missing — alignment BLOCKED** (no invented requirements).
2. No authentication; single user, loopback only; single writer (security-review.md, "Deployment limitations").
3. Pure-Python chunker: a few MiB/s; not a performance claim about other systems.
4. Power-loss durability not tested (reviewed only); Windows directory `fsync` limits documented.
5. Section D "not clicked through" items; no automated browser (end-to-end) test suite exists, only Vitest + API-level scripts.
6. Frontend bundle > 500 kB build warning (cosmetic).
7. Not done: dependency CVE scan, fuzzing, penetration test, cross-browser testing (one Chromium-based pane only), real mobile device.
8. Old Phase 2–5 scratch folders (`cv-*`) may remain in the system temp directory, and an ignored `scratch_pip.log` in the repo root; neither was deleted. `data/chunkvault.db` (ignored) is existing local data and was not touched.
9. Roadmap numbering was superseded (benchmarks and simulation delivered in Phase 5), recorded in `docs/roadmap/phases.md`.

## Files changed or added in Phase 6 (uncommitted)

New: `backend/app/api/guard.py`, `backend/tests/test_security.py`, `backend/tests/test_docs_consistency.py`, `scripts/verify_phase6.py`, `docs/verification/*` (four documents). Modified: config, errors, main, benchmark worker/jobs, test fixtures (local `base_url`), `scripts/verify_phase2.py`, `frontend/src/styles.css`, `frontend/src/pages/TradeoffsPage.jsx`, README, `.env.example`, and the architecture/decision/roadmap documents listed in the section C docs fix.
