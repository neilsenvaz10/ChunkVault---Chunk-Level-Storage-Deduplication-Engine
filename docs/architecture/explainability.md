# Interactive Explanations: Actual Data vs Illustration

Phase 4 added interactive explanations of the architecture to the React app (System Architecture,
Database Explorer, File Storage and Architecture Trade-offs pages). This document records, for
each view, **what is real** and **what is an educational illustration**, so nothing is mistaken
for a measurement.

| View | Real (read from the backend) | Illustration / static explanation |
|---|---|---|
| Component diagram | Module paths and endpoints name real files and routes in this repository | The component descriptions, rationale and dependency edges are written explanation (they mirror `lld.md`); they are not generated from code |
| Upload / restore lifecycle | When a stored file is selected: its size, whole-file SHA-256, chunk count, chunk hashes and sizes, current reference counts, relative chunk paths, the configured limit and chunking parameters. Upload-time decisions (new / reused / repeated) only for uploads made **in the current browser session**, from that upload's response | The nine / eight stage descriptions. The page states it is **not a recorded execution trace**; no timings or historical decisions are shown, and stepping through performs no upload, restore or delete |
| Chunking explorer | Parameters, boundary rule and polynomial come from `GET /api/system/config`. The CDC-vs-fixed comparison is **computed by the real chunker** via `GET /api/system/chunking-demo` | The schematic byte stream in the concept diagram (bar lengths follow the configured sizes). The comparison runs on **seeded generated data**, not on a stored file, and nothing is stored |
| Consistency protocol | — | Entirely explanatory: the nine steps and four interruption points mirror `consistency-protocol.md` and `upload.py`; they are not observations of a run |
| API explorer | Routes, parameters, request/response fields and status codes are read live from `/api/openapi.json` | Only the "backend component" and "reads / mutates" columns are project annotations (OpenAPI cannot express them); un-annotated endpoints are labelled |
| ADR explorer | The text is parsed from `docs/decisions/*.md` at build time | None (a missing section is shown as "Not recorded in this ADR") |
| Content-addressed view | Manifest, hashes, sizes, reference counts and shared status from the API; paths are the documented layout derived from the digest, **relative to the store root** | — |
| Reference-count walkthrough | The trigger and view definitions are read from `backend/app/database/schema.sql`; the invariant status above it is live | The step-by-step example (files A and B, chunks X and Y) is **illustrative** and labelled as such |

## Safety properties
- No walkthrough calls a mutating endpoint. Frontend tests assert that stepping through the lifecycle and reference-count walkthroughs makes no upload, download, delete or reconcile call.
- `GET /api/system/chunking-demo` is read-only and bounded (input at most 512 KiB, insertion at most 4 KiB, validated parameters). It generates its input from a seed, never reads uploaded data, and writes nothing to the database or chunk store.
- Database inspection remains read-only; no endpoint accepts SQL.
- The API still has no authentication and assumes localhost use.

## About the ADRs
The six ADRs written in Phase 1 were expanded in Phase 4 with Problem, Alternatives considered, Advantages, Disadvantages and Scalability sections. The alternatives listed are the ones implied by each decision's title and rationale, not a record of separate evaluations. ADR-0007 records the staging / atomic publication / single-commit / reconciliation protocol, which was designed in Phase 1 and implemented in Phase 2; it was written retroactively and says so.
