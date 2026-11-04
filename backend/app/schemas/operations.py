"""Integrity / reconciliation schemas. (Benchmark and simulation schemas live in schemas/benchmark.py
and schemas/simulation.py.)"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FindingKindLiteral = Literal[
    "missing_chunk_file", "corrupt_chunk", "orphan_chunk_file", "unreferenced_chunk", "pending_chunk",
    "orphan_staging_file", "ref_count_mismatch", "manifest_inconsistent", "file_hash_mismatch",
    "incomplete_file", "stuck_operation",
]


class IntegrityCheckRequest(BaseModel):
    """POST /api/integrity/audit. Omit ``file_id`` to audit the whole store."""

    file_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    verify_chunk_bytes: bool = Field(default=True, description="Whole-store audit: re-hash stored bytes, not just check presence.")


class IntegrityFinding(BaseModel):
    kind: FindingKindLiteral
    subject: str = Field(description="Chunk hash, file id, operation id or staging file name.")
    detail: str


class IntegrityCheckResponse(BaseModel):
    """The audit is read-only: it changes nothing and records no operation."""

    healthy: bool
    chunks_checked: int = Field(ge=0)
    files_checked: int = Field(ge=0)
    findings: list[IntegrityFinding]
    counts_by_kind: dict[str, int]


class RepairActionModel(BaseModel):
    action: str
    subject: str
    detail: str


class ReconcileResponse(BaseModel):
    """POST /api/integrity/reconcile. Explicit repair; every action is listed."""

    actions: list[RepairActionModel]
    remaining: IntegrityCheckResponse
