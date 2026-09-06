"""token_usage

Revision ID: e5f1d2c4a7b9
Revises: d4e2b0c9a3g8
Create Date: 2026-09-06 13:00:00.000000

Adds the per-call token/cost ledger table for campaign budget enforcement
(E-12). ``TokenTracker`` persists every LLM call (attacker, target, evaluator,
verifier) here so ``MAX_CAMPAIGN_COST`` is enforced across worker restarts /
campaign resumes instead of restarting at zero.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f1d2c4a7b9"
down_revision: Union[str, None] = "d4e2b0c9a3g8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "token_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "experiment_id",
            sa.Uuid(),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_token_usage_experiment_id", "token_usage", ["experiment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_token_usage_experiment_id", table_name="token_usage")
    op.drop_table("token_usage")