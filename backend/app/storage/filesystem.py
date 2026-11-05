"""Local content-addressed chunk store.

Layout::

    <chunks_dir>/<hash[0:2]>/<hash>        published chunk (name == SHA-256 of its bytes)
    <staging_dir>/<hash>.<uuid>.tmp        chunk being written

Durability model (local filesystem, single machine):
* Staged bytes are flushed and ``fsync``ed before publication.
* Publication is ``os.replace`` of a staged file onto its final name: atomic on
  POSIX and on NTFS when staging and chunks are on the same volume (checked at
  construction). Readers never observe a partial chunk.
* After the rename the parent directory is ``fsync``ed where the platform
  allows it (POSIX). On Windows directory fsync is unavailable, so a power
  failure right after publication may lose the rename; the metadata commit
  happens afterwards, so such a loss is caught as a *missing chunk* by the
  integrity audit, never as silent corruption.
* A published chunk is never overwritten. An existing file is verified first.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path

from app.core.errors import ConfigurationError
from app.hashing.interfaces import HashingService
from app.hashing.sha256 import Sha256Service, validate_digest
from app.storage.interfaces import (
    ChunkCorruptedError,
    ChunkNotFoundError,
    ChunkWriteError,
    StagedChunk,
)


class ChunkStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    CORRUPT = "corrupt"


class FilesystemChunkStore:
    def __init__(self, chunks_dir: Path, staging_dir: Path, hasher: HashingService | None = None) -> None:
        self.chunks_dir = Path(chunks_dir)
        self.staging_dir = Path(staging_dir)
        self._hasher = hasher or Sha256Service()
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        if self.chunks_dir.stat().st_dev != self.staging_dir.stat().st_dev:
            raise ConfigurationError("chunks and staging directories must be on the same filesystem")

    # -- paths ---------------------------------------------------------------

    def path_for(self, chunk_hash: str) -> Path:
        """Final location of a chunk. The digest is validated first, so no
        input can contain separators or '..'."""
        validate_digest(chunk_hash)
        return self.chunks_dir / chunk_hash[:2] / chunk_hash

    # -- writing -------------------------------------------------------------

    def stage(self, chunk_hash: str, data: bytes) -> StagedChunk:
        validate_digest(chunk_hash)
        if not self._hasher.verify(data, chunk_hash):
            raise ChunkWriteError("chunk bytes do not match the supplied digest")
        path = self.staging_dir / f"{chunk_hash}.{uuid.uuid4().hex}.tmp"
        try:
            with open(path, "xb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            self._unlink_quiet(path)
            raise ChunkWriteError(f"cannot stage chunk: {exc.strerror or exc}") from exc
        return StagedChunk(hash=chunk_hash, path=path, size_bytes=len(data))

    def publish(self, staged: StagedChunk) -> bool:
        """Move a staged chunk to its address. Returns True if it created the
        chunk, False if a valid chunk already existed. Raises
        ``ChunkCorruptedError`` if the existing file does not match its digest
        (it is NOT overwritten)."""
        target = self.path_for(staged.hash)
        try:
            if target.exists():
                status = self.verify(staged.hash)
                if status is ChunkStatus.CORRUPT:
                    raise ChunkCorruptedError("an existing stored chunk does not match its digest")
                self.discard(staged)
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged.path, target)
            self._fsync_dir(target.parent)
            return True
        except OSError as exc:
            raise ChunkWriteError(f"cannot publish chunk: {exc.strerror or exc}") from exc

    def discard(self, staged: StagedChunk) -> None:
        self._unlink_quiet(staged.path)

    def delete(self, chunk_hash: str) -> bool:
        path = self.path_for(chunk_hash)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ChunkWriteError(f"cannot delete chunk: {exc.strerror or exc}") from exc

    # -- reading -------------------------------------------------------------

    def exists(self, chunk_hash: str) -> bool:
        return self.path_for(chunk_hash).is_file()

    def read(self, chunk_hash: str) -> bytes:
        path = self.path_for(chunk_hash)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise ChunkNotFoundError("chunk file is missing") from None
        except OSError as exc:
            raise ChunkNotFoundError(f"chunk file is unreadable: {exc.strerror or exc}") from exc

    def verify(self, chunk_hash: str, expected_size: int | None = None) -> ChunkStatus:
        """Re-hash the stored bytes. Never raises for missing/corrupt content."""
        try:
            data = self.read(chunk_hash)
        except ChunkNotFoundError:
            return ChunkStatus.MISSING
        if expected_size is not None and len(data) != expected_size:
            return ChunkStatus.CORRUPT
        return ChunkStatus.OK if self._hasher.verify(data, chunk_hash) else ChunkStatus.CORRUPT

    # -- enumeration ---------------------------------------------------------

    def iter_hashes(self) -> Iterator[str]:
        """Published chunk hashes; entries whose name or directory is not a
        valid digest layout are ignored."""
        for sub in sorted(self.chunks_dir.iterdir()):
            if not sub.is_dir() or len(sub.name) != 2:
                continue
            for entry in sorted(sub.iterdir()):
                try:
                    validate_digest(entry.name)
                except Exception:
                    continue
                if entry.name[:2] == sub.name and entry.is_file():
                    yield entry.name

    def iter_staged(self) -> Iterator[Path]:
        for entry in sorted(self.staging_dir.iterdir()):
            if entry.is_file():
                yield entry

    def free_bytes(self) -> int | None:
        """Free bytes on the filesystem(s) holding the chunk store: the chunks directory and the staging directory
        (normally the same volume; if they differ, the smaller figure). ``None`` if the OS cannot say, in which case
        callers skip the advisory check rather than block uploads."""
        try:
            frees = {}
            for d in (self.chunks_dir, self.staging_dir):
                frees[os.stat(d).st_dev] = shutil.disk_usage(d).free
            return min(frees.values())
        except OSError:
            return None

    def disk_usage(self) -> tuple[int, int]:
        """(published chunk file count, total bytes) from file sizes."""
        count = total = 0
        for h in self.iter_hashes():
            try:
                total += self.path_for(h).stat().st_size
                count += 1
            except OSError:
                pass
        return count, total

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _unlink_quiet(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        if os.name == "nt":
            return
        try:
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
