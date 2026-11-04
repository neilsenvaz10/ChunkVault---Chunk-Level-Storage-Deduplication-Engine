# ADR-0005: Sequential processing instead of distributed workers

**Status:** accepted · **Phase:** 1

**Decision.** One process; chunks of an upload are processed one after another; the backend is a
single FastAPI application with in-process modules, not services.

**Consequences.** + Deterministic, easy to reason about for the consistency protocol, easy to
test. − Throughput limited to one core per upload. Capacity simulation (Phase 7) is an analytical
model only; ChunkVault does not implement or claim distributed behaviour.
