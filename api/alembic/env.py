"""Alembic environment for SustainabilityDataSpace API."""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context
from src.config.settings import settings
from src.database import models as _models  # noqa: F401  (register metadata)
from src.database.alembic_compare import compare_server_default
from src.database.alembic_version import ensure_alembic_version_table_capacity
from src.database.base import Base

config = context.config
MIGRATION_LOCK_KEY = 640_104_214

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url.get_secret_value())
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in offline mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=compare_server_default,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    connection = config.attributes.get("connection")

    if connection is not None:
        _run_migrations_with_connection(connection)
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as online_connection:
        _run_migrations_with_connection(online_connection)


def _run_migrations_with_connection(connection) -> None:
    """Run Alembic migrations under a shared advisory lock on PostgreSQL."""
    lock_acquired = False
    if connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
        )
        connection.commit()
        lock_acquired = True

    try:
        ensure_alembic_version_table_capacity(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=compare_server_default,
        )
        with context.begin_transaction():
            context.run_migrations()
    finally:
        if lock_acquired:
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY}
            )
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
