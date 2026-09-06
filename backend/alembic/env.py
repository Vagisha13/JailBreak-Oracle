from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import context

# Import our models and settings
from app.core.config import settings
from app.db.base import Base
import app.models  # Ensures all models are registered

config = context.config

# Override sqlalchemy.url with our settings config
config.set_main_option("sqlalchemy.url", settings.SYNC_DATABASE_URL)

if config.config_file_name is not None:
    # Never disable already-configured application loggers (e.g. the structured
    # JSON loggers from app/core/logging.py). Alembic's own template defaults to
    # disable_existing_loggers=True, which silently kills app log output for the
    # rest of the process once a migration runs in-process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # pgvector is required for the `attacks.embedding` column. CREATE
        # EXTENSION is PostgreSQL-only; SQLite (dev/tests) has no extensions.
        if connection.dialect.name == "postgresql":
            connection.execute(
                __import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS vector;")
            )
            connection.commit()

        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
