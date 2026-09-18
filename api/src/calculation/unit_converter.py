"""Unit conversion system for sustainability metrics."""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

import structlog

# Conversion engine integration (additive, preserves existing API)
from src.calculation.conversion.dimensions import DimensionVector
from src.calculation.conversion.physical import (
    PhysicalConversionRule,
    PhysicalUnit,
    PhysicalUnitConverter,
    _safe_decimal_eval,
    validate_conversion_formula,
)
from src.config.settings import settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from src.calculation.storage_strategy import StorageStrategy

logger = structlog.get_logger(__name__)


class UnitCategory(Enum):
    """Categories of units for conversion."""

    MASS = "mass"
    ENERGY = "energy"
    VOLUME = "volume"
    AREA = "area"
    LENGTH = "length"
    TIME = "time"
    TEMPERATURE = "temperature"
    EMISSIONS = "emissions"
    CURRENCY = "currency"
    PERCENTAGE = "percentage"
    RATIO = "ratio"
    COUNT = "count"
    SEMANTIC = "semantic"
    POWER = "power"
    PRESSURE = "pressure"
    SPEED = "speed"
    FORCE = "force"
    DENSITY = "density"
    FLOW = "flow"


@dataclass
class UnitDefinition:
    """Definition of a unit with conversion information."""

    symbol: str
    name: str
    category: UnitCategory
    base_unit: str  # Base unit for this category
    conversion_factor: Decimal  # Factor to convert to base unit
    conversion_offset: Decimal = Decimal("0")  # Offset for temperature conversions
    aliases: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversionRule:
    """Rule for converting between specific units."""

    from_unit: str
    to_unit: str
    formula: str  # Formula for conversion (e.g., "value * 1000")
    reverse_formula: Optional[str] = None  # Reverse formula if different
    conditions: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversionResult:
    """Result of a unit conversion."""

    original_value: Union[int, float, Decimal]
    original_unit: str
    converted_value: Decimal
    converted_unit: str
    conversion_factor: Decimal | None
    formula_used: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class UnitConversionError(Exception):
    """Exception raised for unit conversion errors."""

    pass


