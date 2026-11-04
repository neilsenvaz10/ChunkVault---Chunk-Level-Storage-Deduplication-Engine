# ADR-0002: SHA-256 for content-addressed storage

**Status:** accepted · **Phase:** 1

## Problem
The store treats "same hash" as "same bytes" and skips the write. That is only safe if accidental or crafted collisions are negligible, and the identifier must also let any stored chunk be verified later.

## Alternatives considered
- **A fast non-cryptographic hash** (for example CRC or xxHash): cheap, but collisions are not negligible.
- **The rolling fingerprint itself** as identity: it is not collision resistant and is only meant to find boundaries.
- **SHA-256** (standard library `hashlib`).

## Decision
Identify every chunk by its lowercase-hex SHA-256. The digest is the file name in the chunk store and the primary key in `chunks`. The same digest verifies integrity on restore and audit. The rolling fingerprint is never used for identity.

## Advantages
- Collision resistant for practical purposes, in the standard library, and doubles as a checksum.
- Identical content always maps to the same address, so deduplication is a lookup.

## Disadvantages
- Slower than non-cryptographic hashes.
- 32 bytes of identity per chunk in metadata.

## Consequences
As defence in depth the deduplication manager also compares sizes and treats a size mismatch for an equal digest as corruption. SHA-256 equality is used as the practical identity mechanism; ChunkVault does not claim collisions are mathematically impossible.

## Scalability implications
Digests partition naturally, so a larger system could shard the chunk index and store by digest prefix. ChunkVault does not implement sharding.
