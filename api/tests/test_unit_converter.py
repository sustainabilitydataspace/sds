"""Tests for unit conversion system."""

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from src.calculation.unit_converter import (
    ConversionResult,
    ConversionRule,
    UnitCategory,
    UnitConversionError,
    UnitConverter,
    UnitDefinition,
)


def test_unit_converter_import_does_not_initialize_settings():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pydantic_settings; "
            "pydantic_settings.BaseSettings.__init__ = lambda *args, **kwargs: "
            "(_ for _ in ()).throw(RuntimeError('SETTINGS_EAGER')); "
            "from src.calculation import unit_converter; "
            "assert 'src.config.settings' not in __import__('sys').modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


class TestUnitConverter:
    """Test cases for UnitConverter."""

    def setup_method(self):
        """Set up test fixtures."""
        self.converter = UnitConverter(precision=4)

    def test_init(self):
        """Test UnitConverter initialization."""
        assert self.converter.precision == 4
        assert len(self.converter.units) > 0
        assert len(self.converter.unit_aliases) > 0

    def test_normalize_unit_symbol_direct(self):
        """Test unit symbol normalization with direct match."""
        assert self.converter.normalize_unit_symbol("kg") == "kg"
        assert self.converter.normalize_unit_symbol("m²") == "m²"
        assert self.converter.normalize_unit_symbol("kWh") == "kWh"

    def test_normalize_unit_symbol_alias(self):
        """Test unit symbol normalization with aliases."""
        assert self.converter.normalize_unit_symbol("ton") == "t"
        assert self.converter.normalize_unit_symbol("liter") == "L"
        assert self.converter.normalize_unit_symbol("celsius") == "°C"

    def test_normalize_unit_symbol_case_insensitive(self):
        """Test case-insensitive unit normalization."""
        assert self.converter.normalize_unit_symbol("KG") == "kg"
        assert self.converter.normalize_unit_symbol("Kilogram") == "kg"

    def test_normalize_unit_symbol_unknown(self):
        """Test normalization with unknown unit."""
        with pytest.raises(UnitConversionError, match="Unknown unit"):
            self.converter.normalize_unit_symbol("unknown_unit")

    def test_get_unit_category(self):
        """Test getting unit category."""
        assert self.converter.get_unit_category("kg") == UnitCategory.MASS
        assert self.converter.get_unit_category("kWh") == UnitCategory.ENERGY
        assert self.converter.get_unit_category("L") == UnitCategory.VOLUME
        assert self.converter.get_unit_category("°C") == UnitCategory.TEMPERATURE

    def test_are_units_compatible_same_category(self):
        """Test unit compatibility within same category."""
        assert self.converter.are_units_compatible("kg", "g")
        assert self.converter.are_units_compatible("kWh", "MJ")
        assert self.converter.are_units_compatible("L", "m³")

    def test_are_units_compatible_different_category(self):
        """Test unit compatibility across different categories."""
        assert not self.converter.are_units_compatible("kg", "L")
        assert not self.converter.are_units_compatible("kWh", "m²")

    def test_are_units_compatible_unknown_unit(self):
        """Test compatibility with unknown units."""
        assert not self.converter.are_units_compatible("kg", "unknown")
        assert not self.converter.are_units_compatible("unknown1", "unknown2")

    def test_convert_same_unit(self):
        """Test conversion with same unit."""
        result = self.converter.convert(100, "kg", "kg")

        assert result.original_value == 100
        assert result.original_unit == "kg"
        assert result.converted_value == Decimal("100")
        assert result.converted_unit == "kg"
        assert result.conversion_factor == Decimal("1")
        assert result.formula_used == "identity"

    def test_conversion_start_logging_is_sampled(self):
        """Batch conversion logging should not emit one event per row."""

        class FakeLogger:
            def __init__(self):
                self.debug_calls = []

            def debug(self, *args, **kwargs):
                self.debug_calls.append((args, kwargs))

            def error(self, *args, **kwargs):
                raise AssertionError("conversion should not fail")

        fake_logger = FakeLogger()
        self.converter.logger = fake_logger

        for _index in range(12):
            self.converter.convert(1, "kg", "g")

        assert len(fake_logger.debug_calls) == 10

    def test_convert_mass_units(self):
        """Test mass unit conversions."""
        # kg to g
        result = self.converter.convert(1, "kg", "g")
        assert result.converted_value == Decimal("1000.0000")

        # g to kg
        result = self.converter.convert(1000, "g", "kg")
        assert result.converted_value == Decimal("1.0000")

        # kg to t
        result = self.converter.convert(1000, "kg", "t")
        assert result.converted_value == Decimal("1.0000")

        # lb to kg
        result = self.converter.convert(1, "lb", "kg")
        assert abs(result.converted_value - Decimal("0.4536")) < Decimal("0.001")

    def test_convert_energy_units(self):
        """Test energy unit conversions."""
        # kWh to MJ
        result = self.converter.convert(1, "kWh", "MJ")
        assert result.converted_value == Decimal("3.6000")

        # MJ to kWh
        result = self.converter.convert(3.6, "MJ", "kWh")
        assert result.converted_value == Decimal("1.0000")

        # GJ to kWh
        result = self.converter.convert(1, "GJ", "kWh")
        assert abs(result.converted_value - Decimal("277.7778")) < Decimal("0.001")

        # kWh to MWh
        result = self.converter.convert(1000, "kWh", "MWh")
        assert result.converted_value == Decimal("1.0000")

        # GJ to MWh
        result = self.converter.convert(1, "GJ", "MWh")
        assert abs(result.converted_value - Decimal("0.2778")) < Decimal("0.001")

    def test_convert_volume_units(self):
        """Test volume unit conversions."""
        # L to m³
        result = self.converter.convert(1000, "L", "m³")
        assert result.converted_value == Decimal("1.0000")

        # m³ to L
        result = self.converter.convert(1, "m³", "L")
        assert result.converted_value == Decimal("1000.0000")

        # mL to L
        result = self.converter.convert(1000, "mL", "L")
        assert result.converted_value == Decimal("1.0000")

    def test_convert_temperature_units(self):
        """Test temperature unit conversions."""
        # Celsius to Kelvin
        result = self.converter.convert(0, "°C", "K")
        assert result.converted_value == Decimal("273.1500")

        # Kelvin to Celsius
        result = self.converter.convert(273.15, "K", "°C")
        assert abs(result.converted_value - Decimal("0")) < Decimal("0.01")

        # Celsius to Fahrenheit
        result = self.converter.convert(0, "°C", "°F")
        assert abs(result.converted_value - Decimal("32")) < Decimal("0.1")

        # Fahrenheit to Celsius
        result = self.converter.convert(32, "°F", "°C")
        assert abs(result.converted_value - Decimal("0")) < Decimal("0.1")

    def test_temperature_conversion_factor_is_none(self):
        """Temperature conversions are affine, not linear — no single factor exists."""
        result = self.converter.convert(100, "°C", "K")
        assert result.conversion_factor is None

        result = self.converter.convert(0, "°C", "°F")
        assert result.conversion_factor is None

        result = self.converter.convert(32, "°F", "°C")
        assert result.conversion_factor is None

    def test_btu_family_uses_single_it_basis(self):
        """BTU, therm, MMBtu, and BTU/h must share one Btu_IT basis."""
        catalog_path = (
            Path(__file__).resolve().parents[1] / "src" / "data" / "units_database.json"
        )
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        btu = Decimal(
            catalog["categories"]["energy"]["units"]["BTU"]["conversion_factor"]
        )
        therm = Decimal(
            catalog["categories"]["energy"]["units"]["therm"]["conversion_factor"]
        )
        mmbtu = Decimal(
            catalog["categories"]["energy"]["units"]["MMBtu"]["conversion_factor"]
        )
        btu_per_hour = Decimal(
            catalog["categories"]["power"]["units"]["BTU/h"]["conversion_factor"]
        )

        assert btu == Decimal("1055.05585262")
        assert therm == btu * Decimal("100000")
        assert mmbtu == btu * Decimal("1000000")
        assert btu_per_hour == btu / Decimal("3600")

    def test_physical_converter_is_cached(self):
        """_build_physical_converter should return the same instance on repeated calls."""
        first = self.converter._build_physical_converter()
        second = self.converter._build_physical_converter()
        assert first is second

    def test_get_physical_converter_returns_cached_physical_converter(self):
        """Public accessor should expose the cached physical converter instance."""
        private = self.converter._build_physical_converter()
        first = self.converter.get_physical_converter()
        second = self.converter.get_physical_converter()

        assert first is private
        assert second is first

        result = first.convert(Decimal("1"), "kg", "g")
        assert result.value == Decimal("1000.0000")

    def test_physical_converter_cache_invalidated_on_register(self):
        """Cache must be cleared when a new unit is registered."""
        first = self.converter._build_physical_converter()
        custom_unit = UnitDefinition(
            symbol="custom_kg",
            name="custom_kilogram",
            category=UnitCategory.MASS,
            base_unit="kg",
            conversion_factor=Decimal("1"),
        )
        self.converter.register_unit(custom_unit)
        second = self.converter._build_physical_converter()
        assert first is not second

    def test_custom_rule_preserves_decimal_precision(self):
        """Custom conversion rules should not lose precision via float() intermediate."""
        self.converter.precision = 10
        self.converter.register_unit(
            UnitDefinition(
                symbol="test_unit1",
                name="test unit 1",
                category=UnitCategory.RATIO,
                base_unit="test_unit1",
                conversion_factor=Decimal("1"),
            )
        )
        self.converter.register_unit(
            UnitDefinition(
                symbol="test_unit2",
                name="test unit 2",
                category=UnitCategory.RATIO,
                base_unit="test_unit1",
                conversion_factor=Decimal("1"),
            )
        )
        rule = ConversionRule(
            from_unit="test_unit1",
            to_unit="test_unit2",
            formula="value * 1000000.0000001",
        )
        self.converter.register_conversion_rule(rule)
        result = self.converter.convert(
            Decimal("1.0000001"), "test_unit1", "test_unit2"
        )
        # The result should reflect full Decimal precision, not float truncation
        assert result.converted_value == Decimal("1000000.1000001000")

    def test_physical_converter_uses_loaded_conversion_rules(self):
        """Physical converter must preserve custom conversion-rule provenance."""
        self.converter.register_conversion_rule(
            ConversionRule(
                from_unit="kWh/kg",
                to_unit="MJ/kg",
                formula="value * 3.6",
                reverse_formula="value / 3.6",
            )
        )
        physical = self.converter._build_physical_converter()
        result = physical.convert(Decimal("2"), "kWh/kg", "MJ/kg")

        assert result.value == Decimal("7.2000")
        assert result.trace[0]["rule_id"] == "kWh/kg->MJ/kg"
        assert result.trace[0]["formula"] == "value * 3.6"

    def test_convert_emissions_units(self):
        """Test emissions unit conversions."""
        # kg CO2e to t CO2e
        result = self.converter.convert(1000, "kg CO2e", "t CO2e")
        assert result.converted_value == Decimal("1.0000")

        # t CO2e to kg CO2e
        result = self.converter.convert(1, "t CO2e", "kg CO2e")
        assert result.converted_value == Decimal("1000.0000")

        # Using aliases
        result = self.converter.convert(1000, "kgCO2e", "tCO2e")
        assert result.converted_value == Decimal("1.0000")

    def test_convert_incompatible_units(self):
        """Test conversion between incompatible units."""
        with pytest.raises(UnitConversionError, match="Incompatible unit categories"):
            self.converter.convert(100, "kg", "L")

        with pytest.raises(UnitConversionError, match="Incompatible unit categories"):
            self.converter.convert(100, "kWh", "m²")

    def test_convert_zero_value(self):
        """Test conversion with zero value."""
        result = self.converter.convert(0, "kg", "g")
        assert result.converted_value == Decimal("0.0000")
        assert result.conversion_factor == Decimal("1000")

    def test_convert_negative_value(self):
        """Test conversion with negative value."""
        result = self.converter.convert(-100, "kg", "g")
        assert result.converted_value == Decimal("-100000.0000")

    def test_convert_decimal_input(self):
        """Test conversion with Decimal input."""
        result = self.converter.convert(Decimal("1.5"), "kg", "g")
        assert result.converted_value == Decimal("1500.0000")

    def test_register_custom_unit(self):
        """Test registering custom unit."""
        custom_unit = UnitDefinition(
            symbol="custom_kg",
            name="custom_kilogram",
            category=UnitCategory.MASS,
            base_unit="kg",
            conversion_factor=Decimal("1"),
            aliases=["ckg"],
        )

        self.converter.register_unit(custom_unit)

        assert "custom_kg" in self.converter.units
        assert self.converter.unit_aliases["ckg"] == "custom_kg"

        # Test conversion with custom unit
        result = self.converter.convert(1, "custom_kg", "g")
        assert result.converted_value == Decimal("1000.0000")

    def test_register_conversion_rule(self):
        """Test registering custom conversion rule."""
        rule = ConversionRule(
            from_unit="test_unit1",
            to_unit="test_unit2",
            formula="value * 2.5",
            reverse_formula="value / 2.5",
        )

        self.converter.register_conversion_rule(rule)

        assert ("test_unit1", "test_unit2") in self.converter.conversion_rules
        assert ("test_unit2", "test_unit1") in self.converter.conversion_rules

    def test_get_supported_units_all(self):
        """Test getting all supported units."""
        units = self.converter.get_supported_units()

        assert len(units) > 0
        assert all("symbol" in unit for unit in units)
        assert all("category" in unit for unit in units)
        assert all("name" in unit for unit in units)

    def test_get_supported_units_by_category(self):
        """Test getting supported units by category."""
        mass_units = self.converter.get_supported_units(UnitCategory.MASS)
        energy_units = self.converter.get_supported_units(UnitCategory.ENERGY)

        assert len(mass_units) > 0
        assert len(energy_units) > 0
        assert all(unit["category"] == "mass" for unit in mass_units)
        assert all(unit["category"] == "energy" for unit in energy_units)

    def test_get_conversion_path_direct(self):
        """Test getting conversion path for direct conversion."""
        path = self.converter.get_conversion_path("kg", "g")
        assert len(path) >= 2
        assert "kg" in path
        assert "g" in path

    def test_get_conversion_path_via_base(self):
        """Test getting conversion path via base unit."""
        path = self.converter.get_conversion_path("g", "t")
        assert len(path) >= 2
        # Should go through kg (base unit)

    def test_get_conversion_path_incompatible(self):
        """Test getting conversion path for incompatible units."""
        path = self.converter.get_conversion_path("kg", "L")
        assert len(path) == 0

    def test_validate_unit_compatibility_compatible(self):
        """Test unit compatibility validation with compatible units."""
        result = self.converter.validate_unit_compatibility(["kg", "g", "t"])

        assert result["valid"] is True
        assert len(result["errors"]) == 0
        assert "mass" in result["categories"]
        assert len(result["conversions"]) > 0
        assert all(result["conversions"].values())  # All should be True

    def test_validate_unit_compatibility_mixed_categories(self):
        """Test unit compatibility validation with mixed categories."""
        result = self.converter.validate_unit_compatibility(["kg", "L", "kWh"])

        assert len(result["warnings"]) > 0
        assert "Multiple unit categories" in result["warnings"][0]
        assert len(result["categories"]) == 3

    def test_validate_unit_compatibility_unknown_units(self):
        """Test unit compatibility validation with unknown units."""
        result = self.converter.validate_unit_compatibility(["kg", "unknown_unit"])

        assert result["valid"] is False
        assert len(result["errors"]) > 0
        assert "Unknown unit: unknown_unit" in result["errors"][0]

    def test_precision_handling(self):
        """Test precision handling in conversions."""
        converter = UnitConverter(precision=2)

        result = converter.convert(1, "kg", "g")
        assert result.converted_value == Decimal("1000.00")

        result = converter.convert(1, "lb", "kg")
        # Should be rounded to 2 decimal places
        assert len(str(result.converted_value).split(".")[-1]) <= 2


