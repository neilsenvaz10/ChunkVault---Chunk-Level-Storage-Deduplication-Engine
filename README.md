# ChunkVault — Chunk-Level Storage Deduplication Engine

A System Design micro-project: a single-machine prototype that splits files into
content-defined chunks (Rabin-style CDC), identifies them by SHA-256, stores each
unique chunk once in a content-addressed local store, and keeps file→chunk mappings in SQLite.

**Status: Phase 2 — backend storage engine.** Upload, Rabin CDC, SHA-256 deduplication,
the content-addressed chunk store, verified restore, reference-counted delete, the read-only
integrity audit, explicit reconciliation and storage statistics work through the API.
Benchmarking and capacity simulation are still interface-only, and the React app is still the
Phase 1 shell (no upload UI). It is a sequential, single-process prototype (**run uvicorn with
one worker**); there is no distributed functionality.

## Run

Backend (from `backend/`; Python 3.11+):

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Linux/macOS: .venv/bin/pip
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

API docs: http://127.0.0.1:8000/docs · health: http://127.0.0.1:8000/api/health

Example: `curl -F file=@some.bin http://127.0.0.1:8000/api/files`, then `GET /api/storage/stats`.
End-to-end check on a throw-away store: `.venv/Scripts/python ../scripts/verify_phase2.py`.

Frontend (from `frontend/`; Node 18+):

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api to port 8000
```

## Test

```bash
cd backend && .venv/Scripts/python -m pytest    # backend tests (temporary databases)
cd frontend && npm run build                     # production build check
```

## Layout

| Path | Contents |
|---|---|
| `backend/app/` | FastAPI app; one package per component, each with an `interfaces.py` |
| `backend/app/database/` | `schema.sql`, connection/transaction helpers, initialisation |
| `backend/tests/` | pytest suite |
| `frontend/src/` | React + Vite shell |
| `data/` | runtime chunk store, staging area, benchmark output (contents git-ignored) |
| `docs/` | architecture, database, API, decisions, roadmap |

Start with `docs/architecture/hld.md`, then `lld.md`, `docs/database/schema.md`,
`docs/architecture/consistency-protocol.md`, and `docs/decisions/`.
