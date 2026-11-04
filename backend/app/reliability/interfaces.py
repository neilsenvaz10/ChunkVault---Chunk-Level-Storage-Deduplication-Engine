"""Integrity auditor and reconciliation: interfaces only (Phase 5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.core.errors import ChunkVaultError

FindingKind = Literal[
    "missing_chunk_file",
    "corrupt_chunk",
    "orphan_chunk_file",
    "orphan_staging_file",
    "ref_count_mismatch",
    "file_hash_mismatch",
    "stuck_operation",
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
    findings: list[Finding] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.findings


class IntegrityAuditor(Protocol):
    def verify_file(self, file_id: str) -> AuditReport:
        """Check that every chunk of one file exists, re-hashes correctly, and that
        the reassembled hash equals files.sha256. Read-only. Raises ``AuditError``
        only if the audit cannot run; damage is reported as findings."""
        ...

    def verify_store(self) -> AuditReport:
        """Audit everything: chunk bytes vs hashes, ref_count invariant,
        files<->chunk-store orphans, leftover staging files, stuck operations."""
        ...

    def reconcile(self) -> AuditReport:
        """Repair what is safe to repair after a crash: delete stale staging
        files, mark stuck operations 'aborted', fail 'uploading' files, delete
        chunk files unreferenced by metadata, mark mismatching chunks 'corrupt'.
        Never deletes a chunk whose ref_count > 0. Idempotent."""
        ...
