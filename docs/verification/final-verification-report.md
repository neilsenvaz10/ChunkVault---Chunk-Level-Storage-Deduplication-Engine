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
| `cd backend; .venv/Scripts/python -m pytest` | **402 passed** (334 before Phase 6 + 53 security + 15 docs-consistency) |
| `.venv/Scripts/python ../scripts/verify_phase2.py` | **27/27** |
| `.venv/Scripts/python ../scripts/verify_phase6.py` (new, independent) | **74/74**: Tests A–F, database integrity, 9 fault scenarios, benchmark and simulation verification |
| `cd frontend; npm test` | **130 passed** (5 files; 128 at the first Phase 6 run, +2 reconciliation-status regression tests) |
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
- **Follow-up (Phase 6.1, 2026-11-04):** the items that were not clicked through in the first run were then exercised by hand in the browser; see section G.
- Note: test data for the browser run came from a weak JavaScript generator that repeats within 300 KB, so chunk counts there (29 positions, 3 distinct) reflect that data, not typical behaviour; ratios used elsewhere in this report come from the headless script's random data.

## E. Benchmarks and capacity model

Phase 5 measurements were **not** replaced and the benchmark/simulation engines were not rebuilt. Verification re-checked the machinery: identical input does identical work every iteration; stage timings sum exactly to wall time (no overlap); throughput = bytes ÷ seconds and ratio/savings recompute from logical and unique bytes; restored data is verified independently; timer overhead is small (instrumented vs plain median upload ×1.01–×1.08 across runs); CDC and fixed-size are compared on identical bytes. Units: binary (MiB) throughout; the model's default chunk size is 16 KiB (1 MiB → 64 chunks; 1,000,000 B → 62). Model extremes (0 %, nearly 100 % and 100 % duplicates, very large inputs, invalid parameters) behave as documented; the simulator is labelled a mathematical model and measured results are labelled as measured.

Measured Phase 5 figures still stand (`docs/architecture/benchmarking.md`): end-to-end upload ≈ 2–3 MiB/s (pure-Python CDC ≈ 61 % of upload time), identical-file ratio 2.00×, insertion/deletion near start CDC 48.1 % vs fixed 0 %, no CDC advantage for in-place edits. They describe this machine only.

## F. Limitations and open items

1. **Original reports missing — alignment BLOCKED** (no invented requirements).
2. No authentication; single user, loopback only; single writer (security-review.md, "Deployment limitations").
3. Pure-Python chunker: a few MiB/s; not a performance claim about other systems.
4. Power-loss durability not tested (reviewed only); Windows directory `fsync` limits documented.
5. No automated browser (end-to-end) test suite exists, only Vitest + API-level scripts; browser checks are manual observations.
6. Frontend bundle > 500 kB build warning (cosmetic).
7. Not done: Python dependency CVE scan (no audit tool installed; none was installed), fuzzing, penetration test, cross-browser testing (one Chromium-based pane only), real mobile device. The npm audit result is in section G.
8. Old Phase 2–5 scratch folders (`cv-*`) may remain in the system temp directory, and an ignored `scratch_pip.log` in the repo root; neither was deleted. `data/chunkvault.db` (ignored) is existing local data and was not touched.
9. Roadmap numbering was superseded (benchmarks and simulation delivered in Phase 5), recorded in `docs/roadmap/phases.md`.

## Files changed or added in Phase 6 (uncommitted)

New: `backend/app/api/guard.py`, `backend/tests/test_security.py`, `backend/tests/test_docs_consistency.py`, `scripts/verify_phase6.py`, `docs/verification/*` (four documents). Modified: config, errors, main, benchmark worker/jobs, test fixtures (local `base_url`), `scripts/verify_phase2.py`, `frontend/src/styles.css`, `frontend/src/pages/TradeoffsPage.jsx`, README, `.env.example`, and the architecture/decision/roadmap documents listed in the section C docs fix.

## G. Phase 6.1 additions (2026-11-04)

