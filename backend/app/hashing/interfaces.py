"""SHA-256 hashing service: interface (implemented in hashing/sha256.py).

SHA-256 provides content identification (the chunk's address) and integrity
verification. It is unrelated to the rolling fingerprint used for boundaries.
"""

from __future__ import annotations

from typing import Protocol

from app.core.errors import ChunkVaultError


class InvalidDigestError(ChunkVaultError):
    """A value expected to be a lowercase 64-char hex SHA-256 digest is not."""

    code = "invalid_digest"
    status_code = 422


class IncrementalHasher(Protocol):
    def update(self, data: bytes) -> None: ...

    def hexdigest(self) -> str: ...


class HashingService(Protocol):
    def digest(self, data: bytes) -> str:
        """Return lowercase hex SHA-256 of ``data``. Pure; never raises for bytes input."""
        ...

    def new_hasher(self) -> IncrementalHasher:
        """Streaming hasher used for whole-file SHA-256 while chunks are produced."""
        ...

    def verify(self, data: bytes, expected_hex: str) -> bool:
        """True iff ``digest(data) == expected_hex``.

        Raises ``InvalidDigestError`` if ``expected_hex`` is malformed (a
        malformed expectation is a caller bug, not a mismatch).
        """
        ...
