"""Database initialization helpers (for VM/production)."""

from __future__ import annotations

import sys

from sqlalchemy import text

from src.database import (  # noqa: F401  (register tables for Alembic metadata)
    models as _models,
)
from src.database.migrations import assert_database_schema_head, ensure_database_schema

# A schema helper imported by operational CLIs must not construct the default
# application engine (and load application settings) before CLI argument parsing.
engine = None


def _default_engine():
    if engine is not None:
        return engine
    from src.database.session import engine as application_engine

    return application_engine


def init_db_for_engine(target_engine, *, externally_managed: bool = False) -> None:
    """Ensure the target database matches the Alembic head revision."""
    with target_engine.connect() as conn:
        if externally_managed:
            assert_database_schema_head(conn)
        else:
            ensure_database_schema(conn)


def init_db(*, externally_managed: bool = False) -> None:
    """Ensure the default application database matches Alembic head."""
    init_db_for_engine(_default_engine(), externally_managed=externally_managed)


def ping_db(target_engine=None) -> None:
    """Fail fast when DB is required but unavailable."""
    target_engine = target_engine or _default_engine()
    with target_engine.connect() as conn:
        conn.execute(text("SELECT 1"))


if __name__ == "__main__":
    if sys.argv[1:] != ["--check-head-read-only"]:
        raise SystemExit("Only the read-only schema-head check is supported")
    try:
        init_db(externally_managed=True)
    except Exception:
        raise SystemExit("Database schema head check failed; runtime refused") from None
