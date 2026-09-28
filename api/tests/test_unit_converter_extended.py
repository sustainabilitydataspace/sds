"""Extra UnitConverter tests to cover storage/DB management branches."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.calculation.unit_converter import (
    ConversionRule,
    UnitCategory,
    UnitConversionError,
    UnitConverter,
    UnitDefinition,
)
from src.config.settings import settings


class _FakeStorage:
    def __init__(self, *, units=None, rules=None, raise_on_load: bool = False):
        self._units = units or []
        self._rules = rules or []
        self._raise_on_load = raise_on_load

    def load_units(self):
        if self._raise_on_load:
            raise RuntimeError("load_units failed")
        return self._units

    def load_conversion_rules(self):
        return self._rules

    def get_storage_info(self):
        return SimpleNamespace(
            backend="fake",
            available=True,
            read_only=True,
            location="memory",
            metadata={},
        )


def test_apply_conversion_rule_invalid_formula_raises():
    converter = UnitConverter(storage_strategy=_FakeStorage())
    rule = ConversionRule(from_unit="a", to_unit="b", formula="value + unknown_name")
    with pytest.raises(UnitConversionError, match="Formula evaluation failed"):
        converter._apply_conversion_rule(1.0, "a", "b", rule)


def test_convert_temperature_additional_branches():
    converter = UnitConverter(storage_strategy=_FakeStorage())
    converter._initialize_standard_units()

    f_to_k = converter.convert(32, "°F", "K")
    assert f_to_k.converted_unit == "K"

    k_to_f = converter.convert(273.15, "K", "°F")
    assert k_to_f.converted_unit == "°F"


def test_reload_from_storage_replaces_cached_units_and_rules():
    converter = UnitConverter(
        storage_strategy=_FakeStorage(
            units=[
                {
                    "symbol": "unit_a",
                    "name": "Unit A",
                    "category": "count",
                    "base_unit": "unit_a",
                    "conversion_factor": "1",
                    "aliases": ["ua"],
                }
            ],
            rules=[
                {
                    "from_unit": "unit_a",
                    "to_unit": "unit_b",
                    "formula": "value * 2",
                    "metadata": {"kind": "double"},
                }
            ],
        )
    )
    assert converter.normalize_unit_symbol("ua") == "unit_a"
    assert ("unit_a", "unit_b") in converter.conversion_rules

    converter._storage_strategy = _FakeStorage(
        units=[
            {
                "symbol": "unit_c",
                "name": "Unit C",
                "category": "count",
                "base_unit": "unit_c",
                "conversion_factor": "1",
            }
        ],
        rules=[],
    )
    converter.reload_from_storage()

    assert "unit_a" not in converter.units
    assert "unit_c" in converter.units
    assert converter.conversion_rules == {}


def test_direct_rule_compatibility_conversion_path_and_zero_factor():
    converter = UnitConverter(storage_strategy=_FakeStorage())
    converter.register_unit(
        UnitDefinition(
            symbol="unit_a",
            name="Unit A",
            category=UnitCategory.COUNT,
            base_unit="unit_a",
            conversion_factor=Decimal("1"),
            aliases=["ua"],
        )
    )
    converter.register_unit(
        UnitDefinition(
            symbol="unit_b",
            name="Unit B",
            category=UnitCategory.COUNT,
            base_unit="unit_a",
            conversion_factor=Decimal("1"),
            aliases=[],
        )
    )
    converter.register_conversion_rule(
        ConversionRule(
            from_unit="unit_a",
            to_unit="unit_b",
            formula="value * 2",
            metadata={"kind": "double"},
        )
    )

    assert converter.are_units_compatible("unit_a", "unit_a") is True
    assert converter.are_units_compatible("unit_a", "unit_b") is True
    assert converter.are_units_compatible("unknown", "unit_b") is False
    assert converter.get_conversion_path("unit_a", "unit_b") == ["unit_a", "unit_b"]
    assert converter.get_conversion_path("unknown", "unit_b") == []

    result = converter.convert(0, "ua", "unit_b")
    assert result.converted_value == Decimal("0.000000")
    assert result.conversion_factor == Decimal("0")
    assert result.metadata == {"kind": "double"}


def test_load_from_storage_failure_raises_unit_conversion_error():
    with pytest.raises(
        UnitConversionError, match="Failed to initialize unit converter"
    ):
        UnitConverter(storage_strategy=_FakeStorage(raise_on_load=True))


def test_auto_detect_strategy_forced_postgres_unavailable_raises(monkeypatch):
    monkeypatch.setattr(settings, "use_postgres_units", True)

    import src.calculation.postgres_strategy as pg

    monkeypatch.setattr(pg.PostgresStrategy, "is_available", lambda _self: False)

    with pytest.raises(
        UnitConversionError, match="PostgreSQL storage forced but not available"
    ):
        UnitConverter()


def test_auto_detect_strategy_json_unavailable_raises(monkeypatch):
    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(settings, "use_postgres_units", False)

    import src.calculation.json_strategy as js

    monkeypatch.setattr(js.JSONStrategy, "is_available", lambda _self: False)

    with pytest.raises(UnitConversionError, match="No storage backend available"):
        UnitConverter()


def test_auto_detect_strategy_postgres_falls_back_to_json(monkeypatch):
    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(settings, "use_postgres_units", None)

    import src.calculation.postgres_strategy as pg

    monkeypatch.setattr(pg.PostgresStrategy, "is_available", lambda _self: False)

    converter = UnitConverter()
    info = converter.get_storage_info()
    assert info["backend"] == "json"


def test_auto_detect_strategy_uses_available_postgres(monkeypatch):
    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(settings, "use_postgres_units", None)

    import src.calculation.postgres_strategy as pg

    class _AvailablePostgres:
        def __init__(self, db_session=None):
            self.db_session = db_session

        def is_available(self):
            return True

        def get_storage_info(self):
            return SimpleNamespace(
                backend="postgres",
                available=True,
                read_only=False,
                location="postgres://unit-test",
                metadata={},
            )

        def load_units(self):
            return []

        def load_conversion_rules(self):
            return []

    monkeypatch.setattr(pg, "PostgresStrategy", _AvailablePostgres)

    converter = UnitConverter(storage_strategy=_FakeStorage())
    strategy = converter._auto_detect_strategy(db_session=object())

    assert isinstance(strategy, _AvailablePostgres)
    assert strategy.get_storage_info().location == "postgres://unit-test"


def test_auto_detect_strategy_require_database_forces_postgres(monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "use_postgres_units", False)

    import src.calculation.postgres_strategy as pg

    monkeypatch.setattr(pg.PostgresStrategy, "is_available", lambda _self: False)

    with pytest.raises(UnitConversionError, match="DB-first mode"):
        UnitConverter()


def test_database_management_methods_smoke(monkeypatch):
    converter = UnitConverter(storage_strategy=_FakeStorage())
    converter._initialize_standard_units()

    # Mock legacy "database" API used by some methods
    converter.database = MagicMock()
    converter.database.add_unit.return_value = True
    converter.database.add_conversion_rule.return_value = True
    converter.database.remove_unit.return_value = True
    converter.database.export_database.return_value = True
    converter.database.import_database.return_value = True
    converter.database.load_database.return_value = None
    converter.database.get_database_info.return_value = {"backend": "mock"}

    # Add unit (success) + failure to register (invalid category)
    assert (
        converter.add_unit_to_database(
            symbol="u1",
            name="Unit1",
            category="mass",
            base_unit="kg",
            conversion_factor=1.0,
            aliases=["u1a"],
            metadata={"x": 1},
        )
        is True
    )
    assert (
        converter.add_unit_to_database(
            symbol="u2",
            name="Unit2",
            category="invalid-category",
            base_unit="kg",
            conversion_factor=1.0,
            aliases=[],
            metadata={},
        )
        is False
    )

    # Add conversion rule (success) + failure when register raises
    assert (
        converter.add_conversion_rule_to_database(
            from_unit="kg",
            to_unit="g",
            formula="value * 1000",
            reverse_formula="value / 1000",
            metadata={"x": 1},
        )
        is True
    )

    original_register = converter.register_conversion_rule
    monkeypatch.setattr(
        converter,
        "register_conversion_rule",
        lambda _rule: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert (
        converter.add_conversion_rule_to_database(
            from_unit="A",
            to_unit="B",
            formula="value * 2",
        )
        is False
    )
    monkeypatch.setattr(converter, "register_conversion_rule", original_register)

    # Remove unit clears cache + aliases
    converter.unit_aliases["u1a"] = "u1"
    converter.units["u1"] = converter.units["kg"]
    assert converter.remove_unit_from_database("u1", "mass") is True

    # Import/reload database needs _load_from_database; stub it.
    converter._load_from_database = MagicMock()
    assert converter.import_database("in.json", merge=True) is True
    assert converter.reload_database() is True

    converter.database.load_database.side_effect = RuntimeError("no")
    assert converter.reload_database() is False

    assert converter.get_database_info() == {"backend": "mock"}
    assert converter.export_database("out.json") is True

    # unit_db_manager-dependent methods: set attribute explicitly (it does not exist in __init__)
    converter.unit_db_manager = None
    assert converter.refresh_units_cache() is None  # logs warning path

    converter.unit_db_manager = MagicMock()
    converter.unit_db_manager.get_all_units.side_effect = RuntimeError("fail")
    converter.unit_db_manager.get_conversion_rules.return_value = []
    converter._load_units_from_database()

    converter.unit_db_manager.get_all_units.side_effect = None
    converter.unit_db_manager.get_all_units.return_value = [converter.units["kg"]]
    converter.unit_db_manager.get_conversion_rules.return_value = []
    converter._load_units_from_database()

    converter.unit_db_manager = None
    assert converter.create_unit_in_database(converter.units["kg"]) is False
    assert converter.update_unit_in_database("kg", {"name": "x"}) is False
    assert converter.delete_unit_from_database("kg") is False
    assert converter.create_conversion_rule_in_database(MagicMock()) is False
    assert converter.export_units_configuration() is None
    assert converter.import_units_configuration({"x": 1}) is False


def test_unit_db_manager_success_branches_update_live_cache():
    converter = UnitConverter(storage_strategy=_FakeStorage())
    converter._initialize_standard_units()
    unit = UnitDefinition(
        symbol="widget",
        name="Widget",
        category=UnitCategory.COUNT,
        base_unit="widget",
        conversion_factor=Decimal("1"),
        aliases=["widgets"],
    )
    rule = ConversionRule(
        from_unit="widget",
        to_unit="dozen_widget",
        formula="value / 12",
    )

    manager = MagicMock()
    manager.create_unit.return_value = True
    manager.update_unit.return_value = True
    manager.delete_unit.return_value = True
    manager.create_conversion_rule.return_value = True
    manager.export_units_config.return_value = {"units": ["widget"]}
    manager.import_units_config.return_value = True
    manager.get_all_units.return_value = [unit]
    manager.get_conversion_rules.return_value = [rule]
    converter.unit_db_manager = manager

    assert converter.create_unit_in_database(unit) is True
    assert converter.normalize_unit_symbol("widgets") == "widget"

    assert converter.update_unit_in_database("widget", {"name": "Updated"}) is True
    assert converter.units["widget"].name == "Widget"
    manager.update_unit.assert_called_once_with("widget", {"name": "Updated"})

    assert converter.create_conversion_rule_in_database(rule) is True
    assert converter.conversion_rules[("widget", "dozen_widget")] == rule

    assert converter.export_units_configuration() == {"units": ["widget"]}
    assert converter.import_units_configuration({"units": []}) is True
    manager.import_units_config.assert_called_once_with({"units": []})

    assert converter.delete_unit_from_database("widget", soft_delete=False) is True
    assert "widget" not in converter.units
    assert "widgets" not in converter.unit_aliases
    manager.delete_unit.assert_called_once_with("widget", False)


def test_historical_conversion_rules_are_selected_by_effective_date():
    converter = UnitConverter(storage_strategy=_FakeStorage())
    for symbol in ("unit_a", "unit_b"):
        converter.register_unit(
            UnitDefinition(
                symbol=symbol,
                name=symbol,
                category=UnitCategory.COUNT,
                base_unit=symbol,
                conversion_factor=Decimal("1"),
            )
        )

    converter.register_conversion_rule(
        ConversionRule(
            "unit_a",
            "unit_b",
            "value * 2",
            metadata={
                "rule_id": "historical",
                "priority": 10,
                "valid_from": date(2020, 1, 1),
                "valid_to": date(2023, 12, 31),
            },
        )
    )
    converter.register_conversion_rule(
        ConversionRule(
            "unit_a",
            "unit_b",
            "value * 3",
            metadata={
                "rule_id": "current",
                "priority": 10,
                "valid_from": date(2024, 1, 1),
            },
        )
    )

    historical = converter.convert(5, "unit_a", "unit_b", as_of=date(2022, 6, 1))
    current = converter.convert(5, "unit_a", "unit_b", as_of=date(2025, 6, 1))

    assert historical.converted_value == Decimal("10.000000")
    assert current.converted_value == Decimal("15.000000")


def test_expired_rule_is_not_reported_as_currently_compatible():
    converter = UnitConverter(storage_strategy=_FakeStorage())
    converter.register_unit(
        UnitDefinition("unit_a", "unit_a", UnitCategory.COUNT, "unit_a", Decimal("1"))
    )
    converter.register_unit(
        UnitDefinition("unit_b", "unit_b", UnitCategory.ENERGY, "unit_b", Decimal("1"))
    )
    converter.register_conversion_rule(
        ConversionRule(
            "unit_a",
            "unit_b",
            "value * 2",
            metadata={
                "rule_id": "expired",
                "valid_from": date(2020, 1, 1),
                "valid_to": date(2020, 12, 31),
            },
        )
    )

    assert converter.are_units_compatible("unit_a", "unit_b") is False
    assert converter.get_conversion_path("unit_a", "unit_b") == []


def test_case_insensitive_alias_temperature_fallback_and_base_path():
    converter = UnitConverter(storage_strategy=_FakeStorage())
    converter.register_unit(
        UnitDefinition(
            symbol="unit_base",
            name="Unit Base",
            category=UnitCategory.COUNT,
            base_unit="unit_base",
            conversion_factor=Decimal("1"),
        )
    )
    converter.register_unit(
        UnitDefinition(
            symbol="unit_child",
            name="Unit Child",
            category=UnitCategory.COUNT,
            base_unit="unit_base",
            conversion_factor=Decimal("2"),
            aliases=["ChildAlias"],
        )
    )
    converter.unit_aliases.pop("ChildAlias")
    assert converter.normalize_unit_symbol("childalias") == "unit_child"
    assert converter.get_conversion_path("unit_child", "unit_base") == [
        "unit_child",
        "unit_base",
    ]

    converter.register_unit(
        UnitDefinition(
            symbol="degX",
            name="Degree X",
            category=UnitCategory.TEMPERATURE,
            base_unit="K",
            conversion_factor=Decimal("2"),
            conversion_offset=Decimal("10"),
        )
    )
    converter.register_unit(
        UnitDefinition(
            symbol="degY",
            name="Degree Y",
            category=UnitCategory.TEMPERATURE,
            base_unit="K",
            conversion_factor=Decimal("5"),
            conversion_offset=Decimal("1"),
        )
    )

    result = converter.convert(3, "degX", "degY")

    assert result.converted_value == Decimal("4.200000")
    assert result.formula_used == "(value + 10) * 2 / 5 - 1"
