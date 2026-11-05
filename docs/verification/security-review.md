# Security Review (Phase 6)

Scope: the code in this repository as of the Phase 6 working tree, run locally. Method: code review plus
active probing of a running instance on a throw-away store, then regression tests
(`backend/tests/test_security.py`, 53 tests). ChunkVault is a **single-user local prototype with no
authentication**; that is a stated design limit (see "Deployment limitations"), not a defect.

Severity scale: **Critical** remote code execution / data loss by an unauthenticated attacker in the intended
deployment · **High** reading or damaging data across a trust boundary · **Medium** exploitable only with a
user's browser or network position · **Low** information disclosure or hardening · **Info**.

## Vulnerabilities found and fixed

| # | Severity | Finding | Fix | Regression tests |
|---|---|---|---|---|
| F1 | Medium | **Cross-site request forgery.** `POST /api/files` (multipart) and body-less `POST`/`DELETE` are "simple" requests: a web page the user visits could upload, delete or reconcile on `localhost:8000` without a CORS preflight; CORS only blocks *reading* the answer. | `api/guard.py` `LocalAccessGuard`: any non-safe method carrying an `Origin` header outside the allow-list gets `403 origin_not_allowed`. Requests without `Origin` (curl, scripts, tests) and the configured UI origin are unaffected. | `test_security.py` (cross-origin POST/DELETE rejected, allowed origin accepted, GET unaffected) |
| F2 | Medium | **DNS rebinding.** The API answered any `Host` header, so a hostile hostname rebound to 127.0.0.1 would be same-origin to the browser and could read everything. | Same guard: `Host` must be `localhost`, `127.0.0.1` or `[::1]` (`CHUNKVAULT_ALLOWED_HOSTS`, wildcard rejected at start-up) else `400 host_not_allowed`. | `test_security.py` (Host variants, port stripping, IPv6, case) |
| F3 | Low | **Filesystem path leak** in a benchmark worker's error text / log tail (temp directory path shown in UI/API). | `core/errors.scrub_paths` replaces paths with `<path>` in worker errors and log tails. | `test_security.py` |

No Critical or High findings were identified.

## Checked and found sound

- **Injection:** every SQL statement is parameterised; table names for the inspector come from a fixed allow-list (`files`, `chunks`, `file_chunks`, `operations`); row limits are capped at 200. Tests send SQL metacharacters in filenames, ids, hashes and paging parameters.
- **Path traversal:** chunk paths are derived only from a validated 64-hex digest (`ab/<digest>`); uploaded filenames are never used as paths (stored as metadata, sanitised for the download header; control characters and `../` are neutralised). Reserved Windows names are harmless for the same reason.
- **Resource exhaustion:** request bodies are bounded before parsing (`upload_too_large`, `invalid_content_length`); upload size cap (64 MiB default); streaming chunker bounds memory per chunk; benchmark size/iteration/time budgets are validated server-side; one benchmark and one fault experiment at a time; simulation inputs are range-checked and results stay finite.
- **Error handling:** consistent error envelope; no stack traces or absolute paths in responses (see F3); unexpected exceptions return a generic `500`.
- **Fault lab / benchmarks:** the fault lab is **off by default** (`403 fault_lab_disabled`), accepts only a scenario id, and runs on private temp stores; it refuses to run if its store could overlap application data. Benchmarks run in a worker subprocess on a temporary store and never touch stored files.
- **Dependencies / XSS:** no network calls from the backend. The frontend renders Markdown (`marked`) and Mermaid SVG with `dangerouslySetInnerHTML`, but only from documentation bundled at build time; no API-supplied or user-supplied text is rendered as HTML (React escapes everything else). If the viewer were ever pointed at untrusted Markdown it would need a sanitiser; it is not today.
- **Information disclosure:** `/api/docs` and `/api/openapi.json` are served (useful for a demo); the inspector exposes table contents by design (see limitations).

## Deployment limitations (not vulnerabilities in the intended use)

These are true of the design and must be understood before any use beyond a single-user local run:

1. **No authentication or authorisation.** Anyone who can reach the port can upload, download, delete files, run `reconcile`, and start benchmarks. Mitigation: bind to `127.0.0.1` (the default; never `--host 0.0.0.0`), and do not publish the port. The guard in F1/F2 protects against *browsers* on the same machine, not against other local users or processes.
2. **Mutating endpoints are unauthenticated by design**, including `POST /api/integrity/reconcile` (which deletes orphaned chunk files) and `POST /api/benchmarks` (CPU for up to 240 s).
3. **The database inspector exposes stored metadata** (filenames, hashes, operation messages) to any local caller.
4. **No encryption at rest or in transit** (plain HTTP on loopback); no per-user quotas.
5. **Single worker only.** Running several uvicorn workers is unsupported (a file lock guards against interleaved writes but the design assumes one process).
6. **Benchmark cleanup scans the system temp directory** for directories named `chunkvault-bench-*` to remove stale temporary stores; another user's directory with that prefix would be affected on a shared machine.
7. **Restore temp files** left by a client disconnect mid-download are removed by `reconcile` once stale, not immediately.
8. **Durability** relies on `fsync` and SQLite `synchronous=FULL`; Windows directory `fsync` limits are documented in the consistency protocol and were not tested by power-loss.
9. **The development server (Vite)** is for local use; the production build is static files and needs its own origin allow-list entry (`CHUNKVAULT_CORS_ORIGINS`).

## Not done

No penetration test beyond the probes above and no fuzzing campaign. Frontend dependencies were scanned with `npm audit` in Phase 6.1 (results in the final verification report, section G: 2 moderate and 2 low runtime advisories, none exploitable in this application as used; dev-toolchain advisories only affect a local dev/test server). Python packages were not audited because no audit tool is installed.
