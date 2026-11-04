# ADR-0007: Staging, atomic publication, single metadata commit and explicit reconciliation

**Status:** accepted (recorded retroactively: the protocol was designed in Phase 1 in `consistency-protocol.md` and implemented in Phase 2; this ADR records that decision) · **Phase:** 1–2

## Problem
A SQLite transaction cannot atomically commit filesystem writes. A crash can leave chunk bytes with no metadata, or metadata that points at bytes that never reached disk. A file must never be reported complete unless every chunk is stored and mapped.

## Alternatives considered
- **Metadata first, bytes later**: a crash leaves metadata pointing at missing bytes, which breaks restores.
- **Chunk bytes inside SQLite**: one transaction, but couples payload to the database (see ADR-0004 and ADR-0006).
- **Bytes first, metadata last in one transaction, with detection and explicit cleanup.**

## Decision
1. Stream the upload into staging, then write new chunks to staging, `fsync` and atomically publish them to their content address (bytes first).
2. Insert the file row as `uploading`, then commit new chunk rows, the ordered manifest and `complete` status in one SQLite transaction (reference counts are maintained by triggers).
3. Deletion reverses the order: metadata first, bytes after commit.
4. A read-only audit detects every divergence; a separate, explicit, reported reconciliation removes crash leftovers and never deletes a chunk that any manifest references.

## Advantages
- Every interruption point leaves a state that is either a clean rollback or a detectable leftover; a `complete` file always has all its chunks published.
- Ordinary uploads never overwrite or silently repair a damaged chunk.

## Disadvantages
- More moving parts than a single store; leftovers (orphan chunk files, stale staging files) can exist until reconciled.
- Damaged referenced chunks are detected but cannot be repaired (single copy).
- Durability is bounded by `fsync` and platform behaviour (no directory fsync on Windows).

## Consequences
Retrying an interrupted upload restarts it and reuses valid published chunks; there is no resumable upload. The design does not claim crash-proof durability or distributed fault tolerance.

## Scalability implications
The single-writer lock serialises mutations. A larger system would need a different protocol (for example replicated writes and a commit log); ChunkVault does not implement one.
