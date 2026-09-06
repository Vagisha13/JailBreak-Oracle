"""experiments.max_cost_usd

Revision ID: f7b3e4c6d9d1
Revises: f6a2e3d5b8c0
Create Date: 2026-09-06 15:00:00.000000

Persists the per-campaign cost cap so the ledger/summary surface can report it.
NULL means "use the global MAX_CAMPAIGN_COST"; the orchestrator already reads
``CampaignConfig.max_cost_usd`` (default global) at run time.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7b3e4c6d9d1"
down_revision: Union[str, None] = "f6a2e3d5b8c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("max_cost_usd", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("experiments", "max_cost_usd")