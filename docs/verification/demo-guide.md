# Demo Guide (10–15 minutes)

Every "expected" below was observed in Phase 6 verification (`scripts/verify_phase6.py`, a manual browser run on
2026-11-04, and the Phase 5 benchmark report). Where a number depends on the data you upload, the guide says so
and gives the *pattern* that was verified, not a promise of exact digits. Timings are from one Windows 11 machine
with a pure-Python chunker and will differ on yours.

**What was verified how.** Steps 4–7 and 9 (upload, duplicate, modified version, restore, delete) and the dashboard
numbers were verified by the headless script and, for upload/restore/delete/dashboard/benchmark, in a real browser
(uploads were sent through the UI's proxy; delete was clicked in the UI; the benchmark was started with the UI's
button). Steps 1–3, 8, 10, 12–13 (diagrams, chunking demo, database page, fault lab, simulation, docs) are covered by
the Vitest suites and by loading every page at 375/768/1280 px without console errors, but were **not** clicked through
one by one in the browser during Phase 6: rehearse them once before presenting.

## Setup (do this before the audience arrives)

Use a throw-away data directory so the demo never touches your real store. Two terminals:

```bash
cd backend
CHUNKVAULT_DATA_DIR=/tmp/chunkvault-demo CHUNKVAULT_ENABLE_FAULT_LAB=1 CHUNKVAULT_CORS_ORIGINS=http://localhost:5173 .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
cd frontend
npm run dev
```

(PowerShell: set `$env:CHUNKVAULT_DATA_DIR`, `$env:CHUNKVAULT_ENABLE_FAULT_LAB`, `$env:CHUNKVAULT_CORS_ORIGINS` first.)
Open <http://localhost:5173>. The UI must be opened as `localhost` (the API only accepts local Host headers and the
configured origin). The header badge should read **Backend healthy**.

Create two demo files (random bytes, then the same bytes with 61 bytes inserted near the start):

```bash
cd backend
.venv/Scripts/python -c "import os;b=os.urandom(600000);open('/tmp/demo-a.bin','wb').write(b);open('/tmp/demo-b.bin','wb').write(b[:700]+b'<<inserted for the demo>>'*2+b[700:])"
```

(Adjust the `/tmp` paths on Windows.) `demo-a.bin` and `demo-b.bin` share all content except a small insertion.

## Flow

| # | Min | Do | Say / show | Expected observation |
|---|---|---|---|---|
| 1 | 0–1 | **Dashboard** | Problem: storing many near-identical files wastes space. Point at the pipeline strip. | Empty store: 0 files. Health OK. |
| 2 | 1–3 | **Architecture** → click each stage of the HLD diagram, then the "Upload lifecycle" walk-through | Components: React → FastAPI → CDC → SHA-256 → dedup → SQLite + chunk store. Single process, no distributed claims. | Diagram nodes open their descriptions; diagrams scroll/zoom on narrow screens. |
| 3 | 3–4 | **Architecture → Chunking demo** (seeded, generated data) | Insert bytes near the start: content-defined boundaries move with the content; fixed-size boundaries all shift. | CDC reuses almost all later chunks; fixed-size reuses ≈ none. (Phase 5 measured on 512 KiB: **48.1 % vs 0.0 %** savings for an insertion near the start.) |
| 4 | 4–5 | **Files → upload `demo-a.bin`** | Show the chunk map. | One file, *complete*; about 600 KB cut into variable-sized chunks (min 4 KiB, max 128 KiB); savings ≈ 0 % because random data has no repetition. |
| 5 | 5–6 | **Upload `demo-a.bin` again** | Exact duplicate. | No new chunk payload; every reference count 2; **Dashboard: ratio 2.00×, savings 50.0 %** (verified on the check script). |
| 6 | 6–7 | **Upload `demo-b.bin`** | Modified version. | Nearly all chunks reused; only the chunk(s) around the insertion are new (verified pattern: 44 of 45 chunks reused, 1 new). Boundaries resynchronise after the edit. |
| 7 | 7–8 | **Compare** `a` vs `b` | Shared vs unique chunks. | Most positions shared, a few unique. |
| 8 | 8–9 | **Database** page | Tables `files`, `chunks`, `file_chunks`, `operations`; the reference-count invariant card. | Invariant status healthy; `ref_count` of shared chunks ≥ 2; read-only, whitelisted tables. |
| 9 | 9–10 | **Restore / download** a file, then **Delete** `demo-b.bin` (read the confirmation) | Shared chunks survive; only exclusive ones are removed. | Download byte-identical (header `X-ChunkVault-SHA256`). After delete, other files still restore; unique-chunk count drops by the chunks only `b` used. |
| 10 | 10–12 | **Reliability** → run 2–3 scenarios (e.g. *crash before metadata commit*, *crash after commit*) | Fault lab runs on a private temp store; shows steps, expected vs observed, recovery actions and invariants. | `recovery_status` `verified_clean` (a second reconcile does nothing). `restore_failure` honestly reports `verified_with_unrepairable_damage`: corruption is detected, not repaired. |
| 11 | 12–13 | **Performance** → *Start benchmark* (defaults) | Real measurement on this machine, in a separate worker process. | Finishes in a few seconds; for `identical` workload ratio **2.00×**; upload ≈ 2–3 MiB/s, restore faster (Phase 5 and the Phase 6 UI run: 1.88 MiB/s upload on a 128 KiB × 3 run). Say plainly the chunker is pure Python. |
| 12 | 13–14 | **Simulation** → enter 1000 files × 1,000,000 B, 10 chunks/file, 25 % duplicates | A mathematical model, not a measurement. | Formulas and assumptions shown; 100 % duplicates is metadata-dominated; invalid input gives a clear validation error. |
| 13 | 14–15 | **Trade-offs** / **Docs** | What this does *not* do: no auth, no distribution, single writer. | Honest limits are visible in the UI. |

## Fallbacks

- **Backend badge red / "cannot reach API":** the API is not running, or the UI was opened with a host other than `localhost` (API replies `400 host_not_allowed`). Restart uvicorn; use `http://localhost:5173`.
- **Upload rejected with 403 `origin_not_allowed`:** `CHUNKVAULT_CORS_ORIGINS` does not match the UI's origin; set it to exactly the address in the browser bar.
- **Fault-lab page says disabled:** restart the backend with `CHUNKVAULT_ENABLE_FAULT_LAB=1`.
- **Benchmark slow or stuck:** press *Cancel*; the job ends *cancelled* and its temp store is removed. Use a smaller size.
- **Port busy:** change `--port` and set `CHUNKVAULT_API_URL` before `npm run dev`.
- **No time / no live system:** `cd backend; .venv/Scripts/python ../scripts/verify_phase6.py` runs the whole workflow headless (74 checks, ~1 min) and prints each result; the numbers in the Phase 5 benchmark report (`docs/architecture/benchmarking.md`) are saved evidence.
- **Cleanup after the demo:** stop both servers and delete the demo data directory you created.
