"""Choose the metadata database and repository for the configured backend.

``local`` -> SQLite. ``cloud`` -> PostgreSQL (Supabase). Selecting ``cloud`` never falls back to the local database: that
would send a production deployment's data to an ephemeral disk.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.database.interfaces import Database
from app.database.repository import MetadataRepository
from app.database.sqlite import SqliteDatabase


def build_database(settings: Settings) -> Database:
    if settings.backend == "local":
        return SqliteDatabase(settings)
    if settings.backend == "cloud":
        from app.database.postgres import PostgresDatabase  # imported lazily: the local backend never needs the driver

        return PostgresDatabase(settings)  # does not connect until first use
    raise ConfigurationError(f"unknown backend {settings.backend!r}")  # unreachable: Settings validates it


def build_repository(settings: Settings) -> MetadataRepository:
    if settings.backend == "local":
        from app.database.sqlite_repository import SqliteMetadataRepository

        return SqliteMetadataRepository()
    if settings.backend == "cloud":
        from app.database.postgres_repository import PostgresMetadataRepository

        return PostgresMetadataRepository()
    raise ConfigurationError(f"unknown backend {settings.backend!r}")
