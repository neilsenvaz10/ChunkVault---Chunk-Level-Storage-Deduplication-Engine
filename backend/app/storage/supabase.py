"""Supabase Storage implementation of ``ChunkStore`` (private bucket, server-side service key).

Scope (subphase C3): storage PRIMITIVES only. Nothing here decides when a chunk may be published or deleted; the upload, restore,
delete and recovery workflows (C4) and the metadata-last commit order stay in their orchestrators, which call these primitives.

Object model
    One immutable object per chunk at ``<hash[0:2]>/<hash>`` (the same layout as the local store). The key is built only from a
    validated lowercase 64-hex SHA-256, so no input can contain ``/``, ``..`` or other path material. Objects are never updated:
    uploads send ``x-upsert: false`` and an existing object is never overwritten.

Writes (``publish``)
    * 200 -> created. A duplicate-object answer (409, or the 400/"Duplicate" form some Storage versions send) -> the existing
      object is downloaded and its SHA-256 checked: valid -> "already existed" (False); invalid -> ``ChunkCorruptedError``, and
      nothing is overwritten.
    * Network failures, timeouts, 429 and 5xx are retried a bounded number of times (exponential backoff with jitter, within one
      total deadline). Such an attempt has an AMBIGUOUS outcome (the server may have stored the object before the response was
      lost). If a later attempt finds the object present and valid, the call reports True ("created") so the caller's failure
      cleanup stays conservative; if the outcome cannot be established the call raises ``ChunkStoreUnavailableError`` with
      ``details={"outcome": "unknown"}``. Retrying is safe because of ``x-upsert: false``.
    * 401/403 (and invalid-JWT answers) -> ``ChunkStoreAuthError`` immediately, never retried.

Reads (``read``)
    The body is size-capped while streaming and its SHA-256 is verified against the requested hash BEFORE it is returned;
    a mismatch raises ``ChunkCorruptedError``. Missing -> ``ChunkNotFoundError``.

Deletes (``delete``)
    Exactly one validated object key per call, through the bulk-delete endpoint so the answer says whether anything was removed
    (False = already absent; idempotent). Never a prefix, never a listing-driven loop; nothing in this module calls it on its own.

Secrets
    The service key lives only in the client's default headers. It is never logged, never part of an exception message, repr or
    ``describe``; response BODIES are never logged or copied into errors (only status codes and exception class names are).
    Redirects are not followed, so the Authorization header can never be forwarded to another host.

Provider behaviours marked "verify" below are assumptions to confirm against the real service in C5: the exact duplicate response,
the missing-object response of the download endpoint, and the response shape of list and bulk delete.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import CloudSettings
from app.hashing.sha256 import Sha256Service, validate_digest
from app.storage.filesystem import ChunkStatus
from app.storage.interfaces import (
    ChunkCorruptedError,
    ChunkNotFoundError,
    ChunkStoreAuthError,
    ChunkStoreError,
    ChunkStoreUnavailableError,
    ChunkWriteError,
    StagedChunk,
)

logger = logging.getLogger("chunkvault.storage")

_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_LIST_PAGE = 1000
_MAX_LIST_PAGES = 1000  # a hard stop for pagination loops (1,000,000 objects per prefix is far beyond this project's quota)
_ERROR_BODY_LIMIT = 4096


@dataclass(frozen=True)
class CloudStagedChunk(StagedChunk):
    """Staging for the cloud store is in memory: there is no file to leave behind, so nothing needs reconciling."""

    data: bytes = field(default=b"", repr=False, compare=False)


@dataclass(frozen=True)
class StorageConfig:
    base_url: str
    bucket: str
    connect_timeout: float = 5.0
    timeout: float = 20.0
    max_attempts: int = 3
    deadline: float = 60.0
    max_object_bytes: int = 8 * 1024 * 1024
    backoff_base: float = 0.25
    backoff_cap: float = 4.0

    @classmethod
    def from_cloud(cls, c: CloudSettings) -> "StorageConfig":
        return cls(base_url=c.supabase_url.rstrip("/"), bucket=c.bucket, connect_timeout=c.storage_connect_timeout_seconds,
                   timeout=c.storage_timeout_seconds, max_attempts=c.storage_max_attempts, deadline=c.storage_deadline_seconds,
                   max_object_bytes=c.storage_max_object_bytes)


@dataclass(frozen=True)
class _Resp:
    status: int
    body: bytes  # capped; used only for classification and for downloads, never logged


class _Transient(Exception):
    """Internal: a request failed in a way worth retrying. ``reason`` is a status or an exception class name (no secrets)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _TooLarge(Exception):
    pass


