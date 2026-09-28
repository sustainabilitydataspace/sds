"""Coverage tests for unit storage strategies (PostgreSQL + JSON fallback)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.calculation.json_strategy import JSONStrategy
from src.calculation.postgres_strategy import PostgresStrategy
from src.calculation.storage_strategy import StorageInfo, StorageStrategy


class _ConcreteStorageStrategy(StorageStrategy):
    def is_available(self) -> bool:
        return StorageStrategy.is_available(self)

    def load_units(self):
        return StorageStrategy.load_units(self)

    def load_categories(self):
        return StorageStrategy.load_categories(self)

    def load_conversion_rules(self):
        return StorageStrategy.load_conversion_rules(self)

    def get_storage_info(self):
        return StorageInfo(
            backend="test",
            available=True,
            read_only=True,
            location="memory",
            metadata={},
        )


def test_storage_strategy_abstract_passes_and_repr():
    strategy = _ConcreteStorageStrategy()

    assert strategy.is_available() is None
    assert strategy.load_units() is None
    assert strategy.load_categories() is None
    assert strategy.load_conversion_rules() is None
    assert repr(strategy) == "<_ConcreteStorageStrategy backend=test available=True>"


def test_postgres_strategy_is_available_cached_success():
    session = MagicMock()
    session.execute.return_value = None

    strategy = PostgresStrategy(db_session=session)
    assert strategy.is_available() is True

    session.execute.reset_mock()
    assert strategy.is_available() is True  # cached
    session.execute.assert_not_called()


def test_postgres_strategy_is_available_failure_cached():
    session = MagicMock()
    session.execute.side_effect = Exception("db down")

    strategy = PostgresStrategy(db_session=session)
    assert strategy.is_available() is False

    session.execute.reset_mock()
    assert strategy.is_available() is False  # cached
    session.execute.assert_not_called()


def test_postgres_strategy_loaders_delegate_to_unit_service():
    session = MagicMock()
    session.execute.return_value = None

    strategy = PostgresStrategy(db_session=session)
    strategy.unit_service = MagicMock()
    strategy.unit_service.get_all_units.return_value = [{"symbol": "kg"}]
    strategy.unit_service.get_all_categories.return_value = [{"name": "mass"}]
    strategy.unit_service.get_all_conversion_rules.return_value = [
        {"from_unit": "kg", "to_unit": "g"}
    ]

    assert strategy.load_units() == [{"symbol": "kg"}]
    assert strategy.load_categories() == [{"name": "mass"}]
    assert strategy.load_conversion_rules() == [{"from_unit": "kg", "to_unit": "g"}]

    info = strategy.get_storage_info()
    assert info.backend == "postgresql"
    assert info.available is True


def test_postgres_strategy_loader_errors_and_close_warning():
    session = MagicMock()
    strategy = PostgresStrategy(db_session=session)
    strategy.unit_service = MagicMock()
    strategy.unit_service.get_all_units.side_effect = RuntimeError("units")
    strategy.unit_service.get_all_categories.side_effect = RuntimeError("categories")
    strategy.unit_service.get_all_conversion_rules.side_effect = RuntimeError("rules")

    with pytest.raises(RuntimeError, match="units"):
        strategy.load_units()
    with pytest.raises(RuntimeError, match="categories"):
        strategy.load_categories()
    with pytest.raises(RuntimeError, match="rules"):
        strategy.load_conversion_rules()

    session.close.side_effect = RuntimeError("close")
    strategy.close()
    assert strategy.db_session is None
    assert strategy.unit_service is None


def test_postgres_strategy_close_handles_session():
    session = MagicMock()
    strategy = PostgresStrategy(db_session=session)
    strategy.close()
    session.close.assert_called_once()


def test_postgres_strategy_creates_session_lazily(monkeypatch):
    session = MagicMock()
    unit_service = MagicMock()
    unit_service.get_all_units.return_value = [{"symbol": "kg"}]

    monkeypatch.setattr("src.database.session.SessionLocal", lambda: session)
    monkeypatch.setattr(
        "src.services.unit_service.UnitService",
        lambda db: unit_service if db is session else None,
    )

    strategy = PostgresStrategy()

    assert strategy.load_units() == [{"symbol": "kg"}]
    assert strategy.db_session is session

    strategy.close()
    session.close.assert_called_once()
    assert strategy.db_session is None
    assert strategy.unit_service is None


def test_json_strategy_load_units_and_categories_and_rules_with_mocked_db():
    strategy = JSONStrategy(json_path="does-not-matter.json")

    # The test suite patches UnitDatabase globally; JSONStrategy will use it.
    assert strategy.is_available() is True

    units = strategy.load_units()
    assert isinstance(units, list)
    assert units and "symbol" in units[0]

    categories = strategy.load_categories()
    assert isinstance(categories, list)

    rules = strategy.load_conversion_rules()
    assert isinstance(rules, list)

    info = strategy.get_storage_info()
    assert info.backend == "json"


def test_json_strategy_unavailable_and_default_storage_info(monkeypatch):
    import src.calculation.unit_database as unit_database

    class _FailingUnitDatabase:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("json unavailable")

    monkeypatch.setattr(unit_database, "UnitDatabase", _FailingUnitDatabase)
    strategy = JSONStrategy(json_path="missing.json")

    assert strategy.is_available() is False
    assert strategy.is_available() is False

    default_strategy = JSONStrategy()
    default_strategy._available = False
    info = default_strategy.get_storage_info()
    assert info.backend == "json"
    assert info.metadata["database_initialized"] is False
