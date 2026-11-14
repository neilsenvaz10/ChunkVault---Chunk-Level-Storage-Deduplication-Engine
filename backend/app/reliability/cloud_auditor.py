"""Integrity audit for the cloud backend.

The local auditor re-hashes every stored chunk inside one long metadata snapshot. Against remote storage that would hold a
PostgreSQL transaction open across thousands of HTTP requests, so the cloud audit is shaped differently:

1. a SHORT read-only snapshot gathers all metadata (chunk rows, files, manifests, counter mismatches, running operations) and ends;
2. only then does it talk to the object store, never holding a database transaction while it does:
   * presence and size of every stored chunk come from ONE inventory (at most 256 listing requests, a few at a time), which also
     reveals objects that have no metadata row;
   * content (SHA-256) is verified for a bounded random SAMPLE of chunks per audit (``audit_deep_chunks``), and fully for a single
     file when ``verify_file`` is used. The report says how many chunks were hash-verified, so a clean report is never read as
     "every byte was re-hashed".
3. a second short snapshot resolves which files reference each damaged chunk (bounded).

Read-only: nothing is repaired, deleted or rewritten.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor

from app.core.config import CloudSettings, Settings
from app.core.errors import NotFoundError
from app.database.interfaces import Database
from app.database.repository import MetadataRepository
from app.database.states import ChunkState, FileStatus
from app.hashing.sha256 import Sha256Service
from app.reliability.auditor import Auditor, parse_ts
from app.reliability.interfaces import AuditReport, Finding
from app.storage.filesystem import ChunkStatus
from app.storage.interfaces import ChunkCorruptedError, ChunkNotFoundError
from app.storage.supabase import SupabaseChunkStore

_MAX_DETAIL_LOOKUPS = 200
_MAX_ORPHAN_FINDINGS = 500


class CloudAuditor(Auditor):
    def __init__(self, settings: Settings, db: Database, repo: MetadataRepository, store: SupabaseChunkStore,
                 hasher: Sha256Service, cloud: CloudSettings) -> None:
        super().__init__(settings, db, repo, store, hasher)
        self._cloud = cloud
        self._rng = random.Random()

    # -- one file ------------------------------------------------------------

    def verify_file(self, file_id: str) -> AuditReport:
        with self._db.connection() as conn, self._db.snapshot(conn):
            file = self._repo.get_file(conn, file_id)
            if file is None:
                raise NotFoundError("file not found")
            manifest = self._repo.get_file_chunks(conn, file_id)
        findings: list[Finding] = list(self._manifest_findings(file, manifest))
        unique = list(dict.fromkeys(m.chunk_hash for m in manifest))
        fetched: dict[str, tuple[ChunkStatus, bytes | None]] = {}
        with ThreadPoolExecutor(max_workers=self._cloud.io_workers, thread_name_prefix="audit-read") as pool:
            for h, result in zip(unique, pool.map(self._fetch, unique)):
                fetched[h] = result
        whole = self._hasher.new_hasher()
        total, whole_ok = 0, True
        for m in manifest:
            status, data = fetched[m.chunk_hash]
            if status is ChunkStatus.OK and data is not None and len(data) != m.chunk_size:
                status = ChunkStatus.CORRUPT
            if status is ChunkStatus.OK:
                whole.update(data)  # type: ignore[arg-type]
                total += len(data)  # type: ignore[arg-type]
            else:
                whole_ok = False
                if not any(f.subject == m.chunk_hash for f in findings):
                    findings.append(self._chunk_finding(m.chunk_hash, status, [file_id]))
        if file.status is FileStatus.COMPLETE and whole_ok and not findings:
            if total != file.original_size or whole.hexdigest() != file.sha256:
                findings.append(Finding("file_hash_mismatch", file_id, "reassembled size/SHA-256 differs from the file record"))
        return AuditReport(chunks_checked=len(manifest), files_checked=1, findings=findings,
                           notes=[f"all {len(unique)} distinct chunk(s) of this file were downloaded and SHA-256 verified"])

    def _fetch(self, chunk_hash: str) -> tuple[ChunkStatus, bytes | None]:
        try:
            return ChunkStatus.OK, self._store.read(chunk_hash)  # read() verifies the digest before returning
        except ChunkNotFoundError:
            return ChunkStatus.MISSING, None
        except ChunkCorruptedError:
            return ChunkStatus.CORRUPT, None

    # -- whole store ---------------------------------------------------------

    def verify_store(self, verify_chunk_bytes: bool = True) -> AuditReport:
        now = time.time()
        stale = self._s.staging_stale_seconds
        findings: list[Finding] = []
        notes: list[str] = []
        with self._db.connection() as conn, self._db.snapshot(conn):  # metadata only; the connection is RELEASED before any storage request
            rows = self._repo.list_chunks(conn, limit=-1, offset=0)
            files = self._repo.list_files(conn, limit=-1, offset=0)
            manifests = {f.file_id: self._repo.get_file_chunks(conn, f.file_id) for f in files}
            mismatches = self._repo.ref_count_mismatches(conn)
            running = self._repo.list_running_operations(conn)
        row_by_hash = {r.hash: r for r in rows}
        problem_hashes: dict[str, ChunkStatus] = {}

        for r in rows:
            if r.state is ChunkState.PENDING:
                findings.append(Finding("pending_chunk", r.hash, "chunk row left in 'pending' state"))
            if r.ref_count == 0:
                findings.append(Finding("unreferenced_chunk", r.hash,
                                        "metadata row with ref_count 0; garbage collection can reclaim it"))
            if r.state is ChunkState.CORRUPT:
                findings.append(Finding("corrupt_chunk", r.hash, "chunk is flagged 'corrupt'"))
        for h, recorded, actual in mismatches:
            findings.append(Finding("ref_count_mismatch", h, f"recorded ref_count {recorded}, manifest rows {actual}"))
        for f in files:
            findings += self._manifest_findings(f, manifests[f.file_id])
            if f.status is FileStatus.UPLOADING and now - parse_ts(f.created_at) >= stale:
                findings.append(Finding("incomplete_file", f.file_id, "file stuck in 'uploading'"))
        for op in running:
            if now - parse_ts(op.started_at) >= stale:
                findings.append(Finding("stuck_operation", str(op.operation_id),
                                        f"{op.operation_type.value} operation still 'running' since {op.started_at}"))

        if verify_chunk_bytes:  # remote checks: no database transaction is open from here on
            inventory = self._store.inventory()
            for r in rows:
                if r.state is not ChunkState.STORED:
                    continue  # pending rows are reported above; their object may legitimately not exist
                size = inventory.get(r.hash)
                if size is None:
                    problem_hashes[r.hash] = ChunkStatus.MISSING
                elif size >= 0 and size != r.size_bytes:
                    problem_hashes[r.hash] = ChunkStatus.CORRUPT
            orphans = sorted(h for h in inventory if h not in row_by_hash)
            for h in orphans[:_MAX_ORPHAN_FINDINGS]:
                findings.append(Finding("orphan_chunk_file", h, "stored object has no metadata row"))
            if len(orphans) > _MAX_ORPHAN_FINDINGS:
                notes.append(f"{len(orphans) - _MAX_ORPHAN_FINDINGS} further orphan objects were not listed")
            sample_pool = [r for r in rows if r.state is ChunkState.STORED and r.hash not in problem_hashes]
            n = min(self._cloud.audit_deep_chunks, len(sample_pool))
            sample = self._rng.sample(sample_pool, n) if n else []
            with ThreadPoolExecutor(max_workers=self._cloud.io_workers, thread_name_prefix="audit-read") as pool:
                for r, (status, _) in zip(sample, pool.map(lambda x: self._fetch(x.hash), sample)):
                    if status is not ChunkStatus.OK:
                        problem_hashes[r.hash] = status
            notes.append(f"presence and size of {len(rows)} chunk row(s) checked against one object inventory; "
                         f"{n} of {len(sample_pool)} chunk(s) downloaded and SHA-256 verified (random sample)")
            with self._db.connection() as conn2, self._db.snapshot(conn2):  # second short snapshot: who is affected
                for h, status in list(problem_hashes.items())[:_MAX_DETAIL_LOOKUPS]:
                    findings.append(self._chunk_finding(h, status, self._repo.chunks_referenced_by_file(conn2, h)))
            for h, status in list(problem_hashes.items())[_MAX_DETAIL_LOOKUPS:]:
                findings.append(self._chunk_finding(h, status, []))
        else:
            notes.append("metadata-only audit: the object store was not contacted")

        for p in self._store.iter_staged():
            try:
                age = now - p.stat().st_mtime
            except OSError:
                continue
            if age >= stale:
                findings.append(Finding("orphan_staging_file", p.name, "stale file in staging area"))
        return AuditReport(chunks_checked=len(rows), files_checked=len(files), findings=findings, notes=notes)
