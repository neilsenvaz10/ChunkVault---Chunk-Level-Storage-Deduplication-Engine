# ADR-0004: Local filesystem instead of object storage

**Status:** accepted · **Phase:** 1 (layout revised in Phase 2)

## Problem
Chunk bytes need durable, addressable storage with no external service, and a way to publish a chunk so that readers never see a partial file.

## Alternatives considered
- **Object storage** (cloud or self-hosted): replicated and scalable, but an external dependency.
- **Storing chunks as SQLite BLOBs**: simple, but couples payload to the metadata database.
- **A local content-addressed filesystem store.**

## Decision
Store chunk bytes as files at `data/chunks/ab/<sha256>`, written through `data/staging/` and published with an atomic same-volume rename. One level of 256 directories keeps directories small at prototype scale (revised in Phase 2 from the two-level layout first drafted).

## Advantages
- No external service; files are easy to inspect and to damage in tests.
- Atomic rename gives safe publication.

## Disadvantages
- One disk, no replication.
- One file per chunk means many small files and per-file filesystem overhead.
- Durability across power loss relies on `fsync` before rename (and Windows lacks directory fsync).

## Consequences
Metadata and bytes live in different stores and cannot be committed atomically; see ADR-0007. Chunk paths are derived only from a validated digest.

## Scalability implications
Content addressing makes any key-value blob store a drop-in later; a larger system would use replicated object storage keyed by digest. ChunkVault does not implement that.
