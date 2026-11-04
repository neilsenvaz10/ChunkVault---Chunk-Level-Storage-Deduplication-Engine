# API Contracts

Interactive docs for what is actually served: `/api/docs` and `/api/openapi.json` (FastAPI-generated).

## Implemented

| Method | Path | Response |
|---|---|---|
| GET | `/api/health` | `200 HealthResponse` incl. `max_upload_bytes` so clients can validate before sending (`503` with the same body, `status: "degraded"`, if the database check fails) |
| GET | `/api/planned-endpoints` | `200 PlannedEndpointsResponse` — the still-unimplemented endpoints, each `status: "planned"` |
| POST | `/api/files` | multipart field `file` → `201 UploadResponse` (counts plus `chunk_outcomes`: one `{chunk_index, chunk_hash, chunk_size, decision}` per position with decision `new` / `reused` / `adopted` / `repeated_in_file`; this is a fact about that upload only and is **not persisted**); `413 upload_too_large`; `422` bad/missing file or filename; `409 writer_busy` / `chunk_unavailable` / `chunk_identity_conflict`; `500 upload_failed` / `chunk_corrupt` |
| GET | `/api/files?limit&offset` | `200 FileListResponse` (newest first, all statuses; each item includes `chunk_count`) |
| GET | `/api/files/{file_id}` | `200 FileDetailResponse` (ordered manifest + ref counts); `404 file_not_found`; `422` malformed id |
| GET | `/api/files/{file_id}/download` | verified bytes, headers `Content-Disposition`, `X-ChunkVault-SHA256`; `404 file_not_restorable`; `500 chunk_missing` / `chunk_corrupt` / `file_hash_mismatch` / `manifest_inconsistent` |
| DELETE | `/api/files/{file_id}` | `200 DeleteFileResponse`; repeated delete → `404 file_not_found` |
| GET | `/api/chunks?limit&offset&state` | `200 ChunkListResponse` (metadata only, no paths) |
| GET | `/api/storage/stats` | `200 StorageStatsResponse` |
| POST | `/api/integrity/audit` | optional body `{file_id?, verify_chunk_bytes?}` → `200 IntegrityCheckResponse`; read-only; `404` unknown file |
| POST | `/api/integrity/reconcile` | explicit cleanup → `200 ReconcileResponse` (every action listed, plus a post-repair audit); `409 writer_busy` |

| GET | `/api/chunks/{hash}` | `200 ChunkDetailResponse`: the chunk plus every manifest row referencing it; `404`; `422` malformed hash |
| GET | `/api/system/config` | `200 SystemConfigResponse`: the chunking parameters, boundary rule, polynomial, upload limit and relative chunk-path layout the server uses |
| GET | `/api/system/chunking-demo?seed&size&insert_at&insert_len&fixed_size` | `200 ChunkingDemoResponse`: runs the real chunker and a fixed-size chunker on **seeded generated data** with one insertion and reports chunk boundaries and reuse. Read-only, bounded (size 16 KiB–512 KiB, insertion ≤ 4 KiB), stores nothing; `422 validation_error` / `invalid_parameters` |
| GET | `/api/inspect/schema` | `200 SchemaResponse`: whitelisted tables, columns, primary/foreign keys, row counts, ref-count invariant status |
| GET | `/api/inspect/tables/{table}/rows?limit&offset` | `200 TableRowsResponse` for `files`, `chunks`, `file_chunks` or `operations` only (read-only, paginated, max 200 rows) |

| GET | `/api/reliability/scenarios` | `200 ScenarioListResponse`: the nine built-in fault scenarios, their declared expectations, safety rules and whether the lab is enabled |
| POST | `/api/reliability/experiments` | body `{scenario_id}` → `200` experiment result (steps, expected vs observed, SQLite and filesystem state, recovery actions, invariants, backend-computed `recovery_status`); `403 fault_lab_disabled` unless `CHUNKVAULT_ENABLE_FAULT_LAB=1`; `409` if another experiment runs; `422 unknown_scenario` / validation. Runs only against its own temporary store. |
| GET | `/api/benchmarks/workloads` | `200 WorkloadCatalog`: workloads, comparison cases, limits, methodology |
| POST | `/api/benchmarks` | `BenchmarkRequest {kind, workload?, size_bytes?, iterations, seed, fixed_chunk_bytes?}` → `202 {job_id}`; `409` if one is running; `422 invalid_benchmark` (bounds, budget). Runs in a worker process on a temporary store |
| GET | `/api/benchmarks` | `200`: the running job (if any) and saved result summaries, newest first |
| GET | `/api/benchmarks/{job_id}` | `200 JobStatus {status: running|done|failed|cancelled, progress, result, error}` |
| POST | `/api/benchmarks/{job_id}/cancel` | `200` cancelled; `404` if not running |
| POST | `/api/simulations` | `SimulationRequest` (file count, average size, chunks per file, duplicate %, metadata sizes, growth %, retention, horizon) → `200 SimulationResponse`: outputs, formulas, assumptions, sweeps and growth series. A pure mathematical model: reads and writes nothing |

Run uvicorn with a single worker; mutating endpoints are serialised by a writer lock. Request bodies are bounded before parsing: `POST /api/files` allows `max_upload_bytes` + 1 MiB overhead, everything else 1 MiB; violations return `413 upload_too_large` (or `400 invalid_content_length`).

## Error envelope (all non-2xx)

```json
{"error": {"code": "validation_error", "message": "Request validation failed.", "details": [...]}}
```

Codes: `validation_error` (422), `not_found` (404), `method_not_allowed` (405), `internal_error` (500),
plus domain codes from `ChunkVaultError` subclasses.

## Planned

Nothing is planned any more: `GET /api/planned-endpoints` returns an empty list. The Phase 5 endpoints below are implemented.

## Storage statistics

Savings % = `100 x (logical - unique_payload) / logical` (null if logical = 0); dedup ratio = `logical / unique_payload` (null if unique = 0). `unique_payload` counts chunks referenced by complete files; unreferenced rows awaiting reclamation are reported separately. These are logical payload figures; `database_bytes`, `chunk_store_bytes` (sum of file sizes) and `staging_bytes` show actual footprint without filesystem block overhead.
