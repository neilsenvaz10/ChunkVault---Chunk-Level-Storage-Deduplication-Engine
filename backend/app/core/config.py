"""Centralised configuration.

All tunables live here. Values come from environment variables prefixed with
``CHUNKVAULT_`` (see ``.env.example``); nothing is read from the environment
anywhere else.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from app.core.errors import ConfigurationError

KIB = 1024
APP_NAME = "ChunkVault"
APP_VERSION = "0.1.0"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_PREFIX = "CHUNKVAULT_"
MIB = 1024 * KIB
BACKENDS = ("local", "cloud")
VERIFY_ON_REUSE_MODES = ("exists", "full", "none")
MIN_SECRET_LENGTH = 24
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{2,62}$")
_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
# A DNS host name (letters, digits, hyphens, dots); no wildcard, port, path or whitespace.
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")
_DIRECT_SUPABASE_HOST = re.compile(r"^db\.[a-z0-9]+\.supabase\.co$")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")


def _require_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{name} must be an integer, got {value!r}")
    return value


@dataclass(frozen=True)
class CDCConfig:
    """Parameters of the Rabin-style content-defined chunker.

    See docs/architecture/cdc-specification.md for how these are used.
    The algorithm is implemented in app/chunking/rabin.py; this class owns and
    validates the parameters.
    """

    min_chunk_size: int = 4 * KIB
    avg_chunk_size: int = 16 * KIB
    max_chunk_size: int = 128 * KIB
    window_size: int = 64

    def __post_init__(self) -> None:
        for name in ("min_chunk_size", "avg_chunk_size", "max_chunk_size", "window_size"):
            if _require_int(name, getattr(self, name)) <= 0:
                raise ConfigurationError(f"{name} must be positive, got {getattr(self, name)}")
        if not self.min_chunk_size < self.avg_chunk_size < self.max_chunk_size:
            raise ConfigurationError(
                "chunk sizes must satisfy min < avg < max, got "
                f"{self.min_chunk_size} / {self.avg_chunk_size} / {self.max_chunk_size}"
            )
        if self.window_size > self.min_chunk_size:
            raise ConfigurationError(
                "window_size must not exceed min_chunk_size "
                f"({self.window_size} > {self.min_chunk_size})"
            )

    @property
    def boundary_divisor(self) -> int:
        """Cut-point test divisor ``D``.

        After ``min_chunk_size`` bytes each position is a cut point with
        probability ``1/D``, so the expected chunk length is about
        ``min_chunk_size + D == avg_chunk_size`` (before max-size truncation).
        """
        return self.avg_chunk_size - self.min_chunk_size


@dataclass(frozen=True)
class CloudSettings:
    """Settings of the cloud backend (Supabase PostgreSQL + Supabase Storage).

    Every secret field is excluded from ``repr`` and from ``describe()``, and no validation message ever contains a secret
    value. Secrets are read from the backend's environment only; nothing here is, or may become, a frontend (``VITE_*``)
    variable.
    """

    database_url: str = field(repr=False)
    supabase_url: str
    supabase_service_key: str = field(repr=False)
    demo_token: str = field(repr=False)
    admin_token: str = field(repr=False)
    bucket: str = "chunkvault-chunks"
    # Cloud I/O: bounded concurrency inside one upload, in batches (see cloud-migration/phase-b-design.md 2.5).
    io_workers: int = 4
    batch_chunks: int = 64
    # exists: row 'stored' + object present with the right size | full: read and re-hash | none: trust the row.
    verify_on_reuse: str = "exists"
    # Application-level quota on UNIQUE stored object bytes (the free Supabase plan is smaller than this).
    stored_bytes_cap: int = 300 * MIB
    orphan_min_age_seconds: float = 900.0
    # X-Forwarded-For is believed ONLY when the direct peer lies inside one of these networks (explicitly configured, e.g. the reverse
    # proxy's published address range). Empty (the default) = never trusted: the direct peer's address is used. ``trusted_proxy_hops``
    # then says how many proxies append to the header.
    trusted_proxy_cidrs: tuple[str, ...] = ()
    trusted_proxy_hops: int = 1
    pool_min_size: int = 1
    pool_max_size: int = 4
    db_connect_timeout_seconds: int = 10
    db_statement_timeout_ms: int = 30000
    db_lock_timeout_ms: int = 5000
    # Optional PostgreSQL schema (tests run in throw-away schemas); None = the connection's default search_path.
    pg_schema: str | None = None
    # Supabase Storage client: every request is bounded; retries are bounded and only for failures worth retrying.
    storage_connect_timeout_seconds: float = 5.0
    storage_timeout_seconds: float = 20.0  # per read/write on one request
    storage_max_attempts: int = 3
    storage_deadline_seconds: float = 60.0  # total budget of one storage operation including retries
    storage_max_object_bytes: int = 8 * MIB  # larger objects are refused on upload and download
    # Chunks downloaded and SHA-256 verified per whole-store audit (a random sample; presence/size cover every chunk).
    audit_deep_chunks: int = 25
    # ---- upload reliability (cloud). The deadline covers the whole request: staging, analysis, lock wait, publishing, commit.
    upload_deadline_seconds: float = 90.0
    # After a FAILED upload, at most this long is spent removing the objects and rows that upload itself created (the rest stays in the
    # durable 'deleting' state, counted by the quota, for explicit garbage collection).
    upload_cleanup_budget_seconds: float = 30.0
    # ---- abuse limits (cloud). In-memory, per process (the service runs one worker).
    rate_limiting: bool = True
    max_concurrent_uploads: int = 2  # requests analysing/uploading at once (CPU and scratch-disk bound); the writer lock still serialises commits
    max_concurrent_restores: int = 3
    auth_failure_limit: int = 10  # failed authentications per client address within the window, then 429
    auth_failure_window_seconds: float = 300.0
    expensive_requests_per_minute: int = 30  # per client address, for uploads, restores, audits, simulations and the chunking demo
    # ---- start-up (cloud): how often a request may trigger a re-check of an unavailable database
    startup_retry_seconds: float = 5.0

    def __post_init__(self) -> None:
        url = urlsplit(self.database_url or "")
        if url.scheme not in ("postgresql", "postgres") or not url.hostname:
            raise ConfigurationError("DATABASE_URL must be a postgresql:// URL with a host")
        sb = urlsplit(self.supabase_url or "")
        if sb.scheme not in ("https", "http") or not sb.hostname:
            raise ConfigurationError("SUPABASE_URL must be an http(s) URL")
        if sb.scheme == "http" and sb.hostname not in ("localhost", "127.0.0.1"):
            raise ConfigurationError("SUPABASE_URL must use https (http is allowed only for localhost)")
        if not _BUCKET.match(self.bucket or ""):
            raise ConfigurationError("SUPABASE_BUCKET is not a valid bucket name")
        for name in ("supabase_service_key", "demo_token", "admin_token"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or any(c.isspace() for c in value):
                raise ConfigurationError(f"{name} must be a non-empty string without whitespace")
        for name in ("demo_token", "admin_token"):
            if len(getattr(self, name)) < MIN_SECRET_LENGTH:
                raise ConfigurationError(f"{name} must be at least {MIN_SECRET_LENGTH} characters (use a random value)")
        if len({self.demo_token, self.admin_token, self.supabase_service_key}) != 3:
            raise ConfigurationError("the demo token, the admin token and the Supabase service key must all differ")
        if self.verify_on_reuse not in VERIFY_ON_REUSE_MODES:
            raise ConfigurationError(f"verify_on_reuse must be one of {VERIFY_ON_REUSE_MODES}")
        for name, low, high in (("io_workers", 1, 16), ("batch_chunks", 1, 512), ("trusted_proxy_hops", 0, 5),
                                ("pool_min_size", 1, 10), ("pool_max_size", 1, 10),
                                ("db_connect_timeout_seconds", 1, 60), ("db_statement_timeout_ms", 1000, 300000),
                                ("db_lock_timeout_ms", 100, 60000)):
            v = _require_int(name, getattr(self, name))
            if not low <= v <= high:
                raise ConfigurationError(f"{name} must be between {low} and {high}, got {v}")
        if not isinstance(self.trusted_proxy_cidrs, tuple):
            raise ConfigurationError("trusted_proxy_cidrs must be a tuple of network strings")
        for cidr in self.trusted_proxy_cidrs:
            try:
                net = ipaddress.ip_network(cidr, strict=True)
            except (ValueError, TypeError):
                raise ConfigurationError("trusted_proxy_cidrs must contain valid networks such as 10.0.0.0/8 (host bits must be zero)") from None
            if net.prefixlen < (8 if net.version == 4 else 32):
                raise ConfigurationError("a trusted proxy network that broad would trust arbitrary clients; list the proxy's real range")
        if self.pool_min_size > self.pool_max_size:
            raise ConfigurationError("pool_min_size must not exceed pool_max_size")
        if _require_int("stored_bytes_cap", self.stored_bytes_cap) <= 0:
            raise ConfigurationError("stored_bytes_cap must be positive")
        if self.orphan_min_age_seconds < 0:
            raise ConfigurationError("orphan_min_age_seconds must not be negative")
        if self.pg_schema is not None and not _SCHEMA.match(self.pg_schema):
            raise ConfigurationError("pg_schema must be a plain lowercase identifier")
        for name, low, high in (("storage_connect_timeout_seconds", 0.1, 60), ("storage_timeout_seconds", 0.1, 300),
                                ("storage_max_attempts", 1, 6), ("storage_deadline_seconds", 1, 600)):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
                raise ConfigurationError(f"{name} must be a number between {low} and {high}")
        for name, low, high in (("upload_deadline_seconds", 5, 600), ("upload_cleanup_budget_seconds", 1, 300),
                                ("auth_failure_window_seconds", 10, 86400), ("startup_retry_seconds", 0.05, 300)):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
                raise ConfigurationError(f"{name} must be a number between {low} and {high}")
        for name, low, high in (("max_concurrent_uploads", 1, 8), ("max_concurrent_restores", 1, 16), ("auth_failure_limit", 1, 1000),
                                ("expensive_requests_per_minute", 1, 6000)):
            if not low <= _require_int(name, getattr(self, name)) <= high:
                raise ConfigurationError(f"{name} must be between {low} and {high}")
        if not isinstance(self.rate_limiting, bool):
            raise ConfigurationError("rate_limiting must be a boolean")
        if not 0 <= _require_int("audit_deep_chunks", self.audit_deep_chunks) <= 500:
            raise ConfigurationError("audit_deep_chunks must be between 0 and 500")
        if _require_int("storage_max_object_bytes", self.storage_max_object_bytes) < 1024:
            raise ConfigurationError("storage_max_object_bytes must be at least 1024")

    @property
    def database_host(self) -> str:
        return urlsplit(self.database_url).hostname or ""

    def warnings(self) -> list[str]:
        """Non-fatal advice, safe to log (never contains a secret)."""
        out: list[str] = []
        if _DIRECT_SUPABASE_HOST.match(self.database_host):
            out.append("DATABASE_URL uses the direct Supabase host, which may be IPv6-only. Prefer the session pooler URL.")
        if urlsplit(self.database_url).port == 6543:
            out.append("DATABASE_URL looks like the transaction pooler (port 6543); the writer lock needs session mode.")
        if self.verify_on_reuse == "none":
            out.append("verify_on_reuse=none trusts database rows without checking that objects exist.")
        return out

    def secret_values(self) -> tuple[str, ...]:
        """Exact secret strings, for the log redaction filter only."""
        pw = urlsplit(self.database_url).password
        return tuple(v for v in (self.supabase_service_key, self.demo_token, self.admin_token, pw) if v)

    def describe(self) -> dict[str, object]:
        """Loggable view: hosts and numbers, never secrets."""
        return {
            "database_host": self.database_host, "supabase_host": urlsplit(self.supabase_url).hostname,
            "bucket": self.bucket, "io_workers": self.io_workers, "batch_chunks": self.batch_chunks,
            "verify_on_reuse": self.verify_on_reuse, "stored_bytes_cap": self.stored_bytes_cap,
            "pool": [self.pool_min_size, self.pool_max_size], "pg_schema": self.pg_schema,
            "storage_timeout_seconds": self.storage_timeout_seconds, "storage_max_attempts": self.storage_max_attempts,
        }


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    cors_origins: tuple[str, ...]
    cdc: CDCConfig = field(default_factory=CDCConfig)
    max_upload_bytes: int = 64 * 1024 * KIB
    writer_lock_timeout_seconds: float = 30.0
    # Staging files / running operations younger than this are assumed in flight.
    staging_stale_seconds: float = 300.0
    # Advisory free-space protection for uploads: this much free space must remain on the chunk store's
    # volume on top of the bytes an upload may add (see Uploader._check_free_space). 0 disables the margin.
    free_space_reserve_bytes: int = 16 * 1024 * KIB
    # The fault lab damages ONLY temporary stores it creates itself, but it is still off unless
    # explicitly enabled (CHUNKVAULT_ENABLE_FAULT_LAB=1).
    fault_lab_enabled: bool = False
    # Host header allow-list (DNS-rebinding defence). Override with CHUNKVAULT_ALLOWED_HOSTS.
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]")
    app_name: str = APP_NAME
    app_version: str = APP_VERSION
    # "local" = SQLite + filesystem (development, tests, rollback); "cloud" = Supabase PostgreSQL + Storage.
    backend: str = "local"
    cloud: CloudSettings | None = None

    def __post_init__(self) -> None:
        if self.backend not in BACKENDS:
            raise ConfigurationError(f"backend must be one of {BACKENDS}, got {self.backend!r}")
        if self.backend == "cloud":
            if self.cloud is None:
                raise ConfigurationError("the cloud backend requires cloud settings")
            if self.max_upload_bytes > self.cloud.stored_bytes_cap:
                raise ConfigurationError("max_upload_bytes must not exceed the cloud storage quota")
            for origin in self.cors_origins:
                parts = urlsplit(origin)
                if parts.scheme != "https" and (parts.hostname or "") not in _LOCAL_HOSTS:
                    raise ConfigurationError("with the cloud backend every non-local CORS origin must use https")
        if self.max_upload_bytes <= 0:
            raise ConfigurationError("max_upload_bytes must be positive")
        if self.free_space_reserve_bytes < 0:
            raise ConfigurationError("free_space_reserve_bytes must not be negative")
        if self.writer_lock_timeout_seconds < 0 or self.staging_stale_seconds < 0:
            raise ConfigurationError("timeouts must not be negative")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ConfigurationError("allowed_hosts must be an explicit, non-empty list (no '*')")
        if not self.cors_origins:
            raise ConfigurationError("at least one CORS origin is required")
        for origin in self.cors_origins:
            if origin == "*" or not origin.startswith(("http://", "https://")):
                raise ConfigurationError(
                    f"CORS origin must be an explicit http(s) origin, got {origin!r}"
                )

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "writer.lock"

    @property
    def chunks_dir(self) -> Path:
        return self.data_dir / "chunks"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "staging"

    @property
    def benchmarks_dir(self) -> Path:
        return self.data_dir / "benchmarks"

    def ensure_directories(self) -> None:
        for directory in (self.data_dir, self.chunks_dir, self.staging_dir, self.benchmarks_dir):
            directory.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        def get(name: str) -> str | None:
            return env.get(ENV_PREFIX + name)

        def get_int(name: str, default: int) -> int:
            raw = get(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except ValueError:
                raise ConfigurationError(f"{ENV_PREFIX}{name} must be an integer, got {raw!r}")

        def resolve(raw: str) -> Path:
            path = Path(raw)
            return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

        data_dir = resolve(get("DATA_DIR") or "data")
        db_path = resolve(get("DB_PATH")) if get("DB_PATH") else data_dir / "chunkvault.db"
        origins_raw = get("CORS_ORIGINS") or "http://localhost:5173,http://127.0.0.1:5173"
        origins = tuple(o.strip() for o in origins_raw.split(",") if o.strip())
        defaults = CDCConfig()
        cdc = CDCConfig(
            min_chunk_size=get_int("CDC_MIN_CHUNK_SIZE", defaults.min_chunk_size),
            avg_chunk_size=get_int("CDC_AVG_CHUNK_SIZE", defaults.avg_chunk_size),
            max_chunk_size=get_int("CDC_MAX_CHUNK_SIZE", defaults.max_chunk_size),
            window_size=get_int("CDC_WINDOW_SIZE", defaults.window_size),
        )
        def get_float(name: str, default: float) -> float:
            raw = get(name)
            if raw is None:
                return default
            try:
                return float(raw)
            except ValueError:
                raise ConfigurationError(f"{ENV_PREFIX}{name} must be a number, got {raw!r}")

        backend = (get("BACKEND") or "local").strip().lower()
        cloud = None
        if backend == "cloud":
            # Provider-standard names are read unprefixed (the Render/Supabase dashboards use them); the rest are CHUNKVAULT_*.
            def provider(name: str) -> str:
                value = env.get(name)
                if not value:
                    raise ConfigurationError(f"{name} is required with {ENV_PREFIX}BACKEND=cloud")
                return value

            def required(name: str) -> str:
                value = get(name)
                if not value:
                    raise ConfigurationError(f"{ENV_PREFIX}{name} is required with {ENV_PREFIX}BACKEND=cloud")
                return value

            defaults_cloud = CloudSettings.__dataclass_fields__
            try:
                orphan_age = float(get("ORPHAN_MIN_AGE_SECONDS") or defaults_cloud["orphan_min_age_seconds"].default)
            except ValueError:
                raise ConfigurationError(f"{ENV_PREFIX}ORPHAN_MIN_AGE_SECONDS must be a number")
            cloud = CloudSettings(
                database_url=provider("DATABASE_URL"),
                supabase_url=provider("SUPABASE_URL"),
                supabase_service_key=provider("SUPABASE_SERVICE_KEY"),
                demo_token=required("DEMO_TOKEN"),
                admin_token=required("ADMIN_TOKEN"),
                bucket=env.get("SUPABASE_BUCKET") or defaults_cloud["bucket"].default,
                io_workers=get_int("CLOUD_IO_WORKERS", defaults_cloud["io_workers"].default),
                batch_chunks=get_int("CLOUD_BATCH_CHUNKS", defaults_cloud["batch_chunks"].default),
                verify_on_reuse=(get("VERIFY_ON_REUSE") or defaults_cloud["verify_on_reuse"].default).strip().lower(),
                stored_bytes_cap=get_int("STORED_BYTES_CAP", defaults_cloud["stored_bytes_cap"].default),
                orphan_min_age_seconds=orphan_age,
                trusted_proxy_hops=get_int("TRUSTED_PROXY_HOPS", defaults_cloud["trusted_proxy_hops"].default),
                trusted_proxy_cidrs=tuple(x.strip() for x in (get("TRUSTED_PROXY_CIDRS") or "").split(",") if x.strip()),
                pool_max_size=get_int("DB_POOL_MAX", defaults_cloud["pool_max_size"].default),
                pg_schema=(get("PG_SCHEMA") or None),
                storage_timeout_seconds=get_float("STORAGE_TIMEOUT_SECONDS", defaults_cloud["storage_timeout_seconds"].default),
                storage_max_attempts=get_int("STORAGE_MAX_ATTEMPTS", defaults_cloud["storage_max_attempts"].default),
                audit_deep_chunks=get_int("AUDIT_DEEP_CHUNKS", defaults_cloud["audit_deep_chunks"].default),
                upload_deadline_seconds=get_float("UPLOAD_DEADLINE_SECONDS", defaults_cloud["upload_deadline_seconds"].default),
                upload_cleanup_budget_seconds=get_float("UPLOAD_CLEANUP_BUDGET_SECONDS", defaults_cloud["upload_cleanup_budget_seconds"].default),
                rate_limiting=(get("RATE_LIMITING") or "1").strip().lower() not in ("0", "false", "no", "off"),
                max_concurrent_uploads=get_int("MAX_CONCURRENT_UPLOADS", defaults_cloud["max_concurrent_uploads"].default),
                max_concurrent_restores=get_int("MAX_CONCURRENT_RESTORES", defaults_cloud["max_concurrent_restores"].default),
                auth_failure_limit=get_int("AUTH_FAILURE_LIMIT", defaults_cloud["auth_failure_limit"].default),
                auth_failure_window_seconds=get_float("AUTH_FAILURE_WINDOW_SECONDS", defaults_cloud["auth_failure_window_seconds"].default),
                expensive_requests_per_minute=get_int("EXPENSIVE_REQUESTS_PER_MINUTE", defaults_cloud["expensive_requests_per_minute"].default),
                startup_retry_seconds=get_float("STARTUP_RETRY_SECONDS", defaults_cloud["startup_retry_seconds"].default),
            )
        # Conservative public-demo default: 10 MiB with the cloud backend, 64 MiB locally.
        default_upload = 10 * MIB if backend == "cloud" else 64 * 1024 * KIB

        allowed_hosts = tuple(h.strip() for h in (get("ALLOWED_HOSTS") or "localhost,127.0.0.1,[::1]").split(",") if h.strip())
        if backend == "cloud":
            # Render sets RENDER_EXTERNAL_HOSTNAME in the service's own environment: the platform's statement of the one public name
            # it serves. Trusting it is trusting the operator's environment, never a request header; arbitrary Host values stay refused.
            platform_host = (env.get("RENDER_EXTERNAL_HOSTNAME") or "").strip().lower()
            if platform_host:
                if not _HOSTNAME.match(platform_host):
                    raise ConfigurationError("RENDER_EXTERNAL_HOSTNAME is not a valid host name")
                if platform_host not in allowed_hosts:
                    allowed_hosts += (platform_host,)

        return cls(
            data_dir=data_dir,
            db_path=db_path,
            cors_origins=origins,
            cdc=cdc,
            backend=backend,
            cloud=cloud,
            max_upload_bytes=get_int("MAX_UPLOAD_BYTES", default_upload),
            writer_lock_timeout_seconds=get_float("WRITER_LOCK_TIMEOUT_SECONDS", 30.0),
            staging_stale_seconds=get_float("STAGING_STALE_SECONDS", 300.0),
            free_space_reserve_bytes=get_int("FREE_SPACE_RESERVE_BYTES", 16 * 1024 * KIB),
            allowed_hosts=allowed_hosts,
            fault_lab_enabled=(get("ENABLE_FAULT_LAB") or "").strip().lower() in ("1", "true", "yes"),
        )