class UnitConverter:
    """System for converting between different units."""

    def __init__(
        self,
        precision: int = 6,
        storage_strategy: Optional["StorageStrategy"] = None,
        db_session: Optional["Session"] = None,
        database_path: Optional[str] = None,
    ):
        """Initialize unit converter with storage strategy.

        Args:
            precision: Decimal precision for conversions
            storage_strategy: Storage strategy to use (auto-detect if None)
            db_session: Database session for PostgreSQL strategy
            database_path: Path to JSON file (for JSON strategy or fallback)
        """
        self.precision = precision
        self.logger = logger.bind(component="UnitConverter")
        self._conversion_start_log_count = 0
        self._conversion_start_log_emitted = 0
        self._conversion_start_sample_limit = 10

        # Unit definitions registry
        self.units: Dict[str, UnitDefinition] = {}
        self.unit_aliases: Dict[str, str] = {}  # alias -> canonical symbol

        # Conversion rules registry
        self.conversion_rules: Dict[Tuple[str, str], ConversionRule] = {}

        # Cached physical converter (invalidated on registry mutation)
        self._physical_converter: PhysicalUnitConverter | None = None

        # Initialize storage strategy
        self._storage_strategy = storage_strategy or self._auto_detect_strategy(
            db_session=db_session, json_path=database_path
        )

        # Load units and rules from storage
        self._load_from_storage()

    def _auto_detect_strategy(
        self, db_session: Optional["Session"] = None, json_path: Optional[str] = None
    ) -> "StorageStrategy":
        """
        Auto-detect best available storage strategy.

        Tries PostgreSQL first, falls back to JSON if unavailable.

        Args:
            db_session: Database session for PostgreSQL
            json_path: Path to JSON file for fallback

        Returns:
            Available storage strategy

        Raises:
            UnitConversionError: If no storage backend is available
        """
        from src.calculation.json_strategy import JSONStrategy
        from src.calculation.postgres_strategy import PostgresStrategy

        resolved_json_path = (
            json_path or settings.units_json_path or settings.units_database_path
        )

        force_postgres = (
            settings.require_database or settings.use_postgres_units is True
        )

        # PostgreSQL (optional): only attempt when not explicitly disabled.
        if force_postgres or settings.use_postgres_units is not False:
            postgres_strategy = PostgresStrategy(db_session)
            if postgres_strategy.is_available():
                storage_info = postgres_strategy.get_storage_info()
                self.logger.info(
                    "Using PostgreSQL storage for units",
                    location=storage_info.location,
                    read_only=storage_info.read_only,
                )
                return postgres_strategy

            if force_postgres:
                if settings.require_database:
                    error_msg = "PostgreSQL storage is required in DB-first mode but not available"
                else:
                    error_msg = "PostgreSQL storage forced but not available"
                self.logger.error(error_msg)
                raise UnitConversionError(error_msg)

        # Fallback to JSON
        self.logger.warning(
            "PostgreSQL not available, falling back to JSON storage",
            reason="PostgreSQL connection failed or not configured",
        )

        json_strategy = JSONStrategy(resolved_json_path)
        if json_strategy.is_available():
            storage_info = json_strategy.get_storage_info()
            self.logger.info(
                "Using JSON storage for units (fallback mode)",
                location=storage_info.location,
                read_only=storage_info.read_only,
            )
            return json_strategy

        # No storage available
        error_msg = "No storage backend available for units (PostgreSQL and JSON both unavailable)"
        self.logger.error(error_msg)
        raise UnitConversionError(error_msg)

    def _load_from_storage(self):
        """Load units and conversion rules from storage strategy."""
        try:
            # Load unit definitions
            units_data = self._storage_strategy.load_units()
            for unit_data in units_data:
                # Convert dict to UnitDefinition
                unit_def = self._dict_to_unit_definition(unit_data)
                self.register_unit(unit_def)

            # Load conversion rules
            rules_data = self._storage_strategy.load_conversion_rules()
            for rule_data in rules_data:
                # Convert dict to ConversionRule
                rule = self._dict_to_conversion_rule(rule_data)
                self.register_conversion_rule(rule)

            # Log storage info
            storage_info = self._storage_strategy.get_storage_info()
            self.logger.info(
                "Units loaded from storage",
                backend=storage_info.backend,
                units_count=len(units_data),
                rules_count=len(rules_data),
                read_only=storage_info.read_only,
            )

        except Exception as e:
            self.logger.error("Failed to load from storage", error=str(e))
            raise UnitConversionError(f"Failed to initialize unit converter: {e}")

    def _dict_to_unit_definition(self, unit_data: Dict[str, Any]) -> UnitDefinition:
        """
        Convert dictionary to UnitDefinition object.

        Args:
            unit_data: Dictionary with unit properties

        Returns:
            UnitDefinition object
        """
        return UnitDefinition(
            symbol=unit_data["symbol"],
            name=unit_data["name"],
            category=UnitCategory(unit_data["category"]),
            base_unit=unit_data["base_unit"],
            conversion_factor=Decimal(str(unit_data["conversion_factor"])),
            conversion_offset=Decimal(str(unit_data.get("conversion_offset", 0))),
            aliases=unit_data.get("aliases", []),
            metadata=unit_data.get("metadata", {}),
        )

    def _dict_to_conversion_rule(self, rule_data: Dict[str, Any]) -> ConversionRule:
        """
        Convert dictionary to ConversionRule object.

        Args:
            rule_data: Dictionary with rule properties

        Returns:
            ConversionRule object
        """
        return ConversionRule(
            from_unit=rule_data["from_unit"],
            to_unit=rule_data["to_unit"],
            formula=rule_data["formula"],
            reverse_formula=rule_data.get("reverse_formula"),
            conditions=rule_data.get("conditions", {}),
            metadata=rule_data.get("metadata", {}),
        )

    def reload_from_storage(self):
        """
        Reload units from storage.

        This is useful after units are modified in PostgreSQL.
        """
        self.logger.info("Reloading units from storage")

        # Clear current data
        self.units.clear()
        self.unit_aliases.clear()
        self.conversion_rules.clear()
        self._physical_converter = None

        # Reload from storage
        self._load_from_storage()

    def get_storage_info(self) -> Dict[str, Any]:
        """
        Get information about current storage backend.

        Returns:
            Dictionary with storage information
        """
        storage_info = self._storage_strategy.get_storage_info()
        return {
            "backend": storage_info.backend,
            "available": storage_info.available,
            "read_only": storage_info.read_only,
            "location": storage_info.location,
            "units_count": len(self.units),
            "rules_count": len(self.conversion_rules),
            "metadata": storage_info.metadata,
        }

    def _initialize_standard_units(self):
        """Initialize standard unit definitions."""

        # Mass units (base: kg)
        mass_units = [
            UnitDefinition("kg", "kilogram", UnitCategory.MASS, "kg", Decimal("1")),
            UnitDefinition("g", "gram", UnitCategory.MASS, "kg", Decimal("0.001")),
            UnitDefinition(
                "mg", "milligram", UnitCategory.MASS, "kg", Decimal("0.000001")
            ),
            UnitDefinition(
                "t",
                "tonne",
                UnitCategory.MASS,
                "kg",
                Decimal("1000"),
                aliases=["ton", "metric_ton"],
            ),
            UnitDefinition("lb", "pound", UnitCategory.MASS, "kg", Decimal("0.453592")),
            UnitDefinition(
                "oz", "ounce", UnitCategory.MASS, "kg", Decimal("0.0283495")
            ),
        ]

        # Energy units (base: J)
        energy_units = [
            UnitDefinition("J", "joule", UnitCategory.ENERGY, "J", Decimal("1")),
            UnitDefinition(
                "kJ", "kilojoule", UnitCategory.ENERGY, "J", Decimal("1000")
            ),
            UnitDefinition(
                "MJ", "megajoule", UnitCategory.ENERGY, "J", Decimal("1000000")
            ),
            UnitDefinition(
                "GJ", "gigajoule", UnitCategory.ENERGY, "J", Decimal("1000000000")
            ),
            UnitDefinition(
                "kWh", "kilowatt-hour", UnitCategory.ENERGY, "J", Decimal("3600000")
            ),
            UnitDefinition(
                "MWh", "megawatt-hour", UnitCategory.ENERGY, "J", Decimal("3600000000")
            ),
            UnitDefinition(
                "GWh",
                "gigawatt-hour",
                UnitCategory.ENERGY,
                "J",
                Decimal("3600000000000"),
            ),
            UnitDefinition(
                "BTU",
                "british_thermal_unit",
                UnitCategory.ENERGY,
                "J",
                Decimal("1055.05585262"),
            ),
            UnitDefinition(
                "cal", "calorie", UnitCategory.ENERGY, "J", Decimal("4.184")
            ),
            UnitDefinition(
                "kcal", "kilocalorie", UnitCategory.ENERGY, "J", Decimal("4184")
            ),
        ]

        # Volume units (base: m³)
        volume_units = [
            UnitDefinition(
                "m³",
                "cubic_meter",
                UnitCategory.VOLUME,
                "m³",
                Decimal("1"),
                aliases=["m3", "cubic_meter"],
            ),
            UnitDefinition(
                "L",
                "liter",
                UnitCategory.VOLUME,
                "m³",
                Decimal("0.001"),
                aliases=["l", "liter", "litre"],
            ),
            UnitDefinition(
                "mL",
                "milliliter",
                UnitCategory.VOLUME,
                "m³",
                Decimal("0.000001"),
                aliases=["ml"],
            ),
            UnitDefinition(
                "gal", "gallon", UnitCategory.VOLUME, "m³", Decimal("0.00378541")
            ),
            UnitDefinition(
                "ft³",
                "cubic_foot",
                UnitCategory.VOLUME,
                "m³",
                Decimal("0.0283168"),
                aliases=["ft3"],
            ),
        ]

        # Area units (base: m²)
        area_units = [
            UnitDefinition(
                "m²",
                "square_meter",
                UnitCategory.AREA,
                "m²",
                Decimal("1"),
                aliases=["m2", "sqm"],
            ),
            UnitDefinition(
                "km²",
                "square_kilometer",
                UnitCategory.AREA,
                "m²",
                Decimal("1000000"),
                aliases=["km2"],
            ),
            UnitDefinition("ha", "hectare", UnitCategory.AREA, "m²", Decimal("10000")),
            UnitDefinition(
                "ft²",
                "square_foot",
                UnitCategory.AREA,
                "m²",
                Decimal("0.092903"),
                aliases=["ft2", "sqft"],
            ),
            UnitDefinition("acre", "acre", UnitCategory.AREA, "m²", Decimal("4046.86")),
        ]

        # Length units (base: m)
        length_units = [
            UnitDefinition("m", "meter", UnitCategory.LENGTH, "m", Decimal("1")),
            UnitDefinition(
                "km", "kilometer", UnitCategory.LENGTH, "m", Decimal("1000")
            ),
            UnitDefinition(
                "cm", "centimeter", UnitCategory.LENGTH, "m", Decimal("0.01")
            ),
            UnitDefinition(
                "mm", "millimeter", UnitCategory.LENGTH, "m", Decimal("0.001")
            ),
            UnitDefinition("ft", "foot", UnitCategory.LENGTH, "m", Decimal("0.3048")),
            UnitDefinition("in", "inch", UnitCategory.LENGTH, "m", Decimal("0.0254")),
            UnitDefinition("yd", "yard", UnitCategory.LENGTH, "m", Decimal("0.9144")),
            UnitDefinition("mi", "mile", UnitCategory.LENGTH, "m", Decimal("1609.34")),
        ]

        # Time units (base: s)
        time_units = [
            UnitDefinition("s", "second", UnitCategory.TIME, "s", Decimal("1")),
            UnitDefinition("min", "minute", UnitCategory.TIME, "s", Decimal("60")),
            UnitDefinition("h", "hour", UnitCategory.TIME, "s", Decimal("3600")),
            UnitDefinition("d", "day", UnitCategory.TIME, "s", Decimal("86400")),
            UnitDefinition("week", "week", UnitCategory.TIME, "s", Decimal("604800")),
            UnitDefinition(
                "month", "month", UnitCategory.TIME, "s", Decimal("2629746")
            ),  # Average month
            UnitDefinition(
                "year", "year", UnitCategory.TIME, "s", Decimal("31556952")
            ),  # Average year
        ]

        # Temperature units (base: K)
        temperature_units = [
            UnitDefinition("K", "kelvin", UnitCategory.TEMPERATURE, "K", Decimal("1")),
            UnitDefinition(
                "°C",
                "celsius",
                UnitCategory.TEMPERATURE,
                "K",
                Decimal("1"),
                Decimal("273.15"),
                aliases=["C", "celsius"],
            ),
            UnitDefinition(
                "°F",
                "fahrenheit",
                UnitCategory.TEMPERATURE,
                "K",
                Decimal("0.555556"),
                Decimal("459.67"),
                aliases=["F", "fahrenheit"],
            ),
        ]

        # Emissions units (base: kg CO2e)
        emissions_units = [
            UnitDefinition(
                "kg CO2e",
                "kilogram_co2_equivalent",
                UnitCategory.EMISSIONS,
                "kg CO2e",
                Decimal("1"),
                aliases=["kgCO2e", "kg_co2e"],
            ),
            UnitDefinition(
                "t CO2e",
                "tonne_co2_equivalent",
                UnitCategory.EMISSIONS,
                "kg CO2e",
                Decimal("1000"),
                aliases=["tCO2e", "t_co2e"],
            ),
            UnitDefinition(
                "Mt CO2e",
                "megatonne_co2_equivalent",
                UnitCategory.EMISSIONS,
                "kg CO2e",
                Decimal("1000000000"),
                aliases=["MtCO2e"],
            ),
            UnitDefinition(
                "lb CO2e",
                "pound_co2_equivalent",
                UnitCategory.EMISSIONS,
                "kg CO2e",
                Decimal("0.453592"),
                aliases=["lbCO2e"],
            ),
        ]

        # Percentage and ratio units
        ratio_units = [
            UnitDefinition("%", "percent", UnitCategory.PERCENTAGE, "%", Decimal("1")),
            UnitDefinition(
                "‰",
                "permille",
                UnitCategory.PERCENTAGE,
                "%",
                Decimal("0.1"),
                aliases=["permille"],
            ),
            UnitDefinition("ratio", "ratio", UnitCategory.RATIO, "ratio", Decimal("1")),
            UnitDefinition(
                "fraction", "fraction", UnitCategory.RATIO, "ratio", Decimal("1")
            ),
        ]

        # Register all units
        all_units = (
            mass_units
            + energy_units
            + volume_units
            + area_units
            + length_units
            + time_units
            + temperature_units
            + emissions_units
            + ratio_units
        )

        for unit in all_units:
            self.register_unit(unit)

    def _initialize_conversion_rules(self):
        """Initialize custom conversion rules for complex conversions."""

        # Energy intensity conversions (energy per mass, area, etc.)
        intensity_rules = [
            ConversionRule("kWh/kg", "MJ/kg", "value * 3.6", "value / 3.6"),
            ConversionRule("BTU/lb", "kJ/kg", "value * 2.326", "value / 2.326"),
            ConversionRule("kWh/m²", "MJ/m²", "value * 3.6", "value / 3.6"),
        ]

        # Emissions intensity conversions
        emissions_intensity_rules = [
            ConversionRule(
                "kg CO2e/kWh", "g CO2e/MJ", "value * 1000 / 3.6", "value * 3.6 / 1000"
            ),
            ConversionRule(
                "t CO2e/MWh", "kg CO2e/GJ", "value * 1000 / 3.6", "value * 3.6 / 1000"
            ),
        ]

        # Register all rules
        all_rules = intensity_rules + emissions_intensity_rules

        for rule in all_rules:
            self.register_conversion_rule(rule)

    def register_unit(self, unit: UnitDefinition):
        """Register a unit definition.

        Args:
            unit: Unit definition to register
        """
        self.units[unit.symbol] = unit

        # Register aliases
        for alias in unit.aliases:
            self.unit_aliases[alias] = unit.symbol

        # Invalidate cached physical converter
        self._physical_converter = None

        self.logger.debug(
            "Unit registered",
            symbol=unit.symbol,
            name=unit.name,
            category=unit.category.value,
            aliases=unit.aliases,
        )

    def register_conversion_rule(self, rule: ConversionRule):
        """Register a custom conversion rule.

        Args:
            rule: Conversion rule to register
        """
        # Load-time guard: reject an unsafe conversion formula at registration
        # rather than only when first executed (codex F07 M2).
        for formula in (rule.formula, rule.reverse_formula):
            if formula and str(formula).strip():
                try:
                    validate_conversion_formula(str(formula))
                except ValueError as exc:
                    raise UnitConversionError(
                        f"Unsafe conversion formula for "
                        f"{rule.from_unit}->{rule.to_unit}: {exc}"
                    )

        key = (rule.from_unit, rule.to_unit)
        self.conversion_rules[key] = rule

        # Register reverse rule if provided
        if rule.reverse_formula:
            reverse_key = (rule.to_unit, rule.from_unit)
            reverse_rule = ConversionRule(
                rule.to_unit,
                rule.from_unit,
                rule.reverse_formula,
                rule.formula,
                rule.conditions,
                rule.metadata,
            )
            self.conversion_rules[reverse_key] = reverse_rule

        # Invalidate cached physical converter
        self._physical_converter = None

        self.logger.debug(
            "Conversion rule registered",
            from_unit=rule.from_unit,
            to_unit=rule.to_unit,
            formula=rule.formula,
        )

    def normalize_unit_symbol(self, unit_symbol: str) -> str:
        """Normalize unit symbol to canonical form.

        Args:
            unit_symbol: Unit symbol to normalize

        Returns:
            Canonical unit symbol

        Raises:
            UnitConversionError: If unit is not recognized
        """
        # Check direct match
        if unit_symbol in self.units:
            return unit_symbol

        # Check aliases
        if unit_symbol in self.unit_aliases:
            return self.unit_aliases[unit_symbol]

        # Case-insensitive search
        unit_lower = unit_symbol.lower()
        for symbol, unit in self.units.items():
            if symbol.lower() == unit_lower or unit.name.lower() == unit_lower:
                return symbol

            for alias in unit.aliases:
                if alias.lower() == unit_lower:
                    return symbol

        raise UnitConversionError(f"Unknown unit: {unit_symbol}")

    def get_unit_category(self, unit_symbol: str) -> UnitCategory:
        """Get the category of a unit.

        Args:
            unit_symbol: Unit symbol

        Returns:
            Unit category
        """
        normalized = self.normalize_unit_symbol(unit_symbol)
        return self.units[normalized].category

    def _build_physical_converter(self) -> PhysicalUnitConverter:
        """Build a PhysicalUnitConverter from current unit registry.

        The result is cached and invalidated when the registry is mutated
        (e.g., via reload_from_storage, register_unit, register_conversion_rule).
        """
        if self._physical_converter is not None:
            return self._physical_converter
        physical_units: dict[str, PhysicalUnit] = {}
        for symbol, unit in self.units.items():
            dimension = self._dimension_for_unit(unit)
            physical_units[symbol] = PhysicalUnit(
                symbol=symbol,
                name=unit.name,
                dimension=dimension,
                factor_to_base=unit.conversion_factor,
                offset_to_base=unit.conversion_offset,
                aliases=tuple(unit.aliases),
            )
        physical_rules = [
            PhysicalConversionRule(
                from_unit=from_unit,
                to_unit=to_unit,
                formula=rule.formula,
                priority=int(rule.metadata.get("priority", 100)),
                rule_id=str(rule.metadata.get("rule_id") or f"{from_unit}->{to_unit}"),
                valid_from=rule.metadata.get("valid_from"),
                valid_to=rule.metadata.get("valid_to"),
            )
            for (from_unit, to_unit), rule in self.conversion_rules.items()
        ]
        self._physical_converter = PhysicalUnitConverter(
            units=physical_units, rules=physical_rules, precision=self.precision
        )
        return self._physical_converter

    def get_physical_converter(self) -> PhysicalUnitConverter:
        """Return the cached PhysicalUnitConverter for the current unit registry."""
        return self._build_physical_converter()

    @staticmethod
    def _dimension_for_unit(unit: UnitDefinition) -> DimensionVector:
        """Map UnitDefinition to DimensionVector using metadata or category fallback."""
        dimension_meta = (
            unit.metadata.get("dimension_vector") if unit.metadata else None
        )
        if dimension_meta:
            return DimensionVector(dimension_meta)
        # Legacy category-based inference (preserved for compatibility)
        category_map = {
            UnitCategory.MASS: {"mass": 1},
            UnitCategory.ENERGY: {"energy": 1},
            UnitCategory.VOLUME: {"volume": 1},
            UnitCategory.AREA: {"area": 1},
            UnitCategory.LENGTH: {"length": 1},
            UnitCategory.TIME: {"time": 1},
            UnitCategory.TEMPERATURE: {"temperature": 1},
            UnitCategory.EMISSIONS: {"co2e": 1},
            UnitCategory.CURRENCY: {"currency": 1},
            UnitCategory.PERCENTAGE: {"ratio": 1},
            UnitCategory.RATIO: {"ratio": 1},
            UnitCategory.COUNT: {"count": 1},
            UnitCategory.SEMANTIC: {},
            UnitCategory.POWER: {"energy": 1, "time": -1},
            UnitCategory.PRESSURE: {"mass": 1, "length": -1, "time": -2},
            UnitCategory.SPEED: {"length": 1, "time": -1},
            UnitCategory.FORCE: {"mass": 1, "length": 1, "time": -2},
            UnitCategory.DENSITY: {"mass": 1, "volume": -1},
            UnitCategory.FLOW: {"volume": 1, "time": -1},
        }
        return DimensionVector(category_map.get(unit.category, {}))

    @staticmethod
    def _is_non_convertible(unit: UnitDefinition) -> bool:
        return bool(unit.metadata and unit.metadata.get("non_convertible"))

    def are_units_compatible(self, from_unit: str, to_unit: str) -> bool:
        """Check if two units are compatible for conversion.

        Args:
            from_unit: Source unit
            to_unit: Target unit

        Returns:
            True if units are compatible
        """
        try:
            from_normalized = self.normalize_unit_symbol(from_unit)
            to_normalized = self.normalize_unit_symbol(to_unit)

            if from_normalized == to_normalized:
                return True

            # Check for direct conversion rule
            if (from_normalized, to_normalized) in self.conversion_rules:
                return True

            from_def = self.units[from_normalized]
            to_def = self.units[to_normalized]
            if self._is_non_convertible(from_def) or self._is_non_convertible(to_def):
                return False

            # Conversion uses direct rules or same-category base-unit conversion.
            from_category = from_def.category
            to_category = to_def.category
            if from_category != to_category:
                return False

            # For same-category units, fail closed on a proven dimensional mismatch.
            try:
                physical = self._build_physical_converter()
                physical.parser.parse(from_normalized)
                physical.parser.parse(to_normalized)
                from_dim = self._dimension_for_unit(from_def)
                to_dim = self._dimension_for_unit(to_def)
                if not from_dim.is_compatible_with(to_dim):
                    return False
            except (UnitConversionError, ValueError, KeyError) as exc:
                self.logger.warning(
                    "Physical converter compatibility check failed, falling back to category comparison",
                    from_unit=from_unit,
                    to_unit=to_unit,
                    error=str(exc),
                )

            return True

        except UnitConversionError:
            return False

    def convert(
        self, value: Union[int, float, Decimal], from_unit: str, to_unit: str
    ) -> ConversionResult:
        """Convert a value from one unit to another.

        Args:
            value: Value to convert
            from_unit: Source unit
            to_unit: Target unit

        Returns:
            ConversionResult with converted value

        Raises:
            UnitConversionError: If conversion is not possible
        """
        try:
            self._log_conversion_start(value, from_unit, to_unit)

            # Normalize units
            from_normalized = self.normalize_unit_symbol(from_unit)
            to_normalized = self.normalize_unit_symbol(to_unit)

            # If same unit, return as-is
            if from_normalized == to_normalized:
                converted_value = Decimal(str(value))
                return ConversionResult(
                    original_value=value,
                    original_unit=from_unit,
                    converted_value=converted_value,
                    converted_unit=to_unit,
                    conversion_factor=Decimal("1"),
                    formula_used="identity",
                )

            # Check for direct conversion rule
            rule_key = (from_normalized, to_normalized)
            if rule_key in self.conversion_rules:
                return self._apply_conversion_rule(
                    value,
                    from_normalized,
                    to_normalized,
                    self.conversion_rules[rule_key],
                )

            from_def = self.units[from_normalized]
            to_def = self.units[to_normalized]
            if self._is_non_convertible(from_def) or self._is_non_convertible(to_def):
                raise UnitConversionError(
                    "Unit is marked non-convertible by catalog policy"
                )

            # Standard category-based conversion
            return self._convert_via_base_unit(value, from_normalized, to_normalized)

        except Exception as e:
            self.logger.error(
                "Unit conversion failed",
                error=str(e),
                value=str(value),
                from_unit=from_unit,
                to_unit=to_unit,
            )
            raise UnitConversionError(f"Conversion failed: {e}")

    def _log_conversion_start(
        self,
        value: Union[int, float, Decimal],
        from_unit: str,
        to_unit: str,
    ) -> None:
        self._conversion_start_log_count += 1
        count = self._conversion_start_log_count
        if count > self._conversion_start_sample_limit and count % 1000 != 0:
            return
        self._conversion_start_log_emitted += 1
        self.logger.debug(
            "Starting unit conversion",
            value=str(value),
            from_unit=from_unit,
            to_unit=to_unit,
            sample_count=count,
        )

    def _apply_conversion_rule(
        self,
        value: Union[int, float, Decimal],
        from_unit: str,
        to_unit: str,
        rule: ConversionRule,
    ) -> ConversionResult:
        """Apply a custom conversion rule.

        Args:
            value: Value to convert
            from_unit: Source unit (normalized)
            to_unit: Target unit (normalized)
            rule: Conversion rule to apply

        Returns:
            ConversionResult
        """
        # Evaluate formula using AST-based safe decimal evaluator (F-001)
        # Preserve Decimal precision throughout; avoid float() intermediate.
        try:
            decimal_value = Decimal(str(value))
            converted_value = _safe_decimal_eval(rule.formula, {"value": decimal_value})
            converted_value = converted_value.quantize(
                Decimal("0." + "0" * self.precision), rounding=ROUND_HALF_UP
            )

            # Calculate conversion factor
            conversion_factor = (
                converted_value / decimal_value if decimal_value != 0 else Decimal("0")
            )

            return ConversionResult(
                original_value=value,
                original_unit=from_unit,
                converted_value=converted_value,
                converted_unit=to_unit,
                conversion_factor=conversion_factor,
                formula_used=rule.formula,
                metadata=rule.metadata,
            )

        except Exception as e:
            raise UnitConversionError(f"Formula evaluation failed: {e}")

    def _convert_via_base_unit(
        self, value: Union[int, float, Decimal], from_unit: str, to_unit: str
    ) -> ConversionResult:
        """Convert via base unit of the category.

        Args:
            value: Value to convert
            from_unit: Source unit (normalized)
            to_unit: Target unit (normalized)

        Returns:
            ConversionResult
        """
        from_def = self.units[from_unit]
        to_def = self.units[to_unit]

        # Check compatibility
        if from_def.category != to_def.category:
            raise UnitConversionError(
                f"Incompatible unit categories: {from_def.category.value} vs {to_def.category.value}"
            )

        # Fail closed on a proven dimensional mismatch WITHIN the same category —
        # e.g. two distinct `ratio`-category units with different dimension_vectors
        # (cases_per_million_hours_worked vs hours_per_employee) must NOT linearly
        # convert through a shared base unit (codex F06 M1). Units that rely on the
        # category-fallback dimension (no explicit dimension_vector) keep identical
        # vectors per category, so this only rejects genuinely incompatible pairs.
        from_dim = self._dimension_for_unit(from_def)
        to_dim = self._dimension_for_unit(to_def)
        if not from_dim.is_compatible_with(to_dim):
            raise UnitConversionError(
                f"Incompatible unit dimensions: {from_unit} ({from_dim}) "
                f"vs {to_unit} ({to_dim})"
            )

        decimal_value = Decimal(str(value))

        # Special handling for temperature
        if from_def.category == UnitCategory.TEMPERATURE:
            # Handle specific temperature conversions
            if from_def.symbol == "°C" and to_def.symbol == "K":
                converted_value = decimal_value + Decimal("273.15")
            elif from_def.symbol == "K" and to_def.symbol == "°C":
                converted_value = decimal_value - Decimal("273.15")
            elif from_def.symbol == "°C" and to_def.symbol == "°F":
                converted_value = decimal_value * Decimal("9") / Decimal("5") + Decimal(
                    "32"
                )
            elif from_def.symbol == "°F" and to_def.symbol == "°C":
                converted_value = (
                    (decimal_value - Decimal("32")) * Decimal("5") / Decimal("9")
                )
            elif from_def.symbol == "°F" and to_def.symbol == "K":
                celsius_value = (
                    (decimal_value - Decimal("32")) * Decimal("5") / Decimal("9")
                )
                converted_value = celsius_value + Decimal("273.15")
            elif from_def.symbol == "K" and to_def.symbol == "°F":
                celsius_value = decimal_value - Decimal("273.15")
                converted_value = celsius_value * Decimal("9") / Decimal("5") + Decimal(
                    "32"
                )
            else:
                # Fallback to generic conversion
                base_value = (
                    decimal_value + from_def.conversion_offset
                ) * from_def.conversion_factor
                converted_value = (
                    base_value / to_def.conversion_factor
                ) - to_def.conversion_offset
        else:
            # Standard linear conversion
            # Convert to base unit
            base_value = decimal_value * from_def.conversion_factor

            # Convert from base unit to target
            converted_value = base_value / to_def.conversion_factor

        # Apply precision
        converted_value = converted_value.quantize(
            Decimal("0." + "0" * self.precision), rounding=ROUND_HALF_UP
        )

        # Calculate overall conversion factor
        if from_def.category == UnitCategory.TEMPERATURE:
            # Temperature conversions are affine (y = mx + b), not linear.
            # No single scalar factor exists — it varies with the input value.
            conversion_factor = None
        else:
            conversion_factor = from_def.conversion_factor / to_def.conversion_factor

        formula_used = (
            f"value * {from_def.conversion_factor} / {to_def.conversion_factor}"
        )
        if from_def.category == UnitCategory.TEMPERATURE:
            if from_def.symbol == "°C" and to_def.symbol == "K":
                formula_used = "value + 273.15"
            elif from_def.symbol == "K" and to_def.symbol == "°C":
                formula_used = "value - 273.15"
            elif from_def.symbol == "°C" and to_def.symbol == "°F":
                formula_used = "value * 9/5 + 32"
            elif from_def.symbol == "°F" and to_def.symbol == "°C":
                formula_used = "(value - 32) * 5/9"
            elif from_def.symbol == "°F" and to_def.symbol == "K":
                formula_used = "(value - 32) * 5/9 + 273.15"
            elif from_def.symbol == "K" and to_def.symbol == "°F":
                formula_used = "(value - 273.15) * 9/5 + 32"
            else:
                formula_used = f"(value + {from_def.conversion_offset}) * {from_def.conversion_factor} / {to_def.conversion_factor} - {to_def.conversion_offset}"

        result = ConversionResult(
            original_value=value,
            original_unit=from_unit,
            converted_value=converted_value,
            converted_unit=to_unit,
            conversion_factor=conversion_factor,
            formula_used=formula_used,
        )
        # Attach trace metadata when available via physical converter
        try:
            physical = self._build_physical_converter()
            p_result = physical.convert(Decimal(str(value)), from_unit, to_unit)
            if p_result.trace:
                result.metadata["trace"] = p_result.trace
        except (UnitConversionError, ValueError, KeyError):
            pass
        return result

    def get_supported_units(
        self, category: Optional[UnitCategory] = None
    ) -> List[Dict[str, Any]]:
        """Get list of supported units.

        Args:
            category: Optional category filter

        Returns:
            List of unit information dictionaries
        """
        units_info = []

        for symbol, unit in self.units.items():
            if category is None or unit.category == category:
                units_info.append(
                    {
                        "symbol": symbol,
                        "name": unit.name,
                        "category": unit.category.value,
                        "base_unit": unit.base_unit,
                        "aliases": unit.aliases,
                        "metadata": unit.metadata,
                    }
                )

        return sorted(units_info, key=lambda x: (x["category"], x["symbol"]))

    def get_conversion_path(self, from_unit: str, to_unit: str) -> List[str]:
        """Get the conversion path between two units.

        Args:
            from_unit: Source unit
            to_unit: Target unit

        Returns:
            List of units in conversion path
        """
        try:
            from_normalized = self.normalize_unit_symbol(from_unit)
            to_normalized = self.normalize_unit_symbol(to_unit)

            # Direct rule
            if (from_normalized, to_normalized) in self.conversion_rules:
                return [from_normalized, to_normalized]

            # Via base unit
            from_def = self.units[from_normalized]
            to_def = self.units[to_normalized]

            if from_def.category == to_def.category:
                if from_def.base_unit == from_normalized:
                    return [from_normalized, to_normalized]
                elif to_def.base_unit == to_normalized:
                    return [from_normalized, to_normalized]
                else:
                    return [from_normalized, from_def.base_unit, to_normalized]

            return []

        except UnitConversionError:
            return []

    def validate_unit_compatibility(self, units: List[str]) -> Dict[str, Any]:
        """Validate compatibility of multiple units.

        Args:
            units: List of unit symbols to validate

        Returns:
            Validation result dictionary
        """
        result = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "categories": {},
            "conversions": {},
        }

        normalized_units = []
        categories = set()

        # Normalize and categorize units
        for unit in units:
            try:
                normalized = self.normalize_unit_symbol(unit)
                normalized_units.append(normalized)

                unit_def = self.units[normalized]
                categories.add(unit_def.category)

                if unit_def.category.value not in result["categories"]:
                    result["categories"][unit_def.category.value] = []
                result["categories"][unit_def.category.value].append(normalized)

            except UnitConversionError:
                result["valid"] = False
                result["errors"].append(f"Unknown unit: {unit}")

        # Check cross-compatibility
        if len(categories) > 1:
            result["warnings"].append(
                f"Multiple unit categories found: {[cat.value for cat in categories]}"
            )

        # Check pairwise conversions
        for i, unit1 in enumerate(normalized_units):
            for unit2 in normalized_units[i + 1 :]:
                compatible = self.are_units_compatible(unit1, unit2)
                result["conversions"][f"{unit1} -> {unit2}"] = compatible

                if not compatible:
                    result["warnings"].append(
                        f"No conversion available: {unit1} -> {unit2}"
                    )

        return result

    # Database management methods
    def add_unit_to_database(
        self,
        symbol: str,
        name: str,
        category: str,
        base_unit: str,
        conversion_factor: float,
        conversion_offset: float = 0,
        aliases: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        permanent: bool = True,
    ) -> bool:
        """Add a new unit to the database.

        Args:
            symbol: Unit symbol
            name: Unit name
            category: Unit category
            base_unit: Base unit for the category
            conversion_factor: Conversion factor to base unit
            conversion_offset: Conversion offset (for temperature)
            aliases: List of aliases
            metadata: Additional metadata
            permanent: Whether to save to permanent database

        Returns:
            True if unit was added successfully
        """
        success = self.database.add_unit(
            symbol=symbol,
            name=name,
            category=category,
            base_unit=base_unit,
            conversion_factor=conversion_factor,
            conversion_offset=conversion_offset,
            aliases=aliases,
            metadata=metadata,
            save_to_database=permanent,
        )

        if success:
            # Reload the unit into converter
            try:
                unit_def = UnitDefinition(
                    symbol=symbol,
                    name=name,
                    category=UnitCategory(category),
                    base_unit=base_unit,
                    conversion_factor=Decimal(str(conversion_factor)),
                    conversion_offset=Decimal(str(conversion_offset)),
                    aliases=aliases or [],
                    metadata=metadata or {},
                )
                self.register_unit(unit_def)
            except Exception as e:
                self.logger.error(
                    "Failed to register new unit in converter",
                    symbol=symbol,
                    error=str(e),
                )
                return False

        return success

    def add_conversion_rule_to_database(
        self,
        from_unit: str,
        to_unit: str,
        formula: str,
        reverse_formula: Optional[str] = None,
        conditions: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        permanent: bool = True,
    ) -> bool:
        """Add a new conversion rule to the database.

        Args:
            from_unit: Source unit
            to_unit: Target unit
            formula: Conversion formula
            reverse_formula: Reverse conversion formula
            conditions: Conditions for rule application
            metadata: Additional metadata
            permanent: Whether to save to permanent database

        Returns:
            True if rule was added successfully
        """
        success = self.database.add_conversion_rule(
            from_unit=from_unit,
            to_unit=to_unit,
            formula=formula,
            reverse_formula=reverse_formula,
            conditions=conditions,
            metadata=metadata,
            save_to_database=permanent,
        )

        if success:
            # Reload the rule into converter
            try:
                rule = ConversionRule(
                    from_unit=from_unit,
                    to_unit=to_unit,
                    formula=formula,
                    reverse_formula=reverse_formula,
                    conditions=conditions or {},
                    metadata=metadata or {},
                )
                self.register_conversion_rule(rule)
            except Exception as e:
                self.logger.error(
                    "Failed to register new conversion rule in converter",
                    from_unit=from_unit,
                    to_unit=to_unit,
                    error=str(e),
                )
                return False

        return success

    def remove_unit_from_database(
        self, symbol: str, category: str, permanent: bool = True
    ) -> bool:
        """Remove a unit from the database.

        Args:
            symbol: Unit symbol to remove
            category: Unit category
            permanent: Whether to remove from permanent database

        Returns:
            True if unit was removed successfully
        """
        success = self.database.remove_unit(symbol, category, permanent)

        if success and symbol in self.units:
            # Remove from converter cache
            del self.units[symbol]

            # Remove aliases
            aliases_to_remove = [
                alias
                for alias, canonical in self.unit_aliases.items()
                if canonical == symbol
            ]
            for alias in aliases_to_remove:
                del self.unit_aliases[alias]

        return success

    def get_database_info(self) -> Dict[str, Any]:
        """Get information about the units database.

        Returns:
            Database information dictionary
        """
        return self.database.get_database_info()

    def export_database(self, export_path: str) -> bool:
        """Export units database to a file.

        Args:
            export_path: Path to export file

        Returns:
            True if export was successful
        """
        return self.database.export_database(export_path)

    def import_database(self, import_path: str, merge: bool = True) -> bool:
        """Import units database from a file.

        Args:
            import_path: Path to import file
            merge: Whether to merge with existing data or replace

        Returns:
            True if import was successful
        """
        success = self.database.import_database(import_path, merge)

        if success:
            # Reload all units and rules
            self.units.clear()
            self.unit_aliases.clear()
            self.conversion_rules.clear()
            self._load_from_database()

        return success

    def reload_database(self) -> bool:
        """Reload units and rules from database.

        Returns:
            True if reload was successful
        """
        try:
            # Clear current cache
            self.units.clear()
            self.unit_aliases.clear()
            self.conversion_rules.clear()

            # Reload database
            self.database.load_database()
            self._load_from_database()

            return True

        except Exception as e:
            self.logger.error("Failed to reload database", error=str(e))
            return False

    def _load_units_from_database(self):
        """Load units and conversion rules from database."""
        try:
            self.logger.info("Loading units from database")

            # Load units
            units = self.unit_db_manager.get_all_units()
            for unit in units:
                self.register_unit(unit)

            # Load conversion rules
            rules = self.unit_db_manager.get_conversion_rules()
            for rule in rules:
                self.register_conversion_rule(rule)

            self.logger.info(
                "Units loaded from database",
                units_count=len(units),
                rules_count=len(rules),
            )

        except Exception as e:
            self.logger.error("Failed to load units from database", error=str(e))
            # Fallback to hardcoded units
            self._initialize_standard_units()
            self._initialize_conversion_rules()

    def refresh_units_cache(self):
        """Refresh units cache from database."""
        if self.unit_db_manager:
            # Clear current cache
            self.units.clear()
            self.unit_aliases.clear()
            self.conversion_rules.clear()

            # Reload from database
            self._load_units_from_database()
        else:
            self.logger.warning("No database manager available for cache refresh")

    def create_unit_in_database(self, unit: UnitDefinition) -> bool:
        """Create a new unit in database and update cache.

        Args:
            unit: Unit definition to create

        Returns:
            True if successful
        """
        if not self.unit_db_manager:
            self.logger.warning("No database manager available")
            return False

        success = self.unit_db_manager.create_unit(unit)
        if success:
            # Update cache
            self.register_unit(unit)

        return success

    def update_unit_in_database(self, symbol: str, updates: Dict[str, Any]) -> bool:
        """Update unit in database and refresh cache.

        Args:
            symbol: Unit symbol to update
            updates: Dictionary of updates

        Returns:
            True if successful
        """
        if not self.unit_db_manager:
            self.logger.warning("No database manager available")
            return False

        success = self.unit_db_manager.update_unit(symbol, updates)
        if success:
            # Refresh cache
            self.refresh_units_cache()

        return success

    def delete_unit_from_database(self, symbol: str, soft_delete: bool = True) -> bool:
        """Delete unit from database and update cache.

        Args:
            symbol: Unit symbol to delete
            soft_delete: Whether to soft delete (deactivate) or hard delete

        Returns:
            True if successful
        """
        if not self.unit_db_manager:
            self.logger.warning("No database manager available")
            return False

        success = self.unit_db_manager.delete_unit(symbol, soft_delete)
        if success:
            # Update cache
            if symbol in self.units:
                unit = self.units[symbol]
                del self.units[symbol]

                # Remove aliases
                for alias in unit.aliases:
                    if alias in self.unit_aliases:
                        del self.unit_aliases[alias]

        return success

    def create_conversion_rule_in_database(self, rule: ConversionRule) -> bool:
        """Create conversion rule in database and update cache.

        Args:
            rule: Conversion rule to create

        Returns:
            True if successful
        """
        if not self.unit_db_manager:
            self.logger.warning("No database manager available")
            return False

        success = self.unit_db_manager.create_conversion_rule(rule)
        if success:
            # Update cache
            self.register_conversion_rule(rule)

        return success

    def export_units_configuration(self) -> Optional[Dict[str, Any]]:
        """Export current units configuration.

        Returns:
            Configuration dictionary or None if no database manager
        """
        if not self.unit_db_manager:
            self.logger.warning("No database manager available")
            return None

        return self.unit_db_manager.export_units_config()

    def import_units_configuration(self, config: Dict[str, Any]) -> bool:
        """Import units configuration and refresh cache.

        Args:
            config: Configuration dictionary

        Returns:
            True if successful
        """
        if not self.unit_db_manager:
            self.logger.warning("No database manager available")
            return False

        success = self.unit_db_manager.import_units_config(config)
        if success:
            # Refresh cache
            self.refresh_units_cache()

        return success
