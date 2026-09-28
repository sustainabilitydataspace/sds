"""Regression tests for the expanded bundled unit catalog."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.calculation.unit_converter import (
    UnitCategory,
    UnitConversionError,
    UnitConverter,
)
from src.calculation.unit_database import UnitDatabase as RealUnitDatabase

UNITS_JSON_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "data" / "units_database.json"
)


class _RealJsonStorage:
    """Storage adapter that bypasses the global pytest UnitDatabase mock."""

    def __init__(self, database_path: Path):
        self._database = RealUnitDatabase(database_path=str(database_path))

    def load_units(self):
        return [
            {
                "symbol": unit.symbol,
                "name": unit.name,
                "category": unit.category.value,
                "base_unit": unit.base_unit,
                "conversion_factor": float(unit.conversion_factor),
                "conversion_offset": float(unit.conversion_offset),
                "aliases": unit.aliases,
                "metadata": unit.metadata,
            }
            for unit in self._database.get_unit_definitions()
        ]

    def load_conversion_rules(self):
        return [
            {
                "from_unit": rule.from_unit,
                "to_unit": rule.to_unit,
                "formula": rule.formula,
                "reverse_formula": rule.reverse_formula,
                "conditions": rule.conditions,
                "metadata": rule.metadata,
            }
            for rule in self._database.get_conversion_rules()
        ]

    def get_storage_info(self):
        class _Info:
            backend = "json"
            available = True
            read_only = True
            location = str(UNITS_JSON_PATH)
            metadata = {}

        return _Info()


def _build_converter() -> UnitConverter:
    return UnitConverter(storage_strategy=_RealJsonStorage(UNITS_JSON_PATH))


def _catalog_label_index():
    payload = json.loads(UNITS_JSON_PATH.read_text(encoding="utf-8"))
    index = {}
    for category_name, category in payload.get("categories", {}).items():
        for symbol, unit in category.get("units", {}).items():
            labels = [symbol, unit.get("symbol", ""), unit.get("name", "")]
            labels.extend(unit.get("aliases", []) or [])
            for label in labels:
                normalized = " ".join(str(label).strip().split()).lower()
                if normalized:
                    index[normalized] = (category_name, symbol, unit)
    return index


def test_units_database_json_exceeds_previous_baseline():
    payload = json.loads(UNITS_JSON_PATH.read_text(encoding="utf-8"))
    categories = payload.get("categories", {})
    units_count = sum(
        len(category.get("units", {})) for category in categories.values()
    )

    assert len(categories) >= 16
    assert units_count >= 120


def test_converter_loads_expanded_catalog_and_aliases():
    converter = _build_converter()

    assert len(converter.units) >= 120
    assert converter.normalize_unit_symbol("ton_us") == "short ton"
    assert converter.normalize_unit_symbol("horsepower") == "hp"
    assert converter.normalize_unit_symbol("gpm") == "gal/min"


def test_atomizer_package_unit_labels_are_catalog_resolvable():
    labels = _catalog_label_index()
    package_unit_labels = [
        "(organization-specific denominator)",
        "Boolean",
        "Count",
        "Date",
        "Energy/(organization-specific denominator)",
        "MWh per currency",
        "MWh/(organization-specific denominator)",
        "Text",
        "actions",
        "boolean",
        "business partners",
        "cases",
        "cases per million hours worked",
        "complaints",
        "count",
        "count_and_percent",
        "count_and_rate",
        "currency_per_tCO2eq",
        "employees",
        "employees (head count)",
        "employees (head count or FTE)",
        "employees and workers",
        "energy per organization-specific denominator",
        "fatalities",
        "fines",
        "governance body members",
        "head count",
        "hours per employee",
        "incidents",
        "injuries",
        "instances",
        "legal actions",
        "m3 per million EUR net revenue",
        "mass unit",
        "MWh/kg",
        "MWh/L",
        "MWh/Nm3",
        "MWh/reported quantity unit",
        "MWh/t",
        "MWh/tonne",
        "non-employees (head count or FTE)",
        "number and percent",
        "number and rate",
        "Nm3",
        "operations",
        "organization-specific metric",
        "rate",
        "recalls",
        "reported area unit",
        "reported distance unit",
        "reported product quantity unit",
        "reported target unit",
        "species",
        "suppliers",
        "tCO2e/(organization-specific denominator)",
        "tCO2eq_per_activity_or_output_unit",
        "varies by denominator",
        "weight or volume",
        "workers",
    ]

    missing = [
        label
        for label in package_unit_labels
        if " ".join(label.strip().split()).lower() not in labels
    ]

    assert missing == []


def test_reporting_placeholder_units_have_non_convertible_policy():
    labels = _catalog_label_index()
    placeholder_labels = [
        "(organization-specific denominator)",
        "mass unit",
        "organization-specific metric",
        "reported target unit",
        "varies by denominator",
        "weight or volume",
    ]

    for label in placeholder_labels:
        _, _, unit = labels[" ".join(label.strip().split()).lower()]
        metadata = unit["metadata"]
        assert metadata["non_convertible"] is True
        assert metadata["conversion_policy"] == "reported_semantic_unit"


def test_non_convertible_reporting_units_are_not_cross_convertible():
    converter = _build_converter()

    assert (
        converter.normalize_unit_symbol("reported target unit")
        == "reported_semantic_unit"
    )
    assert converter.are_units_compatible("reported target unit", "Text") is False
    with pytest.raises(UnitConversionError):
        converter.convert(1, "reported target unit", "Text")


def test_added_categories_are_exposed():
    converter = _build_converter()

    pressure_units = converter.get_supported_units(UnitCategory.PRESSURE)
    power_units = converter.get_supported_units(UnitCategory.POWER)
    flow_units = converter.get_supported_units(UnitCategory.FLOW)
    count_units = converter.get_supported_units(UnitCategory.COUNT)
    semantic_units = converter.get_supported_units(UnitCategory.SEMANTIC)

    assert any(unit["symbol"] == "psi" for unit in pressure_units)
    assert any(unit["symbol"] == "hp" for unit in power_units)
    assert any(unit["symbol"] == "gal/min" for unit in flow_units)
    assert any(unit["symbol"] == "count" for unit in count_units)
    assert any(unit["symbol"] == "reported_semantic_unit" for unit in semantic_units)


def test_mile_to_kilometer_conversion():
    converter = _build_converter()

    result = converter.convert(1, "mi", "km")

    assert result.converted_unit == "km"
    assert result.converted_value == Decimal("1.609340")


def test_short_ton_to_kilogram_conversion():
    converter = _build_converter()

    result = converter.convert(1, "short ton", "kg")

    assert result.converted_unit == "kg"
    assert result.converted_value == Decimal("907.184740")


def test_psi_to_kilopascal_conversion():
    converter = _build_converter()

    result = converter.convert(1, "psi", "kPa")

    assert result.converted_unit == "kPa"
    assert result.converted_value == Decimal("6.894757")


def test_hp_to_kilowatt_conversion():
    converter = _build_converter()

    result = converter.convert(1, "hp", "kW")

    assert result.converted_unit == "kW"
    assert result.converted_value == Decimal("0.745700")


def test_gallon_per_minute_to_liter_per_minute_conversion():
    converter = _build_converter()

    result = converter.convert(1, "gal/min", "L/min")

    assert result.converted_unit == "L/min"
    assert result.converted_value == Decimal("3.785412")


def test_renewable_e1_5_factor_units_are_catalog_resolvable():
    converter = _build_converter()

    assert converter.normalize_unit_symbol("Nm³") == "Nm3"
    assert converter.normalize_unit_symbol("MWh/tonne") == "MWh/t"
    assert converter.normalize_unit_symbol("MWh/litre") == "MWh/L"
    assert converter.normalize_unit_symbol("MWh per Nm3") == "MWh/Nm3"
    assert (
        converter.normalize_unit_symbol("MWh/reported quantity unit")
        == "MWh/reported_semantic_unit"
    )

    mass_result = converter.convert(1, "MWh/t", "MWh/kg")
    volume_result = converter.convert(1, "MWh/Nm3", "MWh/L")

    assert mass_result.converted_value == Decimal("0.001000")
    assert volume_result.converted_value == Decimal("0.001000")


def test_ghg_emission_factor_units_are_catalog_resolvable_and_exact_only():
    converter = _build_converter()

    assert converter.normalize_unit_symbol("kg CO2e/kg") == "kg CO2e/kg"
    assert converter.normalize_unit_symbol("kg CO2e per kg") == "kg CO2e/kg"
    assert converter.normalize_unit_symbol("kg CO2e/kWh") == "kg CO2e/kWh"
    assert converter.normalize_unit_symbol("kg CO2e per kWh") == "kg CO2e/kWh"
    assert (
        converter.normalize_unit_symbol("kg CO2e per reported product quantity unit")
        == "kg CO2e/reported product quantity unit"
    )
    assert (
        converter.normalize_unit_symbol("kg CO2e/asset type/year")
        == "kg CO2e/asset type/year"
    )

    assert converter.are_units_compatible("kg CO2e/kg", "kg CO2e/kg") is True
    assert converter.are_units_compatible("kg CO2e/kWh", "g CO2e/MJ") is True
    assert converter.are_units_compatible("kg CO2e/kg", "kg CO2e/kWh") is False

    result = converter.convert(1, "kg CO2e/kWh", "g CO2e/MJ")
    assert result.converted_value == Decimal("277.777778")
