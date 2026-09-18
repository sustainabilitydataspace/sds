"""Database session management."""

from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import settings

# Create engine
engine_kwargs = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
    "echo": settings.debug,  # Log SQL queries in debug mode
}

# Unwrap SecretStr for SQLAlchemy
_db_url = settings.database_url.get_secret_value()

# Avoid long hangs when PostgreSQL is not reachable in offline/test environments.
if _db_url.startswith("postgresql"):
    engine_kwargs["connect_args"] = {"connect_timeout": 3}
    engine_kwargs["pool_size"] = settings.db_pool_size
    engine_kwargs["max_overflow"] = settings.db_max_overflow
    engine_kwargs["pool_timeout"] = 30

engine = create_engine(_db_url, **engine_kwargs)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Dependency to get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_optional() -> Generator[Optional[Session], None, None]:
    """Optional DB dependency.

    Returns a Session only when settings.require_database is enabled. This keeps
    the test suite offline-safe (no Postgres required).
    """
    if not getattr(settings, "require_database", False):
        yield None
        return

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
