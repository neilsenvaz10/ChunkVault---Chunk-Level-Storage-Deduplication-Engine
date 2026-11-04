# ADR-0005: Sequential processing instead of distributed workers

**Status:** accepted · **Phase:** 1

## Problem
Chunking, deduplication and the two-store commit must stay easy to reason about and to test, on one machine.

## Alternatives considered
- **A worker pool or queue** processing chunks or files concurrently.
- **Microservices** per component.
- **One process with in-process modules and a single-writer lock.**

## Decision
One FastAPI process; the chunks of an upload are processed one after another; mutating operations (upload, delete, reconcile) run under a single-writer lock that combines a thread lock with an OS file lock, so a second server process cannot interleave writes.

## Advantages
- Deterministic and easy to reason about for the consistency protocol.
- A single writer removes whole classes of races.

## Disadvantages
- Throughput is limited to one core and one writer.
- No fault tolerance if the machine is lost.

## Consequences
The server must run with one worker. A caller that cannot obtain the lock within the timeout receives `409 writer_busy`. Restores and audits are read-only and do not take the lock.

## Scalability implications
A larger system could use stateless API workers and concurrency control around a shared chunk index. ChunkVault does not implement or claim any of this.
