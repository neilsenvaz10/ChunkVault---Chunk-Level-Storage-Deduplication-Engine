"""Rabin-style content-defined chunker: interface only (implemented in Phase 2).

Configuration lives in ``app.core.config.CDCConfig``; the algorithm
specification is docs/architecture/cdc-specification.md.

Rolling fingerprints decide WHERE a chunk ends. They are never used as chunk
identity: identity is the SHA-256 computed by the hashing service.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO, Protocol

from app.core.errors import ChunkVaultError


class ChunkingError(ChunkVaultError):
    """Reading the input stream failed while chunking."""

    code = "chunking_error"


@dataclass(frozen=True)
class Chunk:
    index: int  # 0-based position within the file
    offset: int  # byte offset of the first byte within the file
    data: bytes  # len(data) is within [min_chunk_size, max_chunk_size], except the final chunk

    @property
    def size(self) -> int:
        return len(self.data)


class Chunker(Protocol):
    def chunk_stream(self, stream: BinaryIO) -> Iterator[Chunk]:
        """Yield consecutive chunks covering ``stream`` exactly once, in order.

        Inputs : a readable binary stream positioned at the start of the file.
        Outputs: ``Chunk`` objects; concatenating ``data`` reproduces the input.
        Raises : ``ChunkingError`` if the stream cannot be read (I/O error).
        Failure handling: emits no partial state; the caller discards any
        chunks it already consumed (the upload is rolled back by the orchestrator).
        An empty stream yields no chunks. Memory use is bounded by max_chunk_size.
        """
        ...
