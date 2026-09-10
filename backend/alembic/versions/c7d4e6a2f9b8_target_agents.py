"""target agents: targets.is_enabled, experiments.target_snapshot_json,
attack_results.status_code + error_type

Revision ID: c7d4e6a2f9b8
Revises: b9c2d5e7f1a3
Create Date: 2026-09-10 12:00:00.000000

Target agents (S3):
  * ``targets.is_enabled`` — soft-disable a target so it cannot be used for new
    campaigns while keeping its configuration/history.
  * ``experiments.target_snapshot_json`` — immutable snapshot of the target
    config a campaign started with, so reports/status stay deterministic after
    the target is edited or deleted.
  * ``attack_results.status_code`` / ``error_type`` — controlled failure
    attribution (E-27): classify provider/HTTP failures so "which targets are
    timing out?" is a query, not log spelunking.

SQLite-safe via ``batch_alter_table``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d4e6a2f9b8"
down_revision: Union[str, None] = "b9c2d5e7f1a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("targets") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "is_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("1"),
                )
            )
        with op.batch_alter_table("experiments") as batch_op:
            batch_op.add_column(sa.Column("target_snapshot_json", sa.JSON(), nullable=True))
        with op.batch_alter_table("attack_results") as batch_op:
            batch_op.add_column(sa.Column("status_code", sa.Integer(), nullable=True))
            batch_op.add_column(
                sa.Column("error_type", sa.String(length=50), nullable=True)
            )
    else:
        op.add_column(
            "targets",
            sa.Column(
                "is_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )
        op.add_column(
            "experiments",
            sa.Column("target_snapshot_json", sa.JSON(), nullable=True),
        )
        op.add_column("attack_results", sa.Column("status_code", sa.Integer(), nullable=True))
        op.add_column(
            "attack_results",
            sa.Column("error_type", sa.String(length=50), nullable=True),
        )


def downgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("attack_results") as batch_op:
            batch_op.drop_column("error_type")
            batch_op.drop_column("status_code")
        with op.batch_alter_table("experiments") as batch_op:
            batch_op.drop_column("target_snapshot_json")
        with op.batch_alter_table("targets") as batch_op:
            batch_op.drop_column("is_enabled")
    else:
        op.drop_column("attack_results", "error_type")
        op.drop_column("attack_results", "status_code")
        op.drop_column("experiments", "target_snapshot_json")
        op.drop_column("targets", "is_enabled")