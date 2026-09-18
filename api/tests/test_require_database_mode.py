"""Regression tests for REQUIRE_DATABASE semantics (no silent in-memory fallbacks)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from src.api.main import app
from src.calculation.unit_converter import UnitConversionError, UnitConverter
from src.config.settings import settings
from src.services.hierarchy_store import get_hierarchy_store
from src.services.value_store import get_value_store


def test_require_database_startup_fails_fast_when_ping_db_fails(monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(
        settings,
        "jwt_secret_key",
        SecretStr("test-secret-key-with-enough-entropy-for-startup"),
    )
    monkeypatch.setattr(settings, "allowed_origins", ["http://localhost:8090"])

    import src.database.init_db as init_db_mod

    ping_db_calls = 0

    def _boom() -> None:
        nonlocal ping_db_calls
        ping_db_calls += 1
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(init_db_mod, "ping_db", _boom)
    monkeypatch.setattr(init_db_mod, "init_db", lambda: None)

    import asyncio

    loop = asyncio.new_event_loop()
    policy = asyncio.get_event_loop_policy()
    previous_loop = getattr(getattr(policy, "_local", None), "_loop", None)

    asyncio.set_event_loop(loop)
    cm = app.router.lifespan_context(app)
    try:
        with pytest.raises(RuntimeError, match="db unavailable"):
            loop.run_until_complete(cm.__aenter__())
        assert ping_db_calls == 1
    finally:
        try:
            loop.run_until_complete(cm.__aexit__(None, None, None))
        except Exception:
            pass

        try:
            loop.close()
        finally:
            if previous_loop is not None and not previous_loop.is_closed():
                asyncio.set_event_loop(previous_loop)
            else:
                asyncio.set_event_loop(None)

        # If startup partially succeeded before failing, ensure we don't leak state.
        for attr in ("unit_converter", "value_store", "hierarchy_store"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)


def test_require_database_value_store_rejects_missing_db_without_inmemory(monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)

    request = MagicMock()
    request.app = app

    if hasattr(app.state, "value_store"):
        delattr(app.state, "value_store")

    with pytest.raises(HTTPException) as exc:
        get_value_store(request=request, db=None)
    assert exc.value.status_code == 503
    assert not hasattr(app.state, "value_store")


def test_require_database_hierarchy_store_rejects_missing_db_without_inmemory(
    monkeypatch,
):
    monkeypatch.setattr(settings, "require_database", True)

    request = MagicMock()
    request.app = app

    if hasattr(app.state, "hierarchy_store"):
        delattr(app.state, "hierarchy_store")

    with pytest.raises(HTTPException) as exc:
        get_hierarchy_store(request=request, db=None)
    assert exc.value.status_code == 503
    assert not hasattr(app.state, "hierarchy_store")


def test_require_database_unit_converter_rejects_missing_db_without_json_fallback(
    monkeypatch,
):
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "use_postgres_units", None)

    from src.calculation.json_strategy import JSONStrategy
    from src.calculation.postgres_strategy import PostgresStrategy

    monkeypatch.setattr(PostgresStrategy, "is_available", lambda self: False)

    def _fail_json_fallback(self, *args, **kwargs):
        raise AssertionError("DB-first unit converter must not use JSON fallback")

    monkeypatch.setattr(JSONStrategy, "__init__", _fail_json_fallback)

    with pytest.raises(UnitConversionError, match="PostgreSQL storage is required"):
        UnitConverter(db_session=None)
