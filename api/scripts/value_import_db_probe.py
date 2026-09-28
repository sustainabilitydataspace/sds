"""Shared DB dependency probe for value-import performance tools."""

from __future__ import annotations

import os
import re

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from scripts.operational_db_url import resolve_operational_database_url

DATABASE_UNAVAILABLE_EXIT_CODE = 2


class DatabaseUnavailable(RuntimeError):
    """Raised when a DB-backed value-import tool cannot evaluate."""


def require_disposable_value_import_target(db_url: str) -> None:
    """Admit only an explicitly provisioned loopback disposable test database.

    This is a misuse guard, not proof of instance ownership: a separate test
    PostgreSQL instance and disposable credentials must be provisioned externally.
    """
    refusal = "Value-import benchmark requires a separately provisioned disposable PostgreSQL 15 database"
    if (
        os.getenv("SDS_VALUE_IMPORT_DISPOSABLE_ALLOW_WRITE") != "true"
        or os.getenv("SDS_VALUE_IMPORT_DISPOSABLE_DATABASE_URL") != db_url
    ):
        raise ValueError(refusal)
    try:
        resolve_operational_database_url(db_url)
        url = make_url(db_url)
        if (
            not url.drivername.startswith("postgresql")
            or url.host not in {"127.0.0.1", "localhost", "::1"}
            or url.port is None
            or not re.fullmatch(
                r"sds_value_import_disposable_[a-z0-9]{8,40}", url.database or ""
            )
        ):
            raise ValueError(refusal)
    except (ValueError, TypeError):
        raise ValueError(refusal) from None


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


def probe_database(
    db_url: str, *, operation_label: str, require_pg15: bool = False
) -> None:
    """Verify DB reachability before a performance run starts."""
    engine = create_engine(db_url, **_engine_kwargs(db_url, connect_timeout=5))
    try:
        with engine.connect() as conn:
            if require_pg15:
                version = int(
                    conn.execute(text("SHOW server_version_num")).scalar_one()
                )
                if not 150000 <= version < 160000:
                    raise DatabaseUnavailable(
                        "PostgreSQL 15 required; value-import benchmark was not evaluated"
                    )
            conn.execute(text("SELECT 1"))
    except OperationalError:
        endpoint = _database_endpoint(db_url)
        raise DatabaseUnavailable(
            f"PostgreSQL unavailable; {operation_label} was not evaluated. "
            f"Check DATABASE_URL or start the SDS database ({endpoint})."
        ) from None
    finally:
        dispose = getattr(engine, "dispose", None)
        if dispose:
            dispose()
