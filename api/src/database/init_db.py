"""Database initialization helpers (for VM/production)."""

from __future__ import annotations

from sqlalchemy import text

from src.database import (  # noqa: F401  (register tables for Alembic metadata)
    models as _models,
)
from src.database.migrations import ensure_database_schema
from src.database.session import engine


def init_db_for_engine(target_engine) -> None:
    """Ensure the target database matches the Alembic head revision."""
    with target_engine.connect() as conn:
        ensure_database_schema(conn)


def init_db() -> None:
    """Ensure the default application database matches Alembic head."""
    init_db_for_engine(engine)


def ping_db(target_engine=None) -> None:
    """Fail fast when DB is required but unavailable."""
    target_engine = target_engine or engine
    with target_engine.connect() as conn:
        conn.execute(text("SELECT 1"))
