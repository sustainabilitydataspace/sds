"""Alembic helpers for database schema management."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Connection, inspect

from alembic import command
from alembic.config import Config

BASELINE_REVISION = "001_baseline_schema"
HEAD_REVISION = "head"

SERVICE_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI_PATH = SERVICE_ROOT / "alembic.ini"
ALEMBIC_SCRIPT_PATH = SERVICE_ROOT / "alembic"


def _alembic_config(connection: Connection) -> Config:
    """Build an Alembic config for the target database URL."""
    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_PATH))
    config.set_main_option(
        "sqlalchemy.url",
        connection.engine.url.render_as_string(hide_password=False),
    )
    config.attributes["connection"] = connection
    return config


def _has_alembic_version_table(connection: Connection) -> bool:
    """Return True when Alembic is already tracking this database."""
    return inspect(connection).has_table("alembic_version")


def _existing_user_tables(connection: Connection) -> set[str]:
    """List existing application tables excluding Alembic bookkeeping."""
    tables = set(inspect(connection).get_table_names())
    tables.discard("alembic_version")
    return tables


def ensure_database_schema(connection: Connection) -> None:
    """Ensure the database is at Alembic head.

    Existing deployments created before Alembic are stamped to the baseline
    revision and then upgraded. Fresh databases run the whole migration chain.
    """
    config = _alembic_config(connection)
    has_alembic = _has_alembic_version_table(connection)

    if not has_alembic and _existing_user_tables(connection):
        command.stamp(config, BASELINE_REVISION)

    command.upgrade(config, HEAD_REVISION)