**Browser rehearsal** (isolated temporary store with the fault lab enabled; repository `data/chunkvault.db` verified unchanged afterwards, 73,728 bytes, same timestamp):
- *Architecture diagram:* zoom in/out/reset changed the SVG width 662 → 906 → 795 → 662 px; selecting a node opened its detail panel; at high zoom the diagram scrolled inside its own container while the page did not overflow.
- *CDC demonstration:* "Run comparison" with seed 12 / 196,608 B / insertion of 37 B at 100 matched the API exactly (CDC 9 chunks, 8 reused, 81.4 %; fixed-size 13 chunks, 0 reused).
- *Fault injection:* "crash before metadata commit" ended `verified_clean` with eight invariants listed; "restore failure" showed `chunk_corrupt` refusal and the unrepairable-damage status; the application store was unchanged (0 files, identical stats).
- *Reconciliation:* with a planted orphan chunk, the audit showed 1 orphan and a "Review and reconcile" button; the confirmation dialog listed the planned action, **Cancel** changed nothing, **Run reconciliation** removed the orphan; the audit was then healthy and the real file remained.
- *Capacity simulation:* 1000 files × 1 MiB, 25 % duplicates showed ratio 1.33×, 25 % savings and 750 MiB physical, matching `POST /api/simulations`; 150 % duplicates showed the server validation error; Reset restored defaults.
- *Benchmarks:* a request of about 80 MiB was refused with the server's 48 MiB budget message; a permitted 4 MiB × 3 run showed *running*, **Cancel** ended it, the UI reported "cancelled; its temporary store was removed", no worker process and no `chunkvault-*` temp directory remained, and no result was saved.
- *Layout:* all architecture tabs, simulation, performance, reliability and docs pages: no horizontal overflow at 375 and 768 px.
- *Console:* no new console errors during the sweep. The only errors in the log were three expected 422 responses from the deliberately invalid inputs and older connection-refused entries left from when the servers had been stopped before the session.
- Screenshots were not captured (the pane's screenshot timed out once; observations were read from the DOM and API).

**Documentation integration:** the Documentation page now has a "Verification & submission" group linking the final report, traceability matrix, security review and demo guide, rendered from the repository Markdown (no copies). `test_docs_consistency.py` now fails if any `docs/**/*.md` file is not linked there. The new pages and the HLD and sequence Mermaid diagrams rendered at 375 px, and no local filesystem paths appeared in the final report.

**Dependency audit** (`npm audit`; no Python audit tool is installed, so Python packages were not audited; installed: fastapi 0.162.0, starlette 1.10.0, uvicorn 0.54.0, pydantic 2.14.2, python-multipart 0.0.33, httpx 0.28.1):

| Scope | Installed package (usage path) | Severity | Applies here? | Recommendation |
|---|---|---|---|---|
| runtime | react-router / react-router-dom **6.30.6** (open redirect via backslash in `<Link>`/`navigate`; SSR `deserializeErrors`) | moderate | **No in current usage.** Every `<Link to>` is a fixed internal path or `/files/<id>` with a server-issued 32-hex id (one `encodeURIComponent`-encoded query); `navigate('/files')` is a constant; nothing takes a destination from a URL, form or user input. The app is a client-side SPA: no server rendering or hydration, so `deserializeErrors` is unreachable. | upgrade within the supported line after a compatibility run; do not force a major upgrade |
| runtime | mermaid **11.17.2** → katex **0.16.47** (prototype pollution in trust settings) | low | **No in current usage.** Mermaid renders only the repository's own bundled diagrams (no math, no user text); KaTeX is not called directly. | upgrade when mermaid ships a fixed katex |
| dev only | **vitest 2.1.9** with its nested **vite 5.4.21 / esbuild 0.21.5 / tinypool** (critical and high advisories about the Vitest UI/mock server, vite's dev-server file serving, esbuild's dev server) | critical–moderate | **Not in the shipped app.** `npm test` runs `vitest run`, which opens no UI or listening server; the advisories need a running dev/UI server reachable from a hostile web page. | upgrade the test toolchain (a major vitest bump) as a separate, tested change; avoid `vitest --ui` and do not expose it |
| dev only | top-level **vite 6.4.5 / esbuild 0.25.12** used by `npm run dev` and `npm run build` | none flagged | The audit's vite range (<=6.4.3) and esbuild range (<=0.24.2) do not include these installed versions; the flagged copies are the ones nested under vitest above. | none |

`npm audit fix --force` was **not** run, and no dependency was changed.

## H. Reconciliation status message (post-6.1 fix)

Defect (UI, low): after a reconciliation the Reliability dashboard still showed "Verification status … detection only; nothing was changed", which was wrong while "Recovery actions" showed a repair had run. Fix: the hint now depends on where the figures came from — a read-only audit says "read-only audit; nothing was changed"; a reconciliation that acted says "reconciliation modified storage (N actions); counts are from the audit run right after the repair"; one that did nothing says "reconciliation ran and changed nothing …"; a fresh audit afterwards reverts to the read-only text. Regression tests: `phase5.test.jsx` (full audit → reconcile → re-audit flow, and the hint function for 0, 1 and several actions). Affected suites re-run: frontend 130 passed.
