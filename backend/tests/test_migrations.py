"""Alembic migration round-trip checks (E-21 evidence columns).

Runs the real migration chain on a fresh, isolated SQLite file — never the test
DB — and verifies upgrade -> downgrade base -> upgrade is lossless for the
``vulnerabilities.evaluator_evidence`` / ``verifier_evidence`` columns.
"""
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import settings
from app.models.domain import Vulnerability, CampaignJob


def _table_names(db_url: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            return set(inspector.get_table_names())
    finally:
        engine.dispose()


def _column_names(db_url: str, table: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            return {col["name"] for col in inspector.get_columns(table)}
    finally:
        engine.dispose()


def _orm_column_names(model) -> set[str]:
    return set(model.__table__.columns.keys())


def test_evidence_columns_migration_round_trip(tmp_path):
    db_path = tmp_path / "migration_check.db"
    original_sync = settings.SYNC_DATABASE_URL
    original_async = settings.ASYNC_DATABASE_URL
    settings.SYNC_DATABASE_URL = f"sqlite:///{db_path}"
    settings.ASYNC_DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
    try:
        cfg = Config("alembic.ini")

        command.upgrade(cfg, "head")
        columns = _column_names(settings.SYNC_DATABASE_URL, "vulnerabilities")
        assert "evaluator_evidence" in columns
        assert "verifier_evidence" in columns
        # schema parity: migration-created table matches the ORM exactly.
        assert columns == _orm_column_names(Vulnerability)

        command.downgrade(cfg, "f7b3e4c6d9d1")
        columns = _column_names(settings.SYNC_DATABASE_URL, "vulnerabilities")
        assert "evaluator_evidence" not in columns
        assert "verifier_evidence" not in columns

        command.upgrade(cfg, "head")
        columns = _column_names(settings.SYNC_DATABASE_URL, "vulnerabilities")
        assert "evaluator_evidence" in columns
        assert "verifier_evidence" in columns
        assert columns == _orm_column_names(Vulnerability)
    finally:
        settings.SYNC_DATABASE_URL = original_sync
        settings.ASYNC_DATABASE_URL = original_async


def test_campaign_jobs_migration_round_trip(tmp_path):
    """campaign_jobs table exists at head and is dropped/recreated cleanly."""
    db_path = tmp_path / "migration_campaign.db"
    original_sync = settings.SYNC_DATABASE_URL
    original_async = settings.ASYNC_DATABASE_URL
    settings.SYNC_DATABASE_URL = f"sqlite:///{db_path}"
    settings.ASYNC_DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
    try:
        cfg = Config("alembic.ini")

        command.upgrade(cfg, "head")
        assert "campaign_jobs" in _table_names(settings.SYNC_DATABASE_URL)
        columns = _column_names(settings.SYNC_DATABASE_URL, "campaign_jobs")
        assert columns == _orm_column_names(CampaignJob)

        command.downgrade(cfg, "c7d4e6a2f9b8")
        assert "campaign_jobs" not in _table_names(settings.SYNC_DATABASE_URL)

        command.upgrade(cfg, "head")
        assert "campaign_jobs" in _table_names(settings.SYNC_DATABASE_URL)
        assert _column_names(settings.SYNC_DATABASE_URL, "campaign_jobs") == _orm_column_names(
            CampaignJob
        )
    finally:
        settings.SYNC_DATABASE_URL = original_sync
        settings.ASYNC_DATABASE_URL = original_async
