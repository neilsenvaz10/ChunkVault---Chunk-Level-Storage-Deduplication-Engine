# ChunkVault — Chunk-Level Storage Deduplication Engine

A System Design micro-project. Files are split into content-defined chunks (Rabin-style CDC), each chunk is identified by SHA-256 and
stored once, and file→chunk mappings are reference-counted. The same application runs in two modes:

| | **Local mode** (default) | **Cloud mode** (public demo) |
|---|---|---|
| Metadata | SQLite | Supabase PostgreSQL |
| Chunk bytes | content-addressed files on the local filesystem | private Supabase Storage bucket |
| Backend | uvicorn on your machine | Render Free web service (one worker) |
| Frontend | Vite dev server (proxies `/api`) | Vercel Hobby static site |
| Authentication | none; bound to `127.0.0.1`, local `Host` headers only | public reads; demo token for writes; admin token for maintenance |
| Selected by | `CHUNKVAULT_BACKEND=local` (or unset) | `CHUNKVAULT_BACKEND=cloud` on the backend, `VITE_CHUNKVAULT_MODE=cloud` on the frontend |

**Status: phases 1-6 implemented** (see `docs/roadmap/phases.md`; final evidence in `docs/verification/`), plus the cloud deployment
(`cloud-migration/`). Upload, Rabin CDC, SHA-256 deduplication, verified restore, reference-counted delete, the integrity audit, explicit
reconciliation and storage statistics work through the API, and the React app exposes them together with architecture diagrams, a chunking
explorer, a database explorer and the rendered project documentation. It is a single-process design (**run uvicorn with one worker**); there
is no distributed functionality.

## Run locally

Backend (from `backend/`; Python 3.11+):

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Linux/macOS: .venv/bin/pip
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

Interactive API docs (local mode only): http://127.0.0.1:8000/api/docs · health: http://127.0.0.1:8000/api/health

Example: `curl -F file=@some.bin http://127.0.0.1:8000/api/files`, then `GET /api/storage/stats`.
End-to-end checks on throw-away stores: `.venv/Scripts/python ../scripts/verify_phase2.py` and `../scripts/verify_phase6.py`.

**Local security:** local mode has no authentication. Keep the default bind (`127.0.0.1`; never pass `--host 0.0.0.0`); the API accepts only
local `Host` headers and rejects cross-site modification requests. See `docs/verification/security-review.md`. **This applies to local mode
only**: cloud mode is authenticated as described below.

Frontend (from `frontend/`; Node 18+):

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api to port 8000
```

## Cloud deployment (Vercel + Render + Supabase, all free tiers)

The files that drive it: `render.yaml` (backend blueprint; Free plan, Singapore, one worker, auto-deploy off), `vercel.json` (build, SPA
rewrite, security headers, CSP) and `.vercelignore`. Step-by-step notes: `cloud-migration/`.

**Roles and access (cloud mode)**

| Role | Can do | Credential |
|---|---|---|
| Public visitor | read: file list, chunks, statistics, quota, health, database explorer, OpenAPI document | none |
| Demo | upload, download/restore, delete, simulations | `CHUNKVAULT_DEMO_TOKEN` (typed into the page; held in memory only, never stored or put in a URL) |
| Admin | integrity audit, reconcile, garbage collection, benchmarks, fault lab | `CHUNKVAULT_ADMIN_TOKEN` (never entered in the public site; the admin pages are hidden there) |

Cross-origin requests are accepted only from the exact origins in `CHUNKVAULT_CORS_ORIGINS`. The interactive Swagger page (`/api/docs`) is
disabled in cloud mode; `/api/openapi.json` stays because the in-app API explorer reads it.

**Backend environment variables (set in the Render dashboard; names only, never commit values)**

| Name | Purpose |
|---|---|
| `CHUNKVAULT_BACKEND` | `cloud` (set by `render.yaml`) |
| `DATABASE_URL` | Supabase session-pooler URL of the **restricted** application role |
| `SUPABASE_URL` | project URL |
| `SUPABASE_SERVICE_KEY` | server-side secret key (never in the frontend) |
| `CHUNKVAULT_DEMO_TOKEN`, `CHUNKVAULT_ADMIN_TOKEN` | random, at least 24 characters, different from each other |
| `CHUNKVAULT_CORS_ORIGINS` | exact `https` origin of the Vercel site, no trailing slash |
| `SUPABASE_BUCKET` | private bucket name (default `chunkvault-chunks`) |

Optional tuning is listed in `.env.example`. `OWNER_DATABASE_URL` (migrations and role setup) belongs on your own machine only and must
never be set on Render. Frontend variables (set in Vercel; public by design): `VITE_CHUNKVAULT_MODE=cloud` and `VITE_API_BASE_URL`
(the backend's `https` address). No secret may ever be a `VITE_*` variable.

**Limits and free-tier behaviour**

* Upload size: 10 MiB per file in cloud mode (64 MiB locally). Stored unique chunk bytes are capped by an application quota of 300 MiB;
  an upload that would exceed it is refused. Uploads have a 90 s deadline.
* Concurrency: at most 2 uploads and 3 restores at once; commits to the metadata are serialised by a single writer lock.
* Abuse limits are in-memory and per process: 10 failed authentications per client address per 5 minutes, then HTTP 429; 30 expensive
  requests per minute. Render's proxy address range is not configured as trusted, so `X-Forwarded-For` is ignored and all visitors share one
  rate-limit bucket (one abuser can temporarily lock out others). The limits reset when the service restarts.
* Render Free sleeps when idle: the first request after a sleep can take up to about a minute, and the page shows a "waking up" notice.
  The disk is ephemeral (only scratch files live there). Supabase Free may pause an inactive project.
* The public site holds synthetic demo data only; anyone with the demo token can upload and delete. Rotate it after a demo.

## Test

```bash
cd backend && .venv/Scripts/python -m pytest    # backend tests (temporary databases)
cd frontend && npm test                          # component and unit tests (vitest, mocked API)
cd frontend && npm run build                     # production build check
```

## Layout

| Path | Contents |
|---|---|
| `backend/app/` | FastAPI app; one package per component (most with an `interfaces.py` or protocol definitions) |
| `backend/app/database/` | `schema.sql`, connection/transaction helpers, initialisation |
| `backend/tests/` | pytest suite |
| `frontend/src/` | React + Vite shell |
| `data/` | runtime chunk store, staging area, benchmark output (contents git-ignored) |
| `docs/` | architecture, database, API, decisions, roadmap, verification (final report, security review, demo guide, traceability) |
| `scripts/` | independent verification scripts |

For a presentation use `docs/verification/demo-guide.md`. Otherwise start with `docs/architecture/hld.md`, then `lld.md`, `docs/database/schema.md`,
`docs/architecture/consistency-protocol.md`, and `docs/decisions/`.
