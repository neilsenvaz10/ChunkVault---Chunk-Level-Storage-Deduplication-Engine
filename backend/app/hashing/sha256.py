"""SHA-256 hashing service (stdlib ``hashlib``).

SHA-256 is the content identity and integrity mechanism. It is entirely
separate from the rolling fingerprint in ``chunking/rabin.py``, which only
decides where chunks end.
"""

from __future__ import annotations

import hashlib
import re
from typing import BinaryIO

from app.hashing.interfaces import IncrementalHasher, InvalidDigestError

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_READ_SIZE = 1024 * 1024


def is_valid_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def validate_digest(value: object) -> str:
    """Return ``value`` if it is a lowercase 64-char hex digest, else raise."""
    if not is_valid_digest(value):
        raise InvalidDigestError("expected a lowercase 64-character hex SHA-256 digest")
    return value  # type: ignore[return-value]


class Sha256Service:
    def digest(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def new_hasher(self) -> IncrementalHasher:
        return hashlib.sha256()

    def verify(self, data: bytes, expected_hex: str) -> bool:
        validate_digest(expected_hex)
        return self.digest(data) == expected_hex

    def digest_stream(self, stream: BinaryIO) -> tuple[str, int]:
        """Hash a stream incrementally. Returns (hex digest, byte count)."""
        hasher = hashlib.sha256()
        total = 0
        while block := stream.read(_READ_SIZE):
            hasher.update(block)
            total += len(block)
        return hasher.hexdigest(), total
