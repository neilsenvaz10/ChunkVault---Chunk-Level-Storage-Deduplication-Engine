# Requirements and Assumptions

Derived from the Phase 1–3 project briefs. The original System Design Report and Feasibility
Report were not available in the repository, so this list has **not** been checked against them.

## Functional requirements

| ID | Requirement | Status |
|---|---|---|
| FR1 | Accept a file upload and divide it into variable-sized, content-defined chunks | implemented |
| FR2 | Identify each chunk by its SHA-256 digest | implemented |
| FR3 | Store each distinct chunk exactly once in a local content-addressed store | implemented |
| FR4 | Keep file metadata, ordered chunk manifests and reference counts in SQLite | implemented |
| FR5 | Restore any stored file byte-for-byte, verifying every chunk and the whole file | implemented |
| FR6 | Delete a logical file without affecting chunks shared with other files | implemented |
| FR7 | Detect (read-only) missing, corrupt and orphaned data and inconsistent metadata | implemented |
| FR8 | Repair crash leftovers only through an explicit, reported action | implemented |
| FR9 | Report real storage statistics (logical vs unique payload, savings, ratio) | implemented |
| FR10 | Expose all of the above through a documented HTTP API and an interactive UI | implemented |
| FR11 | Benchmark chunking/deduplication on this machine | planned (Phase 6) |
| FR12 | Analytical capacity model driven by stated assumptions | planned (Phase 7) |

## Non-functional requirements

| ID | Requirement | How it is met |
|---|---|---|
| NFR1 | **Integrity**: a file is never reported complete unless every chunk is stored and mapped | metadata commit last, in one transaction; verified restore |
| NFR2 | **Consistency**: SQLite and the filesystem cannot commit atomically, so every crash state must be detectable and repairable | [consistency protocol](consistency-protocol.md) |
| NFR3 | **Durability (local)**: `fsync` before publication, `synchronous=FULL` commits | documented limits for Windows directory fsync |
| NFR4 | **Determinism**: the same bytes always yield the same chunks | fixed polynomial/tables, no randomness |
| NFR5 | **Bounded resources**: request bodies, memory per chunk and upload size are limited | body-limit middleware, 64 MiB default upload limit, streaming chunker |
| NFR6 | **Testability**: isolated temporary stores for all tests | pytest + vitest suites |
| NFR7 | **Honesty**: unimplemented features are labelled; no fabricated metrics | planned-endpoint registry, UI "not implemented" labels |

## Assumptions and constraints

- Single machine, single user, trusted local environment; no authentication.
- One server process (one uvicorn worker). A file lock additionally stops a second process from interleaving writes.
- Local filesystem with atomic same-volume rename; staging and chunk directories on the same volume.
- SHA-256 is treated as collision-free for practical purposes; a size mismatch for an equal digest is nevertheless treated as corruption.
- Files fit the configured upload limit (default 64 MiB) because the prototype's pure-Python chunker is not designed for large data.
- No Docker, Redis, Kafka, Kubernetes, cloud services or distributed databases.
