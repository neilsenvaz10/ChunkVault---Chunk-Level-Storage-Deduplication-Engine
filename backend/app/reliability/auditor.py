"""Read-only integrity audit. Reads the metadata inside a read-only snapshot (``Database.snapshot``) so it cannot modify
metadata and sees one consistent point in time, and never touches the chunk store except to read."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.database.interfaces import Database
from app.database.repository import MetadataRepository
from app.database.states import ChunkState, FileStatus
from app.hashing.sha256 import Sha256Service
from app.reliability.interfaces import AuditReport, Finding
from app.storage.filesystem import ChunkStatus, FilesystemChunkStore


def parse_ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


class Auditor:
    def __init__(self, settings: Settings, db: Database, repo: MetadataRepository, store: FilesystemChunkStore,
                 hasher: Sha256Service) -> None:
        self._s, self._db, self._repo, self._store, self._hasher = settings, db, repo, store, hasher

    # -- one file ------------------------------------------------------------

    def verify_file(self, file_id: str) -> AuditReport:
        with self._db.connection() as conn, self._db.snapshot(conn):
            file = self._repo.get_file(conn, file_id)
            if file is None:
                raise NotFoundError("file not found")
            findings: list[Finding] = []
            manifest = self._repo.get_file_chunks(conn, file_id)
            findings += self._manifest_findings(file, manifest)
            whole = self._hasher.new_hasher()
            total, whole_ok = 0, True
            for m in manifest:
                status = self._store.verify(m.chunk_hash, m.chunk_size)
                if status is ChunkStatus.OK:
                    data = self._store.read(m.chunk_hash)
                    whole.update(data)
                    total += len(data)
                else:
                    whole_ok = False
                    findings.append(self._chunk_finding(m.chunk_hash, status, [file_id]))
            if file.status is FileStatus.COMPLETE and whole_ok and not findings:
                if total != file.original_size or whole.hexdigest() != file.sha256:
                    findings.append(Finding("file_hash_mismatch", file_id,
                                            "reassembled size/SHA-256 differs from the file record"))
            return AuditReport(chunks_checked=len(manifest), files_checked=1, findings=findings)

    # -- whole store ---------------------------------------------------------

    def verify_store(self, verify_chunk_bytes: bool = True) -> AuditReport:
        now = time.time()
        stale = self._s.staging_stale_seconds
        findings: list[Finding] = []
        with self._db.connection() as conn:
            with self._db.snapshot(conn):  # one consistent snapshot of the metadata
                rows = self._repo.list_chunks(conn, limit=-1, offset=0)
                row_hashes = {r.hash for r in rows}
                for r in rows:
                    if r.state is ChunkState.PENDING:
                        findings.append(Finding("pending_chunk", r.hash, "chunk row left in 'pending' state"))
                    if r.ref_count == 0:
                        findings.append(Finding("unreferenced_chunk", r.hash,
                                                "metadata row with ref_count 0; reconciliation can reclaim it"))
                    if r.state is ChunkState.CORRUPT:
                        findings.append(Finding("corrupt_chunk", r.hash, "chunk is flagged 'corrupt'"))
                        continue
                    status = (self._store.verify(r.hash, r.size_bytes) if verify_chunk_bytes
                              else (ChunkStatus.OK if self._store.exists(r.hash) else ChunkStatus.MISSING))
                    if status is not ChunkStatus.OK:
                        findings.append(self._chunk_finding(
                            r.hash, status, self._repo.chunks_referenced_by_file(conn, r.hash)))
                for h in self._store.iter_hashes():
                    if h not in row_hashes:
                        findings.append(Finding("orphan_chunk_file", h, "chunk file has no metadata row"))
                for h, recorded, actual in self._repo.ref_count_mismatches(conn):
                    findings.append(Finding("ref_count_mismatch", h,
                                            f"recorded ref_count {recorded}, manifest rows {actual}"))
                files = self._repo.list_files(conn, limit=-1, offset=0)
                for f in files:
                    findings += self._manifest_findings(f, self._repo.get_file_chunks(conn, f.file_id))
                    if f.status is FileStatus.UPLOADING and now - parse_ts(f.created_at) >= stale:
                        findings.append(Finding("incomplete_file", f.file_id, "file stuck in 'uploading'"))
                for op in self._repo.list_running_operations(conn):
                    if now - parse_ts(op.started_at) >= stale:
                        findings.append(Finding(
                            "stuck_operation", str(op.operation_id),
                            f"{op.operation_type.value} operation still 'running' since {op.started_at}"))
            for p in self._store.iter_staged():
                try:
                    age = now - p.stat().st_mtime
                except OSError:
                    continue
                if age >= stale:
                    findings.append(Finding("orphan_staging_file", p.name, "stale file in staging area"))
            return AuditReport(chunks_checked=len(rows), files_checked=len(files), findings=findings)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _chunk_finding(chunk_hash: str, status: ChunkStatus, file_ids: list[str]) -> Finding:
        kind = "missing_chunk_file" if status is ChunkStatus.MISSING else "corrupt_chunk"
        what = "bytes absent from chunk store" if status is ChunkStatus.MISSING else \
            "stored bytes do not match digest/size"
        return Finding(kind, chunk_hash, f"{what}; referenced by files: {', '.join(file_ids) or 'none'}")

    @staticmethod
    def _manifest_findings(file, manifest) -> list[Finding]:
        out = []
        if [m.chunk_index for m in manifest] != list(range(len(manifest))):
            out.append(Finding("manifest_inconsistent", file.file_id, "chunk_index sequence has gaps"))
        if file.status is FileStatus.COMPLETE and sum(m.chunk_size for m in manifest) != file.original_size:
            out.append(Finding("manifest_inconsistent", file.file_id,
                               "manifest chunk sizes do not add up to original_size"))
        return out
