"""Wave 5 tests for DB-first public catalog routers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import src.api.routers.indicators as indicators_router
import src.api.routers.mappings as mappings_router
from src.config.settings import settings


def _failing_db() -> MagicMock:
    db = MagicMock()
    db.query.side_effect = RuntimeError("db unavailable")
    return db


@pytest.mark.asyncio
async def test_indicators_router_returns_503_without_db_in_db_first(monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)

    with pytest.raises(HTTPException) as exc:
        await indicators_router.list_indicators(
            limit=10,
            offset=0,
            db=None,
            user=MagicMock(),
        )

    assert exc.value.status_code == 503
    assert "Canonical indicator data is unavailable" in exc.value.detail


@pytest.mark.asyncio
async def test_indicators_router_returns_503_on_query_failure_in_db_first(monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)

    with pytest.raises(HTTPException) as exc:
        await indicators_router.list_indicators(
            limit=10,
            offset=0,
            db=_failing_db(),
            user=MagicMock(),
        )

    assert exc.value.status_code == 503
    assert "Canonical indicator data is unavailable" in exc.value.detail


@pytest.mark.asyncio
async def test_mappings_router_returns_503_without_db_in_db_first(monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)

    with pytest.raises(HTTPException) as exc:
        await mappings_router.list_mappings(
            limit=10,
            offset=0,
            db=None,
            user=MagicMock(),
        )

    assert exc.value.status_code == 503
    assert "Canonical mapping data is unavailable" in exc.value.detail


@pytest.mark.asyncio
async def test_mappings_router_returns_503_on_query_failure_in_db_first(monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)

    with pytest.raises(HTTPException) as exc:
        await mappings_router.list_mappings(
            limit=10,
            offset=0,
            db=_failing_db(),
            user=MagicMock(),
        )

    assert exc.value.status_code == 503
    assert "Canonical mapping data is unavailable" in exc.value.detail
