"""attack_lineage

Revision ID: c3e1a9d8b2f7
Revises: aa70f58fa461
Create Date: 2026-09-06 10:30:00.000000

Adds the mutation-lineage columns to ``attacks`` consumed by the Phase 3
feedback-driven mutation engine:

  * ``parent_attack_id``  — self FK; root attacks are NULL, mutations point at
    the attack they evolved from.
  * ``round_number``      — campaign round the attack was created in.

The migration is SQLite-safe via ``batch_alter_table`` (SQLite cannot ALTER
ADD a foreign key constraint directly).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3e1a9d8b2f7"
down_revision: Union[str, None] = "aa70f58fa461"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("attacks") as batch_op:
            batch_op.add_column(
                sa.Column("parent_attack_id", sa.Uuid(), nullable=True)
            )
            batch_op.add_column(
                sa.Column(
                    "round_number", sa.Integer(), nullable=False, server_default="1"
                )
            )
            batch_op.create_foreign_key(
                "fk_attacks_parent_attack_id",
                "attacks",
                ["parent_attack_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch_op.create_index(
                op.f("ix_attacks_parent_attack_id"),
                ["parent_attack_id"],
                unique=False,
            )
    else:
        op.add_column("attacks", sa.Column("parent_attack_id", sa.Uuid(), nullable=True))
        op.add_column(
            "attacks",
            sa.Column("round_number", sa.Integer(), nullable=False, server_default="1"),
        )
        op.create_foreign_key(
            "fk_attacks_parent_attack_id",
            "attacks",
            ["parent_attack_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index(
            op.f("ix_attacks_parent_attack_id"), "attacks", ["parent_attack_id"], unique=False
        )


def downgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("attacks") as batch_op:
            batch_op.drop_index(op.f("ix_attacks_parent_attack_id"))
            batch_op.drop_constraint("fk_attacks_parent_attack_id", type_="foreignkey")
            batch_op.drop_column("round_number")
            batch_op.drop_column("parent_attack_id")
    else:
        op.drop_index(op.f("ix_attacks_parent_attack_id"), table_name="attacks")
        op.drop_constraint("fk_attacks_parent_attack_id", "attacks", type_="foreignkey")
        op.drop_column("attacks", "round_number")
        op.drop_column("attacks", "parent_attack_id")