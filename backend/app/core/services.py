"""Wires the concrete components together. One instance per application."""

from __future__ import annotations

from dataclasses import dataclass

from app.chunking.rabin import RabinChunker
from app.core.config import Settings
from app.core.locking import WriterLock
from app.database.sqlite_repository import SqliteMetadataRepository
from app.deduplication.deletion import FileDeleter
from app.deduplication.manager import Deduplicator
from app.deduplication.upload import Uploader
from app.hashing.sha256 import Sha256Service
from app.reliability.auditor import Auditor
from app.reliability.reconciler import ReconcilerImpl
from app.restoration.engine import Restorer
from app.storage.filesystem import FilesystemChunkStore


@dataclass
class Services:
    settings: Settings
    lock: WriterLock
    hasher: Sha256Service
    chunker: RabinChunker
    store: FilesystemChunkStore
    repo: SqliteMetadataRepository
    dedup: Deduplicator
    uploader: Uploader
    restorer: Restorer
    deleter: FileDeleter
    auditor: Auditor
    reconciler: ReconcilerImpl


def build_services(settings: Settings) -> Services:
    hasher = Sha256Service()
    store = FilesystemChunkStore(settings.chunks_dir, settings.staging_dir, hasher)
    repo = SqliteMetadataRepository()
    lock = WriterLock(settings.lock_path, settings.writer_lock_timeout_seconds)
    chunker = RabinChunker(settings.cdc)
    dedup = Deduplicator(repo, store)
    auditor = Auditor(settings, repo, store, hasher)
    return Services(
        settings=settings, lock=lock, hasher=hasher, chunker=chunker, store=store, repo=repo, dedup=dedup,
        uploader=Uploader(settings, repo, store, chunker, dedup, hasher, lock),
        restorer=Restorer(settings, repo, store, hasher),
        deleter=FileDeleter(settings, repo, store, lock),
        auditor=auditor,
        reconciler=ReconcilerImpl(settings, repo, store, auditor, lock),
    )
