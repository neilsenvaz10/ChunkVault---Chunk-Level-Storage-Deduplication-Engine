from __future__ import annotations

import sqlite3

from app.database.repository import MetadataRepository
from app.database.states import ChunkState
from app.deduplication.interfaces import (
    ChunkDecision,
    ChunkIdentityConflictError,
    ChunkUnavailableError,
)
from app.storage.filesystem import ChunkStatus, FilesystemChunkStore
from app.storage.interfaces import ChunkCorruptedError


class Deduplicator:
    """Decides whether a chunk's bytes already exist. Metadata alone is never
    trusted: a 'stored' row is only reused after its bytes are re-hashed."""

    def __init__(self, repo: MetadataRepository, store: FilesystemChunkStore) -> None:
        self._repo = repo
        self._store = store

    def decide(self, conn: sqlite3.Connection, chunk_hash: str, size_bytes: int) -> ChunkDecision:
        row = self._repo.get_chunk(conn, chunk_hash)
        status = self._store.verify(chunk_hash, expected_size=None)
        if row is not None:
            if row.size_bytes != size_bytes:
                raise ChunkIdentityConflictError("an existing chunk has the same digest but a different size")
            if row.state is not ChunkState.STORED:
                raise ChunkUnavailableError(
                    f"existing chunk {chunk_hash[:12]}… is in state '{row.state.value}'; "
                    "run an integrity audit"
                )
            if status is ChunkStatus.OK:
                return ChunkDecision.DUPLICATE
            raise ChunkUnavailableError(
                f"existing chunk {chunk_hash[:12]}… is {status.value} on disk; run an integrity audit"
            )
        # No metadata row: bytes may be an orphan left by an interrupted upload.
        if status is ChunkStatus.MISSING:
            return ChunkDecision.NEW
        if status is ChunkStatus.OK:
            return ChunkDecision.ADOPT
        raise ChunkCorruptedError(
            f"an unreferenced file named {chunk_hash[:12]}… does not match its digest; "
            "run reconciliation to remove it"
        )
