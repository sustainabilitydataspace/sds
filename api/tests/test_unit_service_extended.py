"""UnitService behavior + branch coverage tests (db mocked)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services.unit_service import UnitConversionError, UnitService


def _unit(
    symbol: str,
    category_id: int,
    category_name: str,
    base_unit: str,
    factor: float,
    offset: float = 0.0,
    *,
    special: bool = False,
):
    category = SimpleNamespace(
        id=category_id,
        name=category_name,
        base_unit=base_unit,
        special_conversions=special,
        units=[],
    )
    return SimpleNamespace(
        id=1,
        symbol=symbol,
        name=symbol,
        category_id=category_id,
        category=category,
        aliases=[],
        conversion_factor=factor,
        conversion_offset=offset,
        unit_metadata={},
    )


def test_unit_service_get_all_units_and_categories():
    db = MagicMock()
    service = UnitService(db)
    service.repository = MagicMock()

    kg = _unit("kg", 1, "mass", "kg", 1.0)
    g = _unit("g", 1, "mass", "kg", 0.001)
    service.repository.get_units.return_value = [kg, g]

    units = service.get_all_units()
    assert units[0]["symbol"] == "kg"
    assert units[1]["symbol"] == "g"
    assert units[0]["category"] == "mass"

    cat = kg.category
    cat.description = "Mass units"
    service.repository.get_unit_categories.return_value = [cat]
    categories = service.get_all_categories()
    assert categories == [
        {
            "name": "mass",
            "description": "Mass units",
            "base_unit": "kg",
            "special_conversions": False,
        }
    ]

    # conversion rules via db query
    rule = SimpleNamespace(
        from_unit="kg",
        to_unit="g",
        formula="value * 1000",
        reverse_formula=None,
        description="mass scaling",
        conditions={"category": "mass"},
        rule_metadata={"source": "fixture"},
        rule_hash="hash-1",
        priority=10,
        valid_from=date(2020, 1, 1),
        valid_to=date(2023, 12, 31),
        source_system="fixture-system",
        source_version="v1",
        is_active=True,
    )
    db.query.return_value.filter.return_value.all.return_value = [rule]
    rules = service.get_all_conversion_rules()
    assert rules == [
        {
            "from_unit": "kg",
            "to_unit": "g",
            "formula": "value * 1000",
            "reverse_formula": None,
            "description": "mass scaling",
            "conditions": {"category": "mass"},
            "metadata": {
                "source": "fixture",
                "rule_id": "hash-1",
                "priority": 10,
                "valid_from": date(2020, 1, 1),
                "valid_to": date(2023, 12, 31),
                "source_system": "fixture-system",
                "source_version": "v1",
            },
        }
    ]

    # supported units + categories listing
    service.repository.get_units.return_value = [kg]
    assert service.get_supported_units(category_name="mass")[0]["symbol"] == "kg"

    cats = service.get_unit_categories()
    assert cats[0]["units_count"] == len(cat.units)


def test_unit_service_convert_identity_and_unknown_unit_errors():
    db = MagicMock()
    service = UnitService(db)
    service.repository = MagicMock()

    result = service.convert(1.23, "kg", "kg")
    assert result.formula_used == "identity"
    assert result.converted_value == Decimal("1.23")

    service.repository.get_unit_by_symbol.return_value = None
    with pytest.raises(UnitConversionError):
        service.convert(1.0, "unknown", "kg")

    # unknown to_unit
    kg = _unit("kg", 1, "mass", "kg", 1.0)
    service.repository.get_unit_by_symbol.side_effect = lambda sym: (
        kg if sym == "kg" else None
    )
    with pytest.raises(UnitConversionError):
        service.convert(1.0, "kg", "unknown")


def test_unit_service_convert_within_category_and_temperature():
    db = MagicMock()
    service = UnitService(db, precision=2)
    service.repository = MagicMock()

    kg = _unit("kg", 1, "mass", "kg", 1.0)
    g = _unit("g", 1, "mass", "kg", 0.001)
    service.repository.get_unit_by_symbol.side_effect = lambda sym: {"kg": kg, "g": g}[
        sym
    ]

    res = service.convert(1.0, "kg", "g")
    assert res.converted_unit == "g"
    assert res.converted_value == Decimal("1000.00")

    c = _unit("°C", 2, "temperature", "K", 1.0, 273.15, special=True)
    k = _unit("K", 2, "temperature", "K", 1.0, 0.0, special=True)
    service.repository.get_unit_by_symbol.side_effect = lambda sym: {"°C": c, "K": k}[
        sym
    ]

    res = service.convert(0.0, "°C", "K")
    assert res.converted_unit == "K"
    assert res.converted_value == Decimal("273.15")


def test_unit_service_custom_conversion_rules_and_compatibility():
    db = MagicMock()
    service = UnitService(db, precision=3)
    service.repository = MagicMock()

    a = _unit("A", 1, "mass", "A", 1.0)
    b = _unit("B", 2, "energy", "B", 1.0)
    service.repository.get_unit_by_symbol.side_effect = lambda sym: {"A": a, "B": b}[
        sym
    ]

    # direct custom rule
    rule = SimpleNamespace(formula="value * 2", reverse_formula=None)
    service.repository.get_conversion_rule.side_effect = lambda f, t: (
        rule if (f, t) == ("A", "B") else None
    )
    res = service.convert(2.0, "A", "B")
    assert res.converted_value == Decimal("4.000")

    # reverse rule
    reverse_rule = SimpleNamespace(formula="value * 2", reverse_formula="value / 2")
    service.repository.get_conversion_rule.side_effect = lambda f, t: (
        reverse_rule if (f, t) == ("B", "A") else None
    )
    res = service.convert(2.0, "A", "B")
    assert res.converted_value == Decimal("1.000")

    # no rule
    service.repository.get_conversion_rule.side_effect = lambda f, t: None
    with pytest.raises(UnitConversionError):
        service.convert(1.0, "A", "B")

    assert service.are_units_compatible("A", "A") is True
    assert service.get_conversion_path("A", "A") == ["A"]


def test_unit_service_apply_conversion_formula_error_and_zero_value():
    db = MagicMock()
    service = UnitService(db)
    with pytest.raises(UnitConversionError):
        service._apply_conversion_formula(Decimal("1"), "value **", "A", "B")

    res = service._apply_conversion_formula(Decimal("0"), "value * 2", "A", "B")
    assert res.conversion_factor == Decimal("0")


def test_unit_service_compatibility_and_paths():
    db = MagicMock()
    service = UnitService(db)
    service.repository = MagicMock()

    kg = _unit("kg", 1, "mass", "kg", 1.0)
    g = _unit("g", 1, "mass", "kg", 0.001)
    m = _unit("m", 2, "length", "m", 1.0)

    service.repository.get_unit_by_symbol.side_effect = lambda sym: {
        "kg": kg,
        "g": g,
        "m": m,
    }.get(sym)

    assert service.are_units_compatible("kg", "missing") is False
    assert service.are_units_compatible("kg", "g") is True

    # custom rule
    rule = SimpleNamespace(formula="value * 2", reverse_formula=None)
    service.repository.get_conversion_rule.side_effect = lambda f, t: (
        rule if (f, t) == ("kg", "m") else None
    )
    assert service.are_units_compatible("kg", "m") is True

    # reverse rule with reverse_formula
    reverse = SimpleNamespace(formula="value * 2", reverse_formula="value / 2")
    service.repository.get_conversion_rule.side_effect = lambda f, t: (
        reverse if (f, t) == ("m", "kg") else None
    )
    assert service.are_units_compatible("kg", "m") is True

    # no rule
    service.repository.get_conversion_rule.side_effect = lambda f, t: None
    assert service.are_units_compatible("kg", "m") is False

    # exception path
    service.repository.get_unit_by_symbol.side_effect = RuntimeError("boom")
    assert service.are_units_compatible("kg", "g") is False

    # conversion path
    service.are_units_compatible = MagicMock(return_value=False)
    assert service.get_conversion_path("kg", "g") == []
    service.are_units_compatible = MagicMock(return_value=True)
    assert service.get_conversion_path("kg", "g") == ["kg", "g"]
