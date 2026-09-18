"""Coverage tests for DB-required lifespan branches (offline-safe via mocks)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from src.config.settings import settings


@pytest.mark.asyncio
async def test_lifespan_runs_with_require_database_and_bootstrap(monkeypatch):
    events: list[object] = []
    bootstrap_paths: dict[str, dict[str, object]] = {}

    # Enable DB-required path without hitting a real Postgres instance.
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "seed_default_users", True)
    monkeypatch.setattr(settings, "seed_reference_data_on_startup", True)
    monkeypatch.setattr(settings, "bootstrap_admin_username", "admin")
    monkeypatch.setattr(settings, "bootstrap_admin_password", SecretStr("secret"))
    monkeypatch.setattr(settings, "reference_indicators_path", "/operator/indicators.json")
    monkeypatch.setattr(settings, "reference_mappings_path", "/operator/mappings.json")
    monkeypatch.setattr(settings, "units_json_path", "/operator/units.json")
    monkeypatch.setattr(settings, "currencies_seed_path", "/operator/currencies.json")
    monkeypatch.setattr(
        settings,
        "jwt_secret_key",
        SecretStr("test-secret-key-at-least-32-chars"),
    )
    monkeypatch.setattr(settings, "allowed_origins", ["http://localhost:8090"])

    # Avoid real DB initialization.
    monkeypatch.setattr(
        "src.database.init_db.ping_db", lambda: events.append("ping_db")
    )
    monkeypatch.setattr(
        "src.database.init_db.init_db", lambda: events.append("init_db")
    )

    # Mock sessions created in lifespan.
    db_session = MagicMock()
    db_session.close.side_effect = lambda: events.append("db_session.close")
    monkeypatch.setattr(
        "src.database.session.SessionLocal",
        MagicMock(side_effect=lambda: events.append("SessionLocal()") or db_session),
    )

    # Mock bootstrap + config loader.
    monkeypatch.setattr(
        "src.database.bootstrap.bootstrap_default_admin",
        lambda _db: events.append("bootstrap_default_admin") or True,
    )
    def reference_bootstrap(_db, **kwargs):
        bootstrap_paths["reference"] = kwargs
        events.append("bootstrap_reference_data_if_empty")
        return {
            "indicators_before": 0,
            "mappings_before": 0,
            "indicators_seeded": 0,
            "mappings_seeded": 0,
            "standards_created": 0,
            "indicators_total": 0,
            "mappings_total": 0,
        }

    monkeypatch.setattr(
        "src.database.bootstrap_reference_data.bootstrap_reference_data_if_empty",
        reference_bootstrap,
    )
    def unit_bootstrap(_db, **kwargs):
        bootstrap_paths["units"] = kwargs
        events.append("bootstrap_units_if_empty")
        return {
            "categories_before": 0,
            "units_before": 0,
            "rules_before": 0,
            "categories_seeded": 0,
            "categories_updated": 0,
            "units_seeded": 0,
            "units_updated": 0,
            "rules_seeded": 0,
            "rules_updated": 0,
            "categories_total": 0,
            "units_total": 1,
            "rules_total": 0,
        }

    monkeypatch.setattr(
        "src.database.bootstrap_units.bootstrap_units_if_empty",
        unit_bootstrap,
    )
    def currency_bootstrap(_db, **kwargs):
        bootstrap_paths["currencies"] = kwargs
        return {
            "currencies_created": 0,
            "policies_created": 0,
            "rate_observations_created": 0,
            "rate_periods_created": 0,
        }

    monkeypatch.setattr(
        "src.database.bootstrap_conversion_catalog.bootstrap_conversion_catalog_if_empty",
        currency_bootstrap,
    )
    monkeypatch.setattr(
        "src.database.bootstrap_semantic_model.bootstrap_semantic_model_if_empty",
        lambda _db: events.append("bootstrap_semantic_model_if_empty")
        or {
            "status": "seeded",
            "concepts_before": 0,
            "concepts_total": 1,
            "created": 1,
            "updated": 0,
            "refreshed_children": 1,
            "purged_stale": 0,
        },
    )

    class FakePostgresStrategy:
        def __init__(self):
            events.append("PostgresStrategy()")

        def close(self):
            events.append("PostgresStrategy.close")

    class FakeUnitConverter:
        def __init__(self, storage_strategy=None):
            events.append(("UnitConverter", type(storage_strategy).__name__))
            self._storage_strategy = storage_strategy or SimpleNamespace(
                close=lambda: events.append("storage.close")
            )

        def get_storage_info(self):
            return {
                "backend": "postgres",
                "units_count": 1,
                "read_only": False,
            }

    monkeypatch.setattr(
        "src.calculation.postgres_strategy.PostgresStrategy", FakePostgresStrategy
    )
    monkeypatch.setattr(
        "src.calculation.unit_converter.UnitConverter", FakeUnitConverter
    )

    from src.api.main import app

    async with app.router.lifespan_context(app):
        # No-op; the assertion is that the lifecycle path executes without raising.
        pass

    assert events[:5] == [
        "ping_db",
        "init_db",
        "SessionLocal()",
        "bootstrap_default_admin",
        "db_session.close",
    ]
    assert events.index("SessionLocal()", 4) < events.index(
        "bootstrap_reference_data_if_empty"
    )
    assert "bootstrap_units_if_empty" in events
    assert events.index("bootstrap_reference_data_if_empty") < events.index(
        "bootstrap_units_if_empty"
    )
    assert events.index("bootstrap_reference_data_if_empty") < events.index(
        "bootstrap_semantic_model_if_empty"
    )
    assert events.index("bootstrap_semantic_model_if_empty") < events.index(
        "bootstrap_units_if_empty"
    )
    assert events.index("bootstrap_units_if_empty") < events.index("PostgresStrategy()")
    assert events.index("PostgresStrategy()") < events.index(
        ("UnitConverter", "FakePostgresStrategy")
    )
    assert "PostgresStrategy.close" in events
    assert db_session.close.call_count >= 2
    assert str(bootstrap_paths["reference"]["indicators_path"]) == "/operator/indicators.json"
    assert str(bootstrap_paths["reference"]["mappings_path"]) == "/operator/mappings.json"
    assert str(bootstrap_paths["units"]["units_json_path"]) == "/operator/units.json"
    assert str(bootstrap_paths["currencies"]["currencies_seed_path"]) == "/operator/currencies.json"


@pytest.mark.asyncio
async def test_lifespan_reraises_default_user_bootstrap_failure(monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "seed_default_users", True)
    monkeypatch.setattr(
        settings,
        "jwt_secret_key",
        SecretStr("test-secret-key-at-least-32-chars"),
    )
    monkeypatch.setattr(settings, "allowed_origins", ["http://localhost:8090"])

    monkeypatch.setattr("src.database.init_db.ping_db", lambda: None)
    monkeypatch.setattr("src.database.init_db.init_db", lambda: None)
    monkeypatch.setattr("src.database.session.SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(
        "src.database.bootstrap.bootstrap_default_admin",
        lambda _db: (_ for _ in ()).throw(RuntimeError("admin seed failed")),
    )

    from src.api.main import app

    with pytest.raises(RuntimeError, match="admin seed failed"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_lifespan_reraises_database_unit_bootstrap_failure(monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "seed_default_users", False)
    monkeypatch.setattr(settings, "seed_reference_data_on_startup", False)
    monkeypatch.setattr(
        settings,
        "jwt_secret_key",
        SecretStr("test-secret-key-at-least-32-chars"),
    )
    monkeypatch.setattr(settings, "allowed_origins", ["http://localhost:8090"])

    monkeypatch.setattr("src.database.init_db.ping_db", lambda: None)
    monkeypatch.setattr("src.database.init_db.init_db", lambda: None)
    monkeypatch.setattr("src.database.session.SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(
        "src.database.bootstrap_semantic_model.bootstrap_semantic_model_if_empty",
        lambda _db: {"status": "skipped"},
    )
    monkeypatch.setattr(
        "src.database.bootstrap_units.bootstrap_units_if_empty",
        lambda _db: (_ for _ in ()).throw(RuntimeError("unit seed failed")),
    )

    from src.api.main import app

    with pytest.raises(RuntimeError, match="unit seed failed"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_lifespan_rejects_empty_postgres_unit_catalog(monkeypatch):
    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(settings, "use_postgres_units", True)

    monkeypatch.setattr("src.database.init_db.ping_db", lambda: None)
    monkeypatch.setattr("src.database.init_db.init_db", lambda: None)
    monkeypatch.setattr("src.database.session.SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(
        "src.database.bootstrap_units.bootstrap_units_if_empty",
        lambda _db: {
            "categories_before": 0,
            "units_before": 0,
            "rules_before": 0,
            "categories_seeded": 0,
            "categories_updated": 0,
            "units_seeded": 0,
            "units_updated": 0,
            "rules_seeded": 0,
            "rules_updated": 0,
            "categories_total": 0,
            "units_total": 0,
            "rules_total": 0,
        },
    )
    monkeypatch.setattr(
        "src.database.bootstrap_conversion_catalog.bootstrap_conversion_catalog_if_empty",
        lambda _db: {
            "currencies_created": 0,
            "policies_created": 0,
            "rate_observations_created": 0,
            "rate_periods_created": 0,
        },
    )

    class FakePostgresStrategy:
        pass

    class EmptyUnitConverter:
        def __init__(self, storage_strategy=None):
            self._storage_strategy = storage_strategy

        def get_storage_info(self):
            return {"backend": "postgres", "units_count": 0, "read_only": False}

    monkeypatch.setattr(
        "src.calculation.postgres_strategy.PostgresStrategy", FakePostgresStrategy
    )
    monkeypatch.setattr(
        "src.calculation.unit_converter.UnitConverter", EmptyUnitConverter
    )

    from src.api.main import app

    with pytest.raises(RuntimeError, match="zero units"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_lifespan_shutdown_logs_close_failure(monkeypatch):
    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(settings, "use_postgres_units", False)

    class BadStorage:
        def close(self):
            raise RuntimeError("close failed")

    class FakeUnitConverter:
        def __init__(self, storage_strategy=None):
            self._storage_strategy = BadStorage()

        def get_storage_info(self):
            return {"backend": "json", "units_count": 1, "read_only": True}

    monkeypatch.setattr(
        "src.calculation.unit_converter.UnitConverter", FakeUnitConverter
    )

    from src.api.main import app

    async with app.router.lifespan_context(app):
        assert hasattr(app.state, "unit_converter")

    assert not hasattr(app.state, "unit_converter")
