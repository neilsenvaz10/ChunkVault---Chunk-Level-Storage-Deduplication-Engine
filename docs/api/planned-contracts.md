# API Contracts

Interactive docs for what is actually served: `/docs` and `/openapi.json` (FastAPI-generated).

## Implemented (Phase 1)

| Method | Path | Response |
|---|---|---|
| GET | `/api/health` | `200 HealthResponse` (`503` with the same body, `status: "degraded"`, if the database check fails) |
| GET | `/api/planned-endpoints` | `200 PlannedEndpointsResponse` — the table below, each entry `status: "planned"` |

## Error envelope (all non-2xx)

```json
{"error": {"code": "validation_error", "message": "Request validation failed.", "details": [...]}}
```

Codes: `validation_error` (422), `not_found` (404), `method_not_allowed` (405), `internal_error` (500),
plus domain codes from `ChunkVaultError` subclasses.

## Planned — NOT implemented, NOT registered (requests return 404)

Schemas exist in `backend/app/schemas/` (`files.py`, `operations.py`) but are not wired to routes.

| Method | Path | Request | Response | Phase |
|---|---|---|---|---|
| POST | `/api/files` | multipart file | `201 UploadResponse` | 3 |
| GET | `/api/files` | — | `FileListResponse` | 3 |
| GET | `/api/files/{file_id}` | — | `FileDetailResponse` (chunk list, ref counts) | 3 |
| GET | `/api/files/{file_id}/content` | — | streamed bytes; `Content-Disposition`, `X-ChunkVault-SHA256` | 4 |
| DELETE | `/api/files/{file_id}` | — | `DeleteFileResponse` | 4 |
| POST | `/api/integrity/checks` | `IntegrityCheckRequest` | `IntegrityCheckResponse` | 5 |
| POST | `/api/benchmarks` | `BenchmarkRequest` | `BenchmarkResponse` | 6 |
| POST | `/api/simulations` | `SimulationRequest` | `SimulationResponse` | 7 |

Planned error mapping: unknown file → 404 `file_not_restorable`/`file_not_found`; missing or
corrupt chunk during restore → 500 `chunk_missing`/`chunk_corrupt`; identity conflict → 409;
invalid input → 422.
