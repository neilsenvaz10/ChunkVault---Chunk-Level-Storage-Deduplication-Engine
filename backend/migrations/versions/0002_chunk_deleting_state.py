"""Durable 'deleting' chunk state for the cloud garbage-collection protocol.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parents[1] / "sql"


def _run(name: str) -> None:
    op.get_bind().exec_driver_sql((_SQL / name).read_text(encoding="utf-8"))


def upgrade() -> None:
    _run("0002_chunk_deleting_state.sql")


def downgrade() -> None:
    _run("0002_downgrade.sql")
