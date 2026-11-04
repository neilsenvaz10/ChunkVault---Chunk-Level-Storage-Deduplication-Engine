# Reliability Engineering

This document describes how ChunkVault behaves when operations fail, how that behaviour is tested
(the **fault lab**), and what is *not* guaranteed. All results below come from real executions of the
real code (see [How to run](#how-to-run)); none are assumed.

## Coordination of SQLite and the filesystem
SQLite transactions cannot atomically commit filesystem writes ([consistency-protocol.md](consistency-protocol.md)).
The upload therefore orders effects so that every interruption leaves either a clean rollback or a
*detectable* leftover:

1. stage the upload (filesystem) → **T1** (SQLite: file `uploading`, operation `running`)
2. stage + `fsync` + atomically publish each new chunk (filesystem, bytes first)
3. **T2** (SQLite, one transaction): chunk rows, ordered manifest, reference-count triggers, file `complete`
4. remove the staged upload (filesystem)

Deletion reverses the order: metadata commits first, chunk files are unlinked afterwards. The audit
is read-only; repair (`reconcile`) is a separate, explicit, reported action that never deletes a
chunk any manifest references.

## Fault scenarios (executed by the lab)
Each scenario runs against its own temporary store that already holds a committed *control file*
(and, for most, a *victim* file that shares most chunks with it). The fault is injected at a real
point of the real code; `crash` means the process dies (the engine's own cleanup does not run),
`exception` means an ordinary error that the engine handles itself.

| Scenario | Kind | Fault point | State observed after the fault | Recovery performed |
|---|---|---|---|---|
| Failure during file upload | crash | while receiving the body | no metadata; 1 staging file | delete staging file |
| Failure during CDC chunk generation | exception | chunker raises after its 3rd chunk | file `failed`; staging and chunks already cleaned by the engine | none needed |
| Failure after chunk creation, before metadata commit | crash | all chunks published, before T2 | file `uploading`, operation `running`, 1 orphan chunk, 1 staging file; no manifest rows visible | delete staging, abort operation, fail file, delete orphan chunk |
| Failure during metadata persistence | exception | inside T2 after chunk rows were written | T2 rolled back (no manifest rows, no orphans, counts unchanged); file `failed` | none needed |
| Failure after commit, before the response | crash | right after COMMIT | file `complete` and restorable; 1 staging file; a client retry creates a **second** record (0 new chunks written) | delete staging file |
| Failure during restoration | exception | a stored chunk damaged (1 bit flipped) | restore refuses (`chunk_corrupt`), no partial output; audit reports 1 corrupt chunk | **none possible**: reported, not repaired |
| Failure during deletion (before commit) | exception | inside the delete transaction | transaction rolled back: file intact and restorable | none needed |
| Failure during deletion (cleanup interrupted) | crash | after the delete committed, while unlinking | metadata consistent; 1 orphan chunk file | delete orphan chunk |
| Interruption during recovery | crash | reconcile dies after deleting 1 of 8 orphans | 7 orphans remain (progress was made) | re-run deletes the other 7; a third run does nothing |

The scenario "failure during file upload" is adapted: ChunkVault receives the body into staging
before any metadata exists, so the earliest failure leaves only a staging file. "Failure during CDC
chunk generation" is an `exception` because chunk generation runs inside the engine's guarded
section. "Failure after commit" is a crash at a named checkpoint (`after_commit`) placed between the
commit and the staged-file cleanup.

## Invariants checked after every experiment
| # | Invariant | Result across the 9 scenarios |
|---|---|---|
| I1 | Every committed file references valid chunks | holds in 8; **violated in the restore-failure scenario** (a damaged chunk is detected, not repairable) |
| I2 | Chunk hashes match stored content | holds in 8; **violated in the restore-failure scenario** |
| I3 | Reference counts match actual references | holds in all 9 |
| I4 | Failed uploads leave no file that *looks* complete | holds in all 9, **but** `failed` rows stay listed (visible, not restorable) |
| I5 | Restoration never silently returns wrong content | holds in all 9 (the damaged file is refused) |
| I6 | Recovery does not delete chunks required by committed files | holds in all 9 (the control file always restores byte-identically) |
| I7 | Repeated recovery is safe | holds in all 9 (second run: 0 actions, state unchanged) |
| I8 | A failed operation leaves no inconsistent metadata | holds in all 9 |

The lab computes a **recovery status** itself and the UI shows only that value:
`verified_clean` (audit clean and all invariants hold), `verified_with_unrepairable_damage`
(leftovers cleaned, but damage is only detected) or `not_recovered`.

## Known recovery limitations (reported, not hidden)
- **Single copy.** A damaged or missing referenced chunk cannot be repaired. Reconcile leaves it and the audit keeps reporting it (I1/I2 violated). There is no replication or backup tooling.
- **Lost responses duplicate records.** Upload is not idempotent: after "failure after commit" a retry creates a second logical file (the chunks are reused, so no payload is added).
- **Failed rows remain listed.** A failed upload leaves a `failed` row (not restorable) until deleted.
- **Deletion of a damaged file's unique chunks** is only possible by deleting the file; there is no automatic quarantine.
- **Durability** is bounded by `fsync` before publication and `synchronous=FULL` commits. Windows has no directory `fsync`, so a power loss right after a rename could lose a chunk file; the audit would then report it as missing. Power-loss behaviour was **not** tested (the lab simulates process death, not power failure or disk faults).
- The lab injects at named points; it does not enumerate every possible interleaving, and it does not test concurrent writers (those are excluded by the single-writer lock).
- Nothing here is a claim of crash-proof durability or distributed fault tolerance.

## Isolation and safety of the lab
See [ADR-0008](../decisions/0008-isolated-experiment-subsystems.md). Experiments: use their own temporary store (removed afterwards, including when the experiment itself crashes); refuse to run if that store could overlap the application's data; are **disabled unless `CHUNKVAULT_ENABLE_FAULT_LAB=1`**; only accept one of nine built-in scenario ids (no custom faults); run one at a time; and take about 0.2–0.3 s each.

## How to run
```bash
# enable the lab and start the backend (single worker)
CHUNKVAULT_ENABLE_FAULT_LAB=1 python -m uvicorn app.main:app --port 8000   # PowerShell: $env:CHUNKVAULT_ENABLE_FAULT_LAB=1
# UI: System Architecture -> "Fault injection & recovery"
# API: POST /api/reliability/experiments  {"scenario_id": "crash_before_metadata_commit"}
# tests (no environment variable needed): cd backend && python -m pytest tests/test_faultlab.py
```

Source: `backend/app/reliability/faultlab.py`. The only production-code change made for it is a no-op
`Uploader.checkpoint` hook (a seam that raises at a named step when a test or the lab replaces it).
