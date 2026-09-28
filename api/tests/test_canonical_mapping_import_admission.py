"""Offline error-path tests for PostgreSQL import admission and cleanup.

The actual cross-worker/pool behavior is asserted separately against disposable PG15.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session

from src.api.routers import mapping_assertions
from src.database.repositories.canonical_mapping_package_job_repository import (
    CanonicalMappingImportBusy,
    canonical_mapping_import_session,
)
from src.services.canonical_mapping_package_job_store import (
    InMemoryCanonicalMappingPackageJobStore,
)


def _synthetic_advisory_engine(*, acquire=True, unlock=True):
    """Only exercise SQLAlchemy cleanup paths, not PostgreSQL lock semantics."""
    engine = create_engine("sqlite:///:memory:")
    engine.dialect.name = "postgresql"
    invalidations = []

    @event.listens_for(engine, "connect")
    def add_functions(dbapi_connection, _record):
        dbapi_connection.create_function(
            "pg_try_advisory_lock", 1, lambda _: int(acquire)
        )
        dbapi_connection.create_function("pg_advisory_unlock", 1, lambda _: int(unlock))

    @event.listens_for(engine, "invalidate")
    def on_invalidate(_connection, _record, _error):
        invalidations.append(True)

    return engine, invalidations


def test_import_session_commits_job_state_on_one_connection_and_unlocks():
    engine, invalidations = _synthetic_advisory_engine()
    try:
        with canonical_mapping_import_session(engine) as session:
            assert session.execute(text("SELECT 42")).scalar_one() == 42
            session.commit()
            assert session.execute(text("SELECT 43")).scalar_one() == 43
        assert not invalidations
    finally:
        engine.dispose()


def test_import_session_rejects_busy_without_entering_body():
    engine, invalidations = _synthetic_advisory_engine(acquire=False)
    try:
        with pytest.raises(CanonicalMappingImportBusy, match="busy"):
            with canonical_mapping_import_session(engine):
                pytest.fail("A busy lock must never admit the body")
        assert not invalidations
    finally:
        engine.dispose()


def test_import_session_invalidates_if_unlock_is_unconfirmed():
    engine, invalidations = _synthetic_advisory_engine(unlock=False)
    try:
        with pytest.raises(RuntimeError, match="lock release unconfirmed"):
            with canonical_mapping_import_session(engine) as session:
                session.execute(text("SELECT 1"))
        assert invalidations
    finally:
        engine.dispose()


def test_import_session_rejects_non_postgresql_engine():
    engine = create_engine("sqlite:///:memory:")
    try:
        with pytest.raises(RuntimeError, match="requires PostgreSQL"):
            with canonical_mapping_import_session(engine):
                pytest.fail("Unsupported database must not enter import")
    finally:
        engine.dispose()


class _InMemoryDatabaseStore(InMemoryCanonicalMappingPackageJobStore):
    """Exercise router orchestration without replacing SQL-level PG15 tests."""

    def __init__(self, _db):
        super().__init__()


@pytest.mark.asyncio
async def test_router_closes_preflight_session_and_offloads_import(
    monkeypatch, tmp_path
):
    db = MagicMock(spec=Session)
    db.get_bind.return_value = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql")
    )
    monkeypatch.setattr(
        mapping_assertions, "_resolve_allowed_package_id", lambda _: tmp_path
    )
    monkeypatch.setattr(
        mapping_assertions,
        "DatabaseCanonicalMappingPackageJobStore",
        _InMemoryDatabaseStore,
    )

    @contextmanager
    def admitted(_engine):
        yield db

    monkeypatch.setattr(
        mapping_assertions, "canonical_mapping_import_session", admitted
    )
    report = SimpleNamespace(
        valid=True,
        blocked=False,
        blockers=[],
        compatibility={
            "status_counts": {},
            "importable_assertion_group_count": 0,
        },
        committed=False,
        as_dict=lambda: {"valid": True},
    )
    monkeypatch.setattr(
        mapping_assertions, "import_canonical_mapping_package_to_db", lambda **_: report
    )
    entered = threading.Event()
    release = threading.Event()

    def slow_preflight(_releases, *, db):
        entered.set()
        assert release.wait(timeout=0.4)
        return set()

    monkeypatch.setattr(
        mapping_assertions, "_installed_release_keys_from_request", slow_preflight
    )
    started = asyncio.get_running_loop().time()
    request = asyncio.create_task(
        mapping_assertions.submit_mapping_package_import_job(
            "pkg",
            mapping_assertions.CanonicalMappingPackageImportRequest(
                installed_standard_releases=[]
            ),
            db=db,
            job_store=_InMemoryDatabaseStore(db),
            user=SimpleNamespace(username="operator"),
        )
    )
    try:
        await asyncio.sleep(0.01)
        assert asyncio.get_running_loop().time() - started < 0.15
        assert entered.is_set()
    finally:
        release.set()
    response = await request
    assert response.status == "completed"
    db.rollback.assert_called_once()
    db.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(CanonicalMappingImportBusy("busy"), 409), (SQLAlchemyTimeoutError("pool"), 503)],
)
async def test_router_fails_closed_when_import_admission_fails(
    monkeypatch,
    tmp_path,
    error,
    expected_status,
):
    db = MagicMock(spec=Session)
    db.get_bind.return_value = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql")
    )
    monkeypatch.setattr(
        mapping_assertions, "_resolve_allowed_package_id", lambda _: tmp_path
    )
    monkeypatch.setattr(
        mapping_assertions,
        "DatabaseCanonicalMappingPackageJobStore",
        _InMemoryDatabaseStore,
    )

    @contextmanager
    def unavailable(_engine):
        raise error
        yield  # pragma: no cover

    monkeypatch.setattr(
        mapping_assertions, "canonical_mapping_import_session", unavailable
    )
    store = _InMemoryDatabaseStore(db)
    with pytest.raises(HTTPException) as exc:
        await mapping_assertions.submit_mapping_package_import_job(
            "pkg",
            mapping_assertions.CanonicalMappingPackageImportRequest(
                installed_standard_releases=[]
            ),
            db=db,
            job_store=store,
            user=SimpleNamespace(username="operator"),
        )
    assert exc.value.status_code == expected_status
    assert not store.has_active_import()
    db.close.assert_called_once()


@pytest.mark.parametrize(
    ("status", "mode", "releases", "expected_detail"),
    [
        ("pending", "strict", [], "not completed"),
        ("completed", "partial", [], "compatibility_mode"),
        ("completed", "strict", [], "installed_standard_releases"),
    ],
)
def test_import_validation_must_match_state_mode_and_installed_slice(
    status,
    mode,
    releases,
    expected_detail,
):
    validation = SimpleNamespace(
        status=status,
        request_metadata={
            "compatibility_mode": mode,
            "installed_standard_releases": releases,
        },
        result_body={
            "compatibility": {
                "installed_standard_releases": [
                    {"standard_id": "GRI", "version": "2024"}
                ]
            }
        },
    )
    with pytest.raises(HTTPException, match=expected_detail) as exc:
        mapping_assertions._assert_validation_matches_import(
            validation,
            request_body=mapping_assertions.CanonicalMappingPackageImportRequest(
                installed_standard_releases=[{"standard_id": "ESRS", "version": "2024"}]
            ),
            installed_releases={("ESRS", "2024")},
        )
    assert exc.value.status_code == 409
