"""Shared DB dependency probe for value-import performance tools."""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

DATABASE_UNAVAILABLE_EXIT_CODE = 2


class DatabaseUnavailable(RuntimeError):
    """Raised when a DB-backed value-import tool cannot evaluate."""


def _database_endpoint(db_url: str) -> str:
    try:
        url = make_url(db_url)
    except Exception:
        return "configured database URL"

    if not url.host:
        return url.database or "configured database URL"

    endpoint = url.host
    if url.port:
        endpoint = f"{endpoint}:{url.port}"
    if url.database:
        endpoint = f"{endpoint}/{url.database}"
    return endpoint


def _engine_kwargs(db_url: str, *, connect_timeout: int) -> dict:
    kwargs: dict = {"pool_pre_ping": True}
    if db_url.startswith("postgresql"):
        kwargs["connect_args"] = {"connect_timeout": connect_timeout}
    return kwargs


def probe_database(db_url: str, *, operation_label: str) -> None:
    """Verify DB reachability before a performance run starts."""
    engine = create_engine(db_url, **_engine_kwargs(db_url, connect_timeout=5))
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        endpoint = _database_endpoint(db_url)
        raise DatabaseUnavailable(
            f"PostgreSQL unavailable; {operation_label} was not evaluated. "
            f"Check DATABASE_URL or start the SDS database ({endpoint})."
        ) from exc
    finally:
        dispose = getattr(engine, "dispose", None)
        if dispose:
            dispose()
