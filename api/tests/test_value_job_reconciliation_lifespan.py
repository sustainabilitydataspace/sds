"""DB-required startup ordering, mode boundaries and sanitized fail-closed errors."""

import traceback
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from src.api import main
from src.config.settings import settings
from src.services.canonical_mapping_package_job_store import (
    DatabaseCanonicalMappingPackageJobStore,
)
from src.services.value_import_job_store import DatabaseValueImportJobStore


@pytest.fixture
def startup(monkeypatch):
    events = []
    sessions = []
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "use_postgres_units", None)
    monkeypatch.setattr(settings, "seed_default_users", False)
    monkeypatch.setattr(settings, "seed_reference_data_on_startup", False)
    monkeypatch.setattr(main, "_validate_security_settings", lambda: None)
    monkeypatch.setattr(main, "logger", MagicMock())
    monkeypatch.setattr(
        "src.database.init_db.ping_db", lambda: events.append("ping_db")
    )
    monkeypatch.setattr(
        "src.database.init_db.init_db",
        lambda *, externally_managed: events.append("init_db"),
    )

    def session_factory():
        db = MagicMock()
        db.close.side_effect = lambda: events.append("close")
        sessions.append(db)
        events.append("session")
        return db

    monkeypatch.setattr("src.database.session.SessionLocal", session_factory)
    monkeypatch.setattr(
        DatabaseCanonicalMappingPackageJobStore,
        "reconcile_legacy_active_jobs",
        lambda _store: events.append("mapping_reconcile") or 0,
    )
    for module, function in (
        ("bootstrap_semantic_model", "bootstrap_semantic_model_if_empty"),
        ("bootstrap_units", "bootstrap_units_if_empty"),
    ):
        monkeypatch.setattr(
            f"src.database.{module}.{function}",
            lambda _db, name=module: events.append(name) or {},
        )
    monkeypatch.setattr(
        "src.database.bootstrap_conversion_catalog.bootstrap_conversion_catalog_if_empty",
        lambda _db: dict(
            currencies_created=0,
            policies_created=0,
            rate_observations_created=0,
            rate_periods_created=0,
        ),
    )
    monkeypatch.setattr(
        "src.services.semantic_concept_projector.SemanticConceptProjector.project",
        lambda _self: SimpleNamespace(as_dict=lambda: {}),
    )
    monkeypatch.setattr(
        "src.calculation.postgres_strategy.PostgresStrategy", MagicMock()
    )
    converter = MagicMock()
    converter.get_storage_info.return_value = dict(
        backend="test", units_count=1, read_only=False
    )
    monkeypatch.setattr(
        "src.calculation.unit_converter.UnitConverter",
        lambda **_kwargs: events.append("converter") or converter,
    )
    return SimpleNamespace(
        app=FastAPI(lifespan=main.lifespan), events=events, sessions=sessions
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 6])
async def test_db_required_startup_reconciles_before_serving(
    startup, monkeypatch, count
):
    def reconcile(store):
        assert store._repo.db is startup.sessions[0]
        startup.events.append("reconcile")
        return count

    monkeypatch.setattr(
        DatabaseValueImportJobStore, "reconcile_legacy_active_jobs", reconcile
    )
    async with startup.app.router.lifespan_context(startup.app):
        startup.events.append("serve")
    assert startup.events[:6] == [
        "ping_db",
        "init_db",
        "session",
        "reconcile",
        "mapping_reconcile",
        "close",
    ]
    assert startup.events.index("reconcile") < startup.events.index("bootstrap_units")
    assert startup.events.index("converter") < startup.events.index("serve")
    assert len(startup.sessions) == 2
    for db in startup.sessions:
        db.close.assert_called_once_with()
    main.logger.info.assert_any_call(
        "Legacy value job reconciliation completed", reconciled_count=count
    )


@pytest.mark.asyncio
async def test_db_required_startup_reconciles_canonical_imports_before_serving(
    startup, monkeypatch
):
    monkeypatch.setattr(
        DatabaseValueImportJobStore,
        "reconcile_legacy_active_jobs",
        lambda _store: startup.events.append("value_reconcile") or 0,
    )
    monkeypatch.setattr(
        DatabaseCanonicalMappingPackageJobStore,
        "reconcile_legacy_active_jobs",
        lambda store: startup.events.append("mapping_reconcile") or 2,
    )
    async with startup.app.router.lifespan_context(startup.app):
        startup.events.append("serve")
    assert startup.events.index("init_db") < startup.events.index("mapping_reconcile")
    assert startup.events.index("value_reconcile") < startup.events.index(
        "mapping_reconcile"
    )
    assert startup.events.index("mapping_reconcile") < startup.events.index(
        "bootstrap_units"
    )
    assert startup.events.index("mapping_reconcile") < startup.events.index("serve")
    main.logger.info.assert_any_call(
        "Legacy canonical mapping job reconciliation completed", reconciled_count=2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("use_postgres_units", [False, True])
async def test_non_required_modes_do_not_reconcile(
    startup, monkeypatch, use_postgres_units
):
    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(settings, "use_postgres_units", use_postgres_units)
    reconcile = MagicMock(side_effect=AssertionError("must not reconcile"))
    monkeypatch.setattr(
        DatabaseValueImportJobStore, "reconcile_legacy_active_jobs", reconcile
    )
    async with startup.app.router.lifespan_context(startup.app):
        pass
    reconcile.assert_not_called()
    assert len(startup.sessions) == int(use_postgres_units)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["session", "reconcile", "close"])
async def test_reconciliation_failure_stops_startup_without_secrets(
    startup, monkeypatch, stage
):
    secret = "synthetic-payload-driver-error-secret"
    failing = MagicMock(side_effect=RuntimeError(secret))
    if stage == "session":
        monkeypatch.setattr("src.database.session.SessionLocal", failing)
    elif stage == "reconcile":
        monkeypatch.setattr(
            DatabaseValueImportJobStore, "reconcile_legacy_active_jobs", failing
        )
    else:

        def reconcile(store):
            store._repo.db.close.side_effect = RuntimeError(secret)
            return 6

        monkeypatch.setattr(
            DatabaseValueImportJobStore, "reconcile_legacy_active_jobs", reconcile
        )
    with pytest.raises(
        RuntimeError, match="reconciliation could not complete; startup refused"
    ) as raised:
        async with startup.app.router.lifespan_context(startup.app):
            pytest.fail("startup must fail before yielding to serve")
    assert "bootstrap_units" not in startup.events
    assert "converter" not in startup.events
    if stage != "session":
        startup.sessions[0].close.assert_called_once_with()
    rendered = "".join(
        traceback.format_exception(type(raised.value), raised.value, raised.tb)
    )
    assert secret not in rendered
    assert secret not in repr(main.logger.mock_calls)
    assert not any(
        call.args and call.args[0] == "Legacy value job reconciliation completed"
        for call in main.logger.info.call_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["ping_db", "init_db"])
async def test_database_init_failure_prevents_reconciliation(
    startup, monkeypatch, stage
):
    monkeypatch.setattr(
        f"src.database.init_db.{stage}",
        MagicMock(side_effect=RuntimeError("initialization failed")),
    )
    reconcile = MagicMock()
    monkeypatch.setattr(
        DatabaseValueImportJobStore, "reconcile_legacy_active_jobs", reconcile
    )
    with pytest.raises(RuntimeError, match="initialization failed"):
        async with startup.app.router.lifespan_context(startup.app):
            pytest.fail("must not serve")
    reconcile.assert_not_called()
    assert not startup.sessions