def object_key(chunk_hash: str) -> str:
    """``ab/abcdef...`` for a validated digest; raises ``InvalidDigestError`` for anything else (no path material can pass)."""
    validate_digest(chunk_hash)
    return f"{chunk_hash[:2]}/{chunk_hash}"


class SupabaseChunkStore:
    """Immutable, content-addressed chunk objects in a private Supabase Storage bucket."""

    #: Orchestrators branch on these capabilities instead of on the concrete class.
    remote = True  # enumeration/usage are expensive: callers use metadata, not listings, on request paths
    deletes_on_failed_upload = False  # legacy flag for the local upload path; the cloud pipeline cleans up its own reservations itself

    def __init__(self, config: StorageConfig, service_key: str, *, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, rng: Callable[[], float] = random.random,
                 clock: Callable[[], float] = time.monotonic, staging_dir: Path | None = None, io_workers: int = 4) -> None:
        parts = urlsplit(config.base_url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise ValueError("a Supabase URL is required")
        if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("an http Supabase URL is only allowed for localhost")
        self._cfg = config
        self._inflight_deletes: dict[str, int] = {}  # hash -> delete() calls still running (abandoned worker threads included)
        self._inflight_lock = threading.Lock()
        self._hasher = Sha256Service()
        self._sleep, self._rng, self._clock = sleep, rng, clock
        self.io_workers = max(1, int(io_workers))
        # Local SCRATCH only (the upload being received, a restored file being sent): never chunk or metadata persistence.
        self.staging_dir = Path(staging_dir) if staging_dir is not None else None
        if self.staging_dir is not None:
            self.staging_dir.mkdir(parents=True, exist_ok=True)
        self._root = f"{config.base_url}/storage/v1"
        self._bucket = quote(config.bucket, safe="")
        self._client = httpx.Client(
            transport=transport, follow_redirects=False,  # never forward the key to another host
            headers={"Authorization": f"Bearer {service_key}", "apikey": service_key, "User-Agent": "chunkvault-storage/1"},
            timeout=httpx.Timeout(connect=config.connect_timeout, read=config.timeout, write=config.timeout, pool=config.connect_timeout),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))

    @classmethod
    def from_settings(cls, cloud: CloudSettings, **kw) -> "SupabaseChunkStore":
        return cls(StorageConfig.from_cloud(cloud), cloud.supabase_service_key, io_workers=cloud.io_workers, **kw)

    def close(self) -> None:
        self._client.close()

    def __repr__(self) -> str:  # never includes headers
        return f"SupabaseChunkStore(bucket={self._cfg.bucket!r})"

    def describe(self) -> dict[str, object]:
        return {"bucket": self._cfg.bucket, "host": urlsplit(self._cfg.base_url).hostname, "max_attempts": self._cfg.max_attempts,
                "timeout_seconds": self._cfg.timeout, "deadline_seconds": self._cfg.deadline}

    # ------------------------------------------------------------------------------------------------ transport

    def _url(self, kind: str, key: str | None = None) -> str:
        tail = f"/{quote(key, safe='/')}" if key else ""
        return f"{self._root}/{kind}/{self._bucket}{tail}" if kind != "object/list" else f"{self._root}/object/list/{self._bucket}"

    def _once(self, method: str, url: str, *, content: bytes | None, json_body, headers: dict | None, max_bytes: int) -> _Resp:
        try:
            with self._client.stream(method, url, content=content, json=json_body, headers=headers) as r:
                declared = r.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise _TooLarge()
                chunks, size = [], 0
                for part in r.iter_bytes():
                    size += len(part)
                    if size > max_bytes:
                        raise _TooLarge()
                    chunks.append(part)
                return _Resp(r.status_code, b"".join(chunks))
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise _Transient(type(exc).__name__) from None  # the message of a transport error is not forwarded

    def _request(self, op: str, method: str, url: str, *, content: bytes | None = None, json_body=None,
                 headers: dict | None = None, max_bytes: int | None = None, attempts: int | None = None) -> tuple[_Resp, bool]:
        """One logical request with bounded retries. Returns (response, an_earlier_attempt_was_ambiguous).

        Raises ``ChunkStoreUnavailableError`` when the attempts or the total deadline are used up, ``ChunkStoreAuthError`` for
        rejected credentials. Any other status is returned for the caller to interpret.
        """
        limit = max_bytes if max_bytes is not None else _ERROR_BODY_LIMIT
        started = self._clock()
        ambiguous = False
        reason = "unknown"
        limit_attempts = attempts or self._cfg.max_attempts
        for attempt in range(1, limit_attempts + 1):
            try:
                resp = self._once(method, url, content=content, json_body=json_body, headers=headers, max_bytes=limit)
            except _TooLarge:
                raise ChunkStoreError("the storage service returned an unexpectedly large response") from None
            except _Transient as exc:
                reason, ambiguous = exc.reason, True
            else:
                if resp.status in (401, 403) or self._looks_like_auth_failure(resp):
                    raise ChunkStoreAuthError("the storage service rejected the server's credentials")
                if resp.status not in _RETRY_STATUSES and resp.status < 500:
                    return resp, ambiguous
                reason, ambiguous = f"HTTP {resp.status}", True
            remaining = self._cfg.deadline - (self._clock() - started)
            if attempt >= limit_attempts or remaining <= 0:
                break
            delay = min(self._cfg.backoff_cap, self._cfg.backoff_base * (2 ** (attempt - 1)))
            delay = min(delay * (0.5 + self._rng() / 2), remaining)
            logger.warning("storage %s attempt %d/%d failed (%s); retrying in %.2fs", op, attempt, limit_attempts, reason, delay)
            self._sleep(delay)
            if self._clock() - started >= self._cfg.deadline:
                break  # the budget ran out while backing off: no further attempt
        logger.warning("storage %s failed after %d attempt(s) (%s)", op, attempt, reason)
        raise ChunkStoreUnavailableError(f"the chunk store is unavailable ({op} failed after {attempt} attempt(s))",
                                         details={"operation": op, "attempts": attempt, "ambiguous": ambiguous})

    @staticmethod
    def _error_fields(resp: _Resp) -> tuple[str, str, str]:
        """(statusCode, error, message) from a Supabase error body, lower-cased; empty strings when absent or not JSON."""
        try:
            data = json.loads(resp.body[:_ERROR_BODY_LIMIT] or b"{}")
        except ValueError:
            return "", "", ""
        if not isinstance(data, dict):
            return "", "", ""
        return tuple(str(data.get(k, "")).lower() for k in ("statusCode", "error", "message"))  # type: ignore[return-value]

    def _looks_like_auth_failure(self, resp: _Resp) -> bool:
        if resp.status != 400:
            return False
        _, error, message = self._error_fields(resp)
        return "jwt" in message or "jws" in message or "signature" in message or error in ("unauthorized", "invalid_jwt")

    def _is_missing(self, resp: _Resp) -> bool:  # verify: some Storage versions answer a missing object with 400 + statusCode 404
        if resp.status == 404:
            return True
        code, error, message = self._error_fields(resp)
        return resp.status == 400 and (code == "404" or error == "not_found" or "not found" in message)

    def _is_duplicate(self, resp: _Resp) -> bool:  # verify: 409, or 400 with statusCode 409 / "Duplicate"
        if resp.status == 409:
            return True
        code, error, message = self._error_fields(resp)
        return resp.status == 400 and (code == "409" or error == "duplicate" or "already exists" in message)

    # ------------------------------------------------------------------------------------------------ writing

    def stage(self, chunk_hash: str, data: bytes) -> CloudStagedChunk:
        validate_digest(chunk_hash)
        if len(data) == 0 or len(data) > self._cfg.max_object_bytes:
            raise ChunkWriteError("chunk size is outside the allowed range")
        if not self._hasher.verify(data, chunk_hash):
            raise ChunkWriteError("chunk bytes do not match the supplied digest")
        return CloudStagedChunk(hash=chunk_hash, path=Path("cloud-staged") / chunk_hash, size_bytes=len(data), data=bytes(data))

    def discard(self, staged: StagedChunk) -> None:
        """Nothing to remove: cloud staging is memory only."""

    def publish(self, staged: StagedChunk) -> bool:
        """Create the object if it does not exist. True = this call (probably) created it, False = a valid object already existed.

        Never overwrites. Raises ``ChunkCorruptedError`` if an existing object does not match its digest, ``ChunkWriteError`` if the
        service refuses the object, ``ChunkStoreUnavailableError`` (``outcome: unknown`` when the result could not be established)
        and ``ChunkStoreAuthError``.
        """
        if not isinstance(staged, CloudStagedChunk):
            raise ChunkWriteError("staged chunk does not belong to this store")
        key = object_key(staged.hash)
        data = staged.data
        if not self._hasher.verify(data, staged.hash):  # the staged bytes are re-checked immediately before they leave the process
            raise ChunkWriteError("chunk bytes do not match the supplied digest")
        try:
            resp, ambiguous = self._request(
                "upload", "POST", self._url("object", key), content=data,
                headers={"x-upsert": "false", "Content-Type": "application/octet-stream", "Cache-Control": "max-age=31536000"})
        except ChunkStoreUnavailableError as exc:
            if self._probe_valid(staged.hash):  # the outcome was ambiguous: look before giving up
                return True
            raise ChunkStoreUnavailableError("the chunk store is unavailable; whether the chunk was stored is unknown",
                                             details={**(exc.details or {}), "outcome": "unknown"}) from None
        if 200 <= resp.status < 300:
            return True
        if self._is_duplicate(resp):
            self._require_existing_valid(staged.hash)  # raises ChunkCorruptedError / unavailable; never overwrites
            return ambiguous  # an earlier attempt of THIS call may have created it
        if resp.status == 413:
            raise ChunkWriteError("the storage service refused the chunk (too large)")
        raise ChunkWriteError(f"the storage service refused the chunk (HTTP {resp.status})")

    # ------------------------------------------------------------------------------------------------ reading

    def _download(self, chunk_hash: str, attempts: int | None = None) -> bytes | None:
        """The object's bytes, or None if it does not exist. Size-capped; NOT yet verified."""
        key = object_key(chunk_hash)
        resp, _ = self._request("download", "GET", self._url("object/authenticated", key), max_bytes=self._cfg.max_object_bytes,
                                  attempts=attempts)
        if 200 <= resp.status < 300:
            return resp.body
        if self._is_missing(resp):
            return None
        raise ChunkStoreError(f"the storage service could not return the chunk (HTTP {resp.status})")

    def read(self, chunk_hash: str) -> bytes:
        """The chunk's bytes, verified against ``chunk_hash`` first. ``ChunkNotFoundError`` / ``ChunkCorruptedError``."""
        data = self._download(chunk_hash)
        if data is None:
            raise ChunkNotFoundError("chunk object is missing")
        if not self._hasher.verify(data, chunk_hash):
            raise ChunkCorruptedError("the stored chunk does not match its digest")
        return data

    def _require_existing_valid(self, chunk_hash: str) -> None:
        data = self._download(chunk_hash)
        if data is None:
            raise ChunkStoreUnavailableError("the storage service reported a duplicate but the object cannot be read",
                                             details={"outcome": "unknown"})
        if not self._hasher.verify(data, chunk_hash):
            raise ChunkCorruptedError("an existing stored chunk does not match its digest")

    def _probe_valid(self, chunk_hash: str) -> bool:
        """Best-effort single look after an exhausted upload: is a VALID object there now? Never raises."""
        try:
            data = self._download(chunk_hash, attempts=1)
        except ChunkStoreError:
            return False
        return data is not None and self._hasher.verify(data, chunk_hash)

    def stat(self, chunk_hash: str) -> int | None:
        """Size in bytes of the stored object, or None if absent, from a metadata listing (no download).  Verify: list response shape."""
        key = object_key(chunk_hash)
        prefix, name = key.split("/")
        resp, _ = self._request("stat", "POST", self._url("object/list"),
                                json_body={"prefix": prefix, "search": name, "limit": 100, "offset": 0}, max_bytes=_LIST_PAGE * 1024)
        if not 200 <= resp.status < 300:
            raise ChunkStoreError(f"the storage service could not list objects (HTTP {resp.status})")
        for entry in self._list_entries(resp):
            if entry.get("name") == name:  # exact match: search is a substring filter
                size = (entry.get("metadata") or {}).get("size")
                return int(size) if isinstance(size, (int, float)) else -1  # present, size unknown
        return None

    def check_many(self, items: list[tuple[str, int]]) -> dict[str, ChunkStatus]:
        """Presence-and-size check of many chunks (the 'exists' verification) with ONE listing request per 2-hex prefix involved.

        A listing returns every object under a prefix, so ``n`` chunks cost at most ``min(n, 256)`` requests (``io_workers`` at a time)
        instead of ``n``. Exactly the same verdict per chunk as ``check`` (missing / wrong size / ok); content is not hashed here
        (that is ``verify`` / the 'full' mode). A storage failure raises: it is never turned into "ok"."""
        for h, _ in items:
            validate_digest(h)
        by_prefix: dict[str, list[tuple[str, int]]] = {}
        for h, size in items:
            by_prefix.setdefault(h[:2], []).append((h, size))

        def one(prefix: str) -> dict[str, ChunkStatus]:
            present = {}
            for entry in self._list_prefix(prefix):
                name = str(entry.get("name", ""))
                if len(name) == 64 and name.startswith(prefix):
                    size = (entry.get("metadata") or {}).get("size")
                    present[name] = int(size) if isinstance(size, (int, float)) else -1
            out = {}
            for h, want in by_prefix[prefix]:
                have = present.get(h)
                out[h] = ChunkStatus.MISSING if have is None else ChunkStatus.CORRUPT if (have >= 0 and have != want) else ChunkStatus.OK
            return out

        result: dict[str, ChunkStatus] = {}
        if not by_prefix:
            return result
        with ThreadPoolExecutor(max_workers=min(self.io_workers, len(by_prefix)), thread_name_prefix="chunk-check") as pool:
            for part in pool.map(one, list(by_prefix)):
                result.update(part)
        return result

    def exists(self, chunk_hash: str) -> bool:
        return self.stat(chunk_hash) is not None

    def check(self, chunk_hash: str, expected_size: int | None = None) -> ChunkStatus:
        """Cheap presence check (no download): present with the right size -> OK. Content is NOT verified (see ``verify``)."""
        size = self.stat(chunk_hash)
        if size is None:
            return ChunkStatus.MISSING
        if expected_size is not None and size >= 0 and size != expected_size:
            return ChunkStatus.CORRUPT
        return ChunkStatus.OK

    def verify(self, chunk_hash: str, expected_size: int | None = None) -> ChunkStatus:
        """Download and re-hash. Never raises for missing/corrupt content (service failures still raise)."""
        data = self._download(chunk_hash)
        if data is None:
            return ChunkStatus.MISSING
        if expected_size is not None and len(data) != expected_size:
            return ChunkStatus.CORRUPT
        return ChunkStatus.OK if self._hasher.verify(data, chunk_hash) else ChunkStatus.CORRUPT

    # ------------------------------------------------------------------------------------------------ deleting

    def delete_in_flight(self, chunk_hash: str) -> bool:
        """True while a delete request for this chunk may still reach Storage (e.g. a worker abandoned by a bounded cleanup).
        Garbage collection must not release such a row: a late delete could otherwise remove a re-published object."""
        with self._inflight_lock:
            return chunk_hash in self._inflight_deletes

    def delete(self, chunk_hash: str) -> bool:
        """Remove ONE object. True if it was removed, False if it was already absent. Called only by orchestrators that have
        established (under the writer lock, from metadata) that nothing references the chunk."""
        key = object_key(chunk_hash)
        with self._inflight_lock:
            self._inflight_deletes[chunk_hash] = self._inflight_deletes.get(chunk_hash, 0) + 1
        try:
            resp, _ = self._request("delete", "DELETE", self._url("object"), json_body={"prefixes": [key]}, max_bytes=_LIST_PAGE * 1024)
        finally:
            with self._inflight_lock:
                n = self._inflight_deletes.get(chunk_hash, 1) - 1
                if n > 0:
                    self._inflight_deletes[chunk_hash] = n
                else:
                    self._inflight_deletes.pop(chunk_hash, None)
        if self._is_missing(resp):
            return False
        if not 200 <= resp.status < 300:
            raise ChunkWriteError(f"the storage service could not delete the chunk (HTTP {resp.status})")
        removed = [e for e in self._list_entries(resp) if str(e.get("name", "")).endswith(chunk_hash)]
        return bool(removed)

    # ------------------------------------------------------------------------------------------------ enumeration

    @staticmethod
    def _list_entries(resp: _Resp) -> list[dict]:
        try:
            data = json.loads(resp.body or b"[]")
        except ValueError:
            raise ChunkStoreError("the storage service returned an unreadable listing") from None
        if not isinstance(data, list):
            raise ChunkStoreError("the storage service returned an unexpected listing")
        return [e for e in data if isinstance(e, dict)]

    def _list_prefix(self, prefix: str) -> Iterator[dict]:
        offset = 0
        for _ in range(_MAX_LIST_PAGES):
            resp, _ = self._request("list", "POST", self._url("object/list"), json_body={
                "prefix": prefix, "limit": _LIST_PAGE, "offset": offset, "sortBy": {"column": "name", "order": "asc"}},
                max_bytes=_LIST_PAGE * 1024)
            if not 200 <= resp.status < 300:
                raise ChunkStoreError(f"the storage service could not list objects (HTTP {resp.status})")
            entries = self._list_entries(resp)
            yield from entries
            if len(entries) < _LIST_PAGE:
                return
            offset += len(entries)
        raise ChunkStoreError("object listing did not terminate")

    def iter_hashes(self) -> Iterator[str]:
        """Every stored chunk hash (256 prefixes, paginated). A maintenance operation, not for request paths."""
        for first in range(256):
            prefix = f"{first:02x}"
            for entry in self._list_prefix(prefix):
                name = str(entry.get("name", ""))
                if name.startswith(prefix) and len(name) == 64:
                    try:
                        yield validate_digest(name)
                    except Exception:  # noqa: BLE001 - foreign object names are ignored, never trusted
                        continue

    def iter_staged(self) -> Iterator[Path]:
        """Leftover LOCAL scratch files (an interrupted upload/restore). Chunk staging itself is in memory."""
        if self.staging_dir is None or not self.staging_dir.is_dir():
            return iter(())
        return iter(sorted(p for p in self.staging_dir.iterdir() if p.is_file()))

    def disk_usage(self) -> tuple[int, int]:
        """(object count, total bytes) from the listing's metadata. A maintenance/statistics operation."""
        count = total = 0
        for first in range(256):
            for entry in self._list_prefix(f"{first:02x}"):
                name = str(entry.get("name", ""))
                if len(name) == 64 and name.startswith(f"{first:02x}"):
                    size = (entry.get("metadata") or {}).get("size")
                    count += 1
                    total += int(size) if isinstance(size, (int, float)) else 0
        return count, total

    def read_many(self, hashes: list[str]) -> Iterator[bytes]:
        """Verified chunk bytes in the order of ``hashes``, fetched with bounded concurrency (a window of at most
        ``2 * io_workers`` requests ahead of the consumer). Stops at the first failure and cancels what has not started."""
        for h in hashes:
            validate_digest(h)
        workers = min(self.io_workers, max(1, len(hashes)))
        window = workers * 2
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chunk-read") as pool:
            pending: list = []
            nxt = 0
            try:
                while nxt < len(hashes) or pending:
                    while nxt < len(hashes) and len(pending) < window:
                        pending.append(pool.submit(self.read, hashes[nxt]))
                        nxt += 1
                    yield pending.pop(0).result()
            finally:
                for f in pending:
                    f.cancel()

    def inventory(self) -> dict[str, int]:
        """{hash: size} of every stored object from one listing per prefix (at most 256 requests, ``io_workers`` at a time).
        Explicit maintenance/audit use only; never part of a request path."""
        def one(first: int) -> list[tuple[str, int]]:
            prefix = f"{first:02x}"
            out = []
            for entry in self._list_prefix(prefix):
                name = str(entry.get("name", ""))
                if len(name) == 64 and name.startswith(prefix):
                    try:
                        validate_digest(name)
                    except Exception:  # noqa: BLE001
                        continue
                    size = (entry.get("metadata") or {}).get("size")
                    out.append((name, int(size) if isinstance(size, (int, float)) else -1))
            return out

        with ThreadPoolExecutor(max_workers=self.io_workers, thread_name_prefix="chunk-list") as pool:
            return {h: sz for part in pool.map(one, range(256)) for h, sz in part}

    def free_bytes(self) -> int | None:
        return None  # not a local volume: the quota guard (C4/E4) is the protection here

    def ping(self) -> bool:
        """True if the bucket answers a one-entry listing with these credentials. Never raises."""
        try:
            resp, _ = self._request("ping", "POST", self._url("object/list"),
                                    json_body={"prefix": "", "limit": 1, "offset": 0}, max_bytes=_LIST_PAGE * 1024)
            return 200 <= resp.status < 300
        except ChunkStoreError:
            return False
