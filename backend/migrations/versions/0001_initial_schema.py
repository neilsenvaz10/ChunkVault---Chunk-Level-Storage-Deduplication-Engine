"""Initial ChunkVault schema: tables, indexes, reference-count triggers, audit view, exposure control.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parents[1] / "sql"


def _run(name: str) -> None:
    # exec_driver_sql hands the file to the driver unchanged (no SQLAlchemy bind-parameter parsing of ':' or '$$').
    op.get_bind().exec_driver_sql((_SQL / name).read_text(encoding="utf-8"))


def upgrade() -> None:
    _run("0001_initial.sql")


def downgrade() -> None:
    _run("0001_downgrade.sql")
