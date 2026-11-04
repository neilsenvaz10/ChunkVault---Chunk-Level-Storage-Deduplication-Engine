# ADR-0003: SQLite instead of a distributed database

**Status:** accepted · **Phase:** 1

## Problem
Metadata needs transactions, foreign keys, indexes and triggers. The project is a single-machine, sequential prototype that must not depend on paid or external services.

## Alternatives considered
- **A distributed or networked database** (for example a replicated SQL or key-value store): scalable, but out of scope and adds infrastructure.
- **Flat files or JSON** for metadata: no transactions or constraints.
- **SQLite** in WAL mode.

## Decision
Use SQLite, with foreign keys enforced on every connection, `synchronous=FULL` commits, explicit `BEGIN IMMEDIATE` transactions and constraints/triggers that enforce the reference-count invariant.

## Advantages
- Zero setup, real ACID transactions, constraints and triggers.
- Easy to inspect and to test with temporary databases.

## Disadvantages
- One writer at a time and one file on one machine; no replication or horizontal scale.

## Consequences
The database itself maintains `ref_count` (one per manifest occurrence), so application code cannot forget to. The repository interface isolates SQL, but no distributed migration path is designed.

## Scalability implications
A larger system would need replicated or sharded metadata with a different transaction and consistency model. This is described only conceptually.
