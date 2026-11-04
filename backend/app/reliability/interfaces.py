"""Integrity auditor and reconciler interfaces.

Implementations: ``reliability/auditor.py`` (read-only detection) and
``reliability/reconciler.py`` (explicit, reported repair). Detection and repair
are deliberately separate classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.core.errors import ChunkVaultError

FindingKind = Literal[
    "missing_chunk_file",  # metadata says stored, bytes absent (detail lists affected files)
    "corrupt_chunk",  # bytes do not match digest/size, or row state is 'corrupt'
    "orphan_chunk_file",  # chunk file with no metadata row
    "unreferenced_chunk",  # metadata row with ref_count 0 (garbage-collection candidate)
    "pending_chunk",  # row left in 'pending' state
    "orphan_staging_file",  # stale file in staging/
    "ref_count_mismatch",  # ref_count != number of manifest rows
    "manifest_inconsistent",  # gaps in chunk_index or sizes not summing to original_size
    "file_hash_mismatch",  # reassembled file does not match files.sha256
    "incomplete_file",  # file stuck in 'uploading'
    "stuck_operation",  # operation still 'running' past the staleness threshold
]


class AuditError(ChunkVaultError):
    """The audit itself could not run (not: it found problems)."""

    code = "audit_failed"
    status_code = 500


@dataclass(frozen=True)
class Finding:
    kind: FindingKind
    subject: str
    detail: str


@dataclass(frozen=True)
class AuditReport:
    chunks_checked: int
    files_checked: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.findings


@dataclass(frozen=True)
class RepairAction:
    action: str  # e.g. 'delete_staging_file', 'abort_operation', 'delete_orphan_chunk'
    subject: str
    detail: str


@dataclass(frozen=True)
class ReconciliationReport:
    actions: list[RepairAction]
    remaining: AuditReport  # audit run AFTER repair; whatever is left needs human attention


class IntegrityAuditor(Protocol):
    def verify_file(self, file_id: str) -> AuditReport:
        """Check one file: manifest shape, every chunk's presence/size/SHA-256, and the
        reassembled whole-file SHA-256. Read-only. Raises ``FileNotRestorableError``-like
        ``NotFoundError`` if the file id is unknown; damage is reported as findings."""
        ...

    def verify_store(self, verify_chunk_bytes: bool = True) -> AuditReport:
        """Audit everything. Read-only: never deletes, rewrites or updates anything."""
        ...


class Reconciler(Protocol):
    def reconcile(self) -> ReconciliationReport:
        """Explicitly invoked repair, run under the writer lock so nothing is in flight.

        Does only these safe things, reporting each: delete leftover staging files;
        mark stuck 'running' operations 'aborted'; mark 'uploading' files 'failed';
        delete chunk rows with ref_count 0 and then their bytes; delete chunk files
        that have no metadata row. It never deletes a chunk with ref_count > 0 and
        never rewrites or 'repairs' missing/corrupt referenced chunks or counters --
        those stay in ``remaining``. Idempotent."""
        ...
