"""experiments.heartbeat_at

Revision ID: f6a2e3d5b8c0
Revises: e5f1d2c4a7b9
Create Date: 2026-09-06 14:00:00.000000

Adds a worker liveness heartbeat to experiments (E-25). The recovery routine
keys RUNNING-campaign staleness on ``heartbeat_at`` instead of ``created_at``,
so legitimately long campaigns are no longer swept and killed just because the
campaign row is old.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a2e3d5b8c0"
down_revision: Union[str, None] = "e5f1d2c4a7b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("experiments", "heartbeat_at")