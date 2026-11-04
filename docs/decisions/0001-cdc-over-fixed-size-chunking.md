# ADR-0001: Rabin-style CDC instead of fixed-size chunking

**Status:** accepted · **Phase:** 1

**Context.** Deduplication quality depends on identical content producing identical chunks.
With fixed-size chunks, inserting or deleting one byte shifts every later boundary, so almost
no later chunk matches its previous version.

**Decision.** Use content-defined chunking: boundaries come from a rolling Rabin-style
fingerprint over a 64-byte window, with min 4 KiB / avg 16 KiB / max 128 KiB.

**Consequences.** + Edits only disturb chunks near the edit. − More CPU than fixed-size; variable-size
chunks; extra metadata per chunk (smaller average chunk = more metadata). Min/max bounds cap
pathological cases. Phase 6 will measure the real trade-off against a fixed-size baseline.
