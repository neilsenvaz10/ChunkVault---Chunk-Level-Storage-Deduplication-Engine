"""Wires the concrete components together. One instance per application."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.chunking.rabin import RabinChunker
from app.core.config import Settings
from app.core.locking import WriterLock
from app.core.errors import ConfigurationError
from app.database.factory import build_database, build_repository
from app.database.interfaces import Database
from app.database.repository import MetadataRepository
from app.deduplication.deletion import FileDeleter
from app.deduplication.manager import Deduplicator
from app.deduplication.upload import Uploader
from app.hashing.sha256 import Sha256Service
from app.reliability.auditor import Auditor
from app.reliability.reconciler import ReconcilerImpl
from app.restoration.engine import Restorer
from app.storage.filesystem import FilesystemChunkStore

if TYPE_CHECKING:  # the local backend must not need the PostgreSQL driver or the HTTP client at import time
    from app.database.postgres import PgWriterLock
    from app.maintenance.gc import GarbageCollector
    from app.storage.supabase import SupabaseChunkStore


@dataclass
class Services:
    settings: Settings
    db: Database
    lock: WriterLock | PgWriterLock
    hasher: Sha256Service
    chunker: RabinChunker
    store: "FilesystemChunkStore | SupabaseChunkStore"
    repo: MetadataRepository
    dedup: Deduplicator
    uploader: Uploader
    restorer: Restorer
    deleter: FileDeleter
    auditor: Auditor
    reconciler: ReconcilerImpl
    gc: "GarbageCollector | None" = None  # cloud only: explicit, authenticated garbage collection


def build_services(settings: Settings) -> Services:
    """Wire the configured backend. ``local`` = SQLite + filesystem; ``cloud`` = PostgreSQL + Supabase Storage. The two are always
    selected together and checked afterwards: a half-cloud, half-local combination is refused, never run."""
    db = build_database(settings)  # never falls back silently
    repo = build_repository(settings)
    hasher = Sha256Service()
    if settings.backend == "cloud":
        from app.database.postgres import PostgresDatabase
        from app.storage.supabase import SupabaseChunkStore

        settings.staging_dir.mkdir(parents=True, exist_ok=True)  # local SCRATCH for the upload being received, nothing else
        store = SupabaseChunkStore.from_settings(settings.cloud, staging_dir=settings.staging_dir)
        if not isinstance(db, PostgresDatabase):
            raise ConfigurationError("the cloud backend requires the PostgreSQL database; refusing a mixed configuration")
    else:
        from app.database.sqlite import SqliteDatabase

        store = FilesystemChunkStore(settings.chunks_dir, settings.staging_dir, hasher)
        if not isinstance(db, SqliteDatabase):
            raise ConfigurationError("the local backend requires the SQLite database; refusing a mixed configuration")
    cloud = settings.cloud if settings.backend == "cloud" else None
    lock = db.lock  # the backend's writer lock (a shared object: its mutual exclusion must not be duplicated)
    chunker = RabinChunker(settings.cdc)
    dedup = Deduplicator(repo, store)
    if cloud is not None:
        from app.reliability.cloud_auditor import CloudAuditor
        from app.maintenance.gc import GarbageCollector

        auditor = CloudAuditor(settings, db, repo, store, hasher, cloud)
        gc = GarbageCollector(settings, db, repo, store, lock)
    else:
        auditor = Auditor(settings, db, repo, store, hasher)
        gc = None
    return Services(
        settings=settings, db=db, lock=lock, hasher=hasher, chunker=chunker, store=store, repo=repo, dedup=dedup,
        uploader=Uploader(settings, db, repo, store, chunker, dedup, hasher, lock, cloud=cloud),
        restorer=Restorer(settings, db, repo, store, hasher),
        deleter=FileDeleter(settings, db, repo, store, lock),
        auditor=auditor,
        reconciler=ReconcilerImpl(settings, db, repo, store, auditor, lock, reclaim_chunks=cloud is None,
                                  inflight_grace_seconds=cloud.orphan_min_age_seconds if cloud else None),
        gc=gc,
    )
