# ADR-0002: SHA-256 for content-addressed storage

**Status:** accepted · **Phase:** 1

**Context.** The store treats "same hash" as "same bytes" and skips the write. That is only safe
if accidental or crafted collisions are negligible.

**Decision.** Identify every chunk by its SHA-256 (hex). The hash is the filename in the chunk
store and the primary key in `chunks`. The same digest verifies integrity on restore and audit.

**Consequences.** + Collision-resistant, in the standard library, doubles as a checksum.
− Slower than non-cryptographic hashes; 32 bytes of identity per chunk. As defence in depth,
the dedup manager also compares sizes and treats a size mismatch as corruption. The rolling
fingerprint is *not* used for identity.
