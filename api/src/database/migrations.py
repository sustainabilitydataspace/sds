"""Alembic helpers for database schema management."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Connection, inspect

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

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


def assert_database_schema_head(connection: Connection) -> None:
    """Read only: require every DB revision to match the image's exact heads."""
    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_PATH))
    expected = tuple(ScriptDirectory.from_config(config).get_heads())
    current = tuple(MigrationContext.configure(connection).get_current_heads())
    if (
        not expected
        or not current
        or len(current) != len(set(current))
        or set(current) != set(expected)
    ):
        raise RuntimeError(
            "Database is not at the approved Alembic head; startup refused"
        )


def ensure_database_schema(connection: Connection) -> None:
    """Ensure the database is at Alembic head.

    Fresh databases run the whole migration chain. Non-empty unversioned
    databases are never adopted automatically: a partial fingerprint cannot
    prove types, constraints, indexes, or provenance safely enough to stamp.
    """
    config = _alembic_config(connection)
    has_alembic = _has_alembic_version_table(connection)

    if not has_alembic and _existing_user_tables(connection):
        raise RuntimeError(
            "Refusing automatic adoption of unversioned non-empty database; "
            "restore a qualified versioned snapshot or complete an explicit "
            "operator-reviewed legacy migration before startup"
        )

    command.upgrade(config, HEAD_REVISION)
