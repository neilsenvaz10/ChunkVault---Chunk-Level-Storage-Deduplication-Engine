# ADR-0006: Separate metadata from chunk bytes

**Status:** accepted · **Phase:** 1

**Decision.** SQLite holds files, chunk records, ordered file→chunk mappings and operations; chunk
bytes live only in the content-addressed filesystem store. The SHA-256 is the sole link.

**Consequences.** + Dedup lookups, reference counting and listings never read chunk data; each
store uses the tool suited to it. − The two stores cannot be committed atomically. This is handled
by the staging → atomic publish → metadata commit → reconciliation protocol in
[consistency-protocol.md](../architecture/consistency-protocol.md).
