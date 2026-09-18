"""Helpers for Alembic version-table compatibility."""

from __future__ import annotations

from sqlalchemy import Connection, text

ALEMBIC_VERSION_NUM_LENGTH = 128


def ensure_alembic_version_table_capacity(connection: Connection) -> None:
    """Ensure Alembic can store SDS revision identifiers on PostgreSQL.

    Alembic's default PostgreSQL version table uses ``VARCHAR(32)``. SDS revision
    identifiers are intentionally descriptive and can exceed that default, so
    existing production databases must be widened before Alembic writes the next
    revision.
    """
    if connection.dialect.name != "postgresql":
        return

    connection.execute(text(f"""
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR({ALEMBIC_VERSION_NUM_LENGTH}) NOT NULL,
                CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
            )
            """))
    connection.execute(
        text(
            "ALTER TABLE alembic_version "
            f"ALTER COLUMN version_num TYPE VARCHAR({ALEMBIC_VERSION_NUM_LENGTH})"
        )
    )
    connection.commit()
