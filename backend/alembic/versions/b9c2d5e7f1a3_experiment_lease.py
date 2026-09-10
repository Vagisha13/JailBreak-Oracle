"""experiments.claim_owner + lease_expires_at

Revision ID: b9c2d5e7f1a3
Revises: b1a2c3d4e5f6
Create Date: 2026-09-10 09:00:00.000000

Multi-worker campaign lease (E-26): an atomic claim so two workers can never
execute the same campaign concurrently. ``claim_owner`` is the worker's per-job
token; ``lease_expires_at`` is the lease expiry, kept fresh by the same per-round
heartbeat recovery keys on. NULL means unclaimed.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9c2d5e7f1a3"
down_revision: Union[str, None] = "b1a2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column(
            "claim_owner",
            sa.Uuid(),  # portable across SQLite + PostgreSQL
            nullable=True,
        ),
    )
    op.add_column(
        "experiments",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("experiments", "lease_expires_at")
    op.drop_column("experiments", "claim_owner")
