"""vulnerabilities.evaluator_evidence + verifier_evidence

Revision ID: b1a2c3d4e5f6
Revises: f7b3e4c6d9d1
Create Date: 2026-09-06 16:00:00.000000

Persists the structured evaluator/verifier evidence lists (E-21) so the
campaign dashboard can display what each verdict was based on. Both columns are
JSON, nullable (legacy rows and heuristic-only evaluations have no evidence).

SQLite-safe via ``batch_alter_table``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1a2c3d4e5f6"
down_revision: Union[str, None] = "f7b3e4c6d9d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("vulnerabilities") as batch_op:
            batch_op.add_column(sa.Column("evaluator_evidence", sa.JSON(), nullable=True))
            batch_op.add_column(sa.Column("verifier_evidence", sa.JSON(), nullable=True))
    else:
        op.add_column(
            "vulnerabilities", sa.Column("evaluator_evidence", sa.JSON(), nullable=True)
        )
        op.add_column(
            "vulnerabilities", sa.Column("verifier_evidence", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("vulnerabilities") as batch_op:
            batch_op.drop_column("evaluator_evidence")
            batch_op.drop_column("verifier_evidence")
    else:
        op.drop_column("vulnerabilities", "evaluator_evidence")
        op.drop_column("vulnerabilities", "verifier_evidence")