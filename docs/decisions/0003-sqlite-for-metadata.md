# ADR-0003: SQLite instead of a distributed database

**Status:** accepted · **Phase:** 1

**Context.** Metadata needs transactions, foreign keys and indexes. The project is a
single-machine, sequential prototype with no paid services.

**Decision.** Use SQLite (WAL mode, foreign keys enforced per connection).

**Consequences.** + Zero setup, ACID, constraints and triggers enforce invariants. − One writer
at a time and no horizontal scale; this is a stated limitation, not something the project claims
to solve. The repository interface isolates SQL, but no distributed migration path is designed.
