# ADR-0006: Separate metadata from chunk bytes

**Status:** accepted · **Phase:** 1

## Problem
Deduplication lookups, reference counting and file listings must be fast and transactional, while chunk payloads are large and opaque.

## Alternatives considered
- **Everything in SQLite** (chunks as BLOBs).
- **Everything on the filesystem** (manifests and counters as files).
- **Metadata in SQLite, payload in the filesystem.**

## Decision
SQLite holds files, chunk records, ordered file-to-chunk manifests and operations; chunk bytes live only in the content-addressed filesystem store. The SHA-256 is the sole link between them.

## Advantages
- Lookups, reference counting and listings never read chunk data.
- Each store uses the tool suited to it.

## Disadvantages
- The two stores cannot be committed atomically (see ADR-0007).

## Consequences
Every crash state must be detectable, which drives the audit and reconciliation design.

## Scalability implications
Metadata and payload can scale independently in a larger system (sharded metadata, replicated object storage). ChunkVault does not implement this.
