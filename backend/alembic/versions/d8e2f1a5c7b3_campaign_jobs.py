"""campaign_jobs — durable PostgreSQL campaign queue

Revision ID: d8e2f1a5c7b3
Revises: c7d4e6a2f9b8
Create Date: 2026-09-11 10:00:00.000000

Replaces the single Redis list (``oracle:campaign:jobs``) with a durable,
retryable PostgreSQL-backed job queue. Queue state (status, attempts,
``available_at``, ``last_error``, worker heartbeat) lives on its own table so
the ``experiments`` row keeps owning campaign domain state.

Semantics:
  * A worker claims a job atomically (row lock ``FOR UPDATE SKIP LOCKED`` +
    status CAS) and executes, then marks it COMPLETED/FAILED.
  * Failed jobs retry with backoff until ``CAMPAIGN_JOB_MAX_ATTEMPTS``; a
    worker crash leaves the job RUNNING for stale recovery to re-admit.
  * A partial unique index on ``(experiment_id)`` (only PENDING/RUNNING rows)
    makes enqueue idempotent while still allowing a *fresh* job after a
    terminal one (e.g. an experiment re-queued post-recovery).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d8e2f1a5c7b3"
down_revision: Union[str, None] = "c7d4e6a2f9b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "campaign_jobs",
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"], ["experiments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_campaign_jobs_experiment_id"),
        "campaign_jobs",
        ["experiment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_campaign_jobs_status"),
        "campaign_jobs",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_campaign_jobs_available_at"),
        "campaign_jobs",
        ["available_at"],
        unique=False,
    )
    # At most one ACTIVE job per experiment: makes enqueue idempotent while
    # terminal rows remain history and a later re-enqueue creates a fresh job.
    op.create_index(
        "uq_campaign_jobs_active_experiment",
        "campaign_jobs",
        ["experiment_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('PENDING', 'RUNNING')"),
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index("uq_campaign_jobs_active_experiment", table_name="campaign_jobs")
    op.drop_index(op.f("ix_campaign_jobs_available_at"), table_name="campaign_jobs")
    op.drop_index(op.f("ix_campaign_jobs_status"), table_name="campaign_jobs")
    op.drop_index(op.f("ix_campaign_jobs_experiment_id"), table_name="campaign_jobs")
    op.drop_table("campaign_jobs")