class TestUnitDefinition:
    """Test cases for UnitDefinition."""

    def test_unit_definition_creation(self):
        """Test UnitDefinition creation."""
        unit = UnitDefinition(
            symbol="test_kg",
            name="test_kilogram",
            category=UnitCategory.MASS,
            base_unit="kg",
            conversion_factor=Decimal("1"),
            aliases=["tkg"],
        )

        assert unit.symbol == "test_kg"
        assert unit.name == "test_kilogram"
        assert unit.category == UnitCategory.MASS
        assert unit.base_unit == "kg"
        assert unit.conversion_factor == Decimal("1")
        assert unit.aliases == ["tkg"]

    def test_unit_definition_with_offset(self):
        """Test UnitDefinition with conversion offset."""
        unit = UnitDefinition(
            symbol="test_temp",
            name="test_temperature",
            category=UnitCategory.TEMPERATURE,
            base_unit="K",
            conversion_factor=Decimal("1"),
            conversion_offset=Decimal("100"),
        )

        assert unit.conversion_offset == Decimal("100")


class TestConversionRule:
    """Test cases for ConversionRule."""

    def test_conversion_rule_creation(self):
        """Test ConversionRule creation."""
        rule = ConversionRule(
            from_unit="unit1",
            to_unit="unit2",
            formula="value * 2",
            reverse_formula="value / 2",
        )

        assert rule.from_unit == "unit1"
        assert rule.to_unit == "unit2"
        assert rule.formula == "value * 2"
        assert rule.reverse_formula == "value / 2"

    def test_conversion_rule_with_conditions(self):
        """Test ConversionRule with conditions."""
        rule = ConversionRule(
            from_unit="unit1",
            to_unit="unit2",
            formula="value * 2",
            conditions={"temperature": "> 0"},
            metadata={"source": "custom"},
        )

        assert rule.conditions == {"temperature": "> 0"}
        assert rule.metadata == {"source": "custom"}


class TestConversionResult:
    """Test cases for ConversionResult."""

    def test_conversion_result_creation(self):
        """Test ConversionResult creation."""
        result = ConversionResult(
            original_value=100,
            original_unit="kg",
            converted_value=Decimal("100000"),
            converted_unit="g",
            conversion_factor=Decimal("1000"),
            formula_used="value * 1000",
        )

        assert result.original_value == 100
        assert result.original_unit == "kg"
        assert result.converted_value == Decimal("100000")
        assert result.converted_unit == "g"
        assert result.conversion_factor == Decimal("1000")
        assert result.formula_used == "value * 1000"
