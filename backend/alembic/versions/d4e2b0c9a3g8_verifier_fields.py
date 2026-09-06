"""verifier_fields

Revision ID: d4e2b0c9a3g8
Revises: c3e1a9d8b2f7
Create Date: 2026-09-06 12:00:00.000000

Adds the persisted independent-verifier fields to ``vulnerabilities`` (E-09)
so the verifier's structured output is retained instead of being folded into
``reasoning``:

  * ``verification_reasoning`` — Text, null
  * ``remediation_guidance``    — Text, null
  * ``verifier_confidence``     — Float, null
  * ``verified_at``             — DateTime(timezone), null

SQLite-safe via ``batch_alter_table``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e2b0c9a3g8"
down_revision: Union[str, None] = "c3e1a9d8b2f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("vulnerabilities") as batch_op:
            batch_op.add_column(sa.Column("verification_reasoning", sa.Text(), nullable=True))
            batch_op.add_column(sa.Column("remediation_guidance", sa.Text(), nullable=True))
            batch_op.add_column(sa.Column("verifier_confidence", sa.Float(), nullable=True))
            batch_op.add_column(
                sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True)
            )
    else:
        op.add_column(
            "vulnerabilities", sa.Column("verification_reasoning", sa.Text(), nullable=True)
        )
        op.add_column(
            "vulnerabilities", sa.Column("remediation_guidance", sa.Text(), nullable=True)
        )
        op.add_column(
            "vulnerabilities", sa.Column("verifier_confidence", sa.Float(), nullable=True)
        )
        op.add_column(
            "vulnerabilities",
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("vulnerabilities") as batch_op:
            batch_op.drop_column("verified_at")
            batch_op.drop_column("verifier_confidence")
            batch_op.drop_column("remediation_guidance")
            batch_op.drop_column("verification_reasoning")
    else:
        op.drop_column("vulnerabilities", "verified_at")
        op.drop_column("vulnerabilities", "verifier_confidence")
        op.drop_column("vulnerabilities", "remediation_guidance")
        op.drop_column("vulnerabilities", "verification_reasoning")