"""Valid state values. These must match the CHECK constraints in schema.sql
(tests/test_database_schema.py verifies the two stay in sync)."""

from __future__ import annotations

from enum import StrEnum


class FileStatus(StrEnum):
    UPLOADING = "uploading"
    COMPLETE = "complete"
    FAILED = "failed"
    DELETING = "deleting"


class ChunkState(StrEnum):
    PENDING = "pending"
    STORED = "stored"
    CORRUPT = "corrupt"


class OperationType(StrEnum):
    UPLOAD = "upload"
    RESTORE = "restore"
    DELETE = "delete"
    INTEGRITY_CHECK = "integrity_check"
    RECONCILIATION = "reconciliation"
    BENCHMARK = "benchmark"
    SIMULATION = "simulation"


class OperationStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"
