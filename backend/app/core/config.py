"""Centralised configuration.

All tunables live here. Values come from environment variables prefixed with
``CHUNKVAULT_`` (see ``.env.example``); nothing is read from the environment
anywhere else.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import ConfigurationError

KIB = 1024
APP_NAME = "ChunkVault"
APP_VERSION = "0.1.0"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_PREFIX = "CHUNKVAULT_"


def _require_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{name} must be an integer, got {value!r}")
    return value


@dataclass(frozen=True)
class CDCConfig:
    """Parameters of the Rabin-style content-defined chunker.

    See docs/architecture/cdc-specification.md for how these are used.
    The algorithm itself is implemented in Phase 2; Phase 1 only owns and
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
class Settings:
    data_dir: Path
    db_path: Path
    cors_origins: tuple[str, ...]
    cdc: CDCConfig = field(default_factory=CDCConfig)
    max_upload_bytes: int = 64 * 1024 * KIB
    writer_lock_timeout_seconds: float = 30.0
    # Staging files / running operations younger than this are assumed in flight.
    staging_stale_seconds: float = 300.0
    # The fault lab damages ONLY temporary stores it creates itself, but it is still off unless
    # explicitly enabled (CHUNKVAULT_ENABLE_FAULT_LAB=1).
    fault_lab_enabled: bool = False
    app_name: str = APP_NAME
    app_version: str = APP_VERSION

    def __post_init__(self) -> None:
        if self.max_upload_bytes <= 0:
            raise ConfigurationError("max_upload_bytes must be positive")
        if self.writer_lock_timeout_seconds < 0 or self.staging_stale_seconds < 0:
            raise ConfigurationError("timeouts must not be negative")
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

        return cls(
            data_dir=data_dir,
            db_path=db_path,
            cors_origins=origins,
            cdc=cdc,
            max_upload_bytes=get_int("MAX_UPLOAD_BYTES", 64 * 1024 * KIB),
            writer_lock_timeout_seconds=get_float("WRITER_LOCK_TIMEOUT_SECONDS", 30.0),
            staging_stale_seconds=get_float("STAGING_STALE_SECONDS", 300.0),
            fault_lab_enabled=(get("ENABLE_FAULT_LAB") or "").strip().lower() in ("1", "true", "yes"),
        )
