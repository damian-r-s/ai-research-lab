"""Mark finished runs whose LangGraph checkpoints were pruned (criterion 2.11).

Revision ID: 0004_checkpoint_pruning
Revises: 0003_runs
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_checkpoint_pruning"
down_revision: str | None = "0003_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade(app_role: str, **_: str) -> None:
    op.add_column(
        "research_runs",
        sa.Column("checkpoints_pruned_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        schema="lab",
    )


def downgrade(app_role: str, **_: str) -> None:
    op.drop_column("research_runs", "checkpoints_pruned_at", schema="lab")
