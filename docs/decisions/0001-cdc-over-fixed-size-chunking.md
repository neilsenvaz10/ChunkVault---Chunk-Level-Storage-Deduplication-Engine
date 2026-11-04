# ADR-0001: Rabin-style CDC instead of fixed-size chunking

**Status:** accepted · **Phase:** 1

## Problem
Deduplication quality depends on identical content producing identical chunks. With fixed-size chunks, inserting or deleting one byte shifts every later boundary, so almost no later chunk matches its previous version.

## Alternatives considered
- **Fixed-size chunking** (for example every 16 KiB): trivial and fast, but not shift-resistant.
- **Whole-file hashing** (file-level deduplication): only detects identical files.
- **Content-defined chunking** with a rolling fingerprint: boundaries follow the content.

## Decision
Use content-defined chunking: boundaries come from a rolling Rabin-style fingerprint over a 64-byte window, with minimum 4 KiB, target 16 KiB and maximum 128 KiB. A boundary is declared when `fingerprint mod 12288 == 12287`.

## Advantages
- An insertion or deletion only disturbs chunks near the edit; later chunks keep their SHA-256 and are reused.
- Minimum and maximum sizes bound the worst cases.

## Disadvantages
- More CPU than slicing every N bytes (the prototype's chunker is pure Python).
- Variable-size chunks and a minimum/maximum policy to manage.
- No advantage for in-place edits, where both schemes change about one chunk, and no guaranteed advantage on every dataset.

## Consequences
Chunk boundaries are a function of content only, so the chunker is deterministic. The 16 KiB target is a design parameter: measured means vary with the data. Real reuse is measured by the Phase 6 benchmarks and shown live in the chunking demo on generated data.

## Scalability implications
Chunking is independent per file, so a larger system could run it in parallel workers on separate files. ChunkVault itself processes files sequentially and does not implement this.
