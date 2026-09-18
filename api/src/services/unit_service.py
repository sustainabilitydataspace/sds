"""Unit service for managing units and conversions."""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

import structlog
from src.calculation.safe_eval import safe_eval
from src.database.models import ConversionRule, Unit, UnitCategory
from src.database.repositories.unit_repository import UnitRepository

logger = structlog.get_logger(__name__)


@dataclass
class ConversionResult:
    """Result of a unit conversion."""

    original_value: Decimal
    original_unit: str
    converted_value: Decimal
    converted_unit: str
    conversion_factor: Decimal | None
    formula_used: str
    metadata: Dict[str, Any] = None


class UnitConversionError(Exception):
    """Exception raised for unit conversion errors."""

    pass


class UnitService:
    """Service for unit management and conversions."""

    def __init__(self, db: Session, precision: int = 6):
        """Initialize unit service.

        Args:
            db: Database session
            precision: Decimal precision for conversions
        """
        self.db = db
        self.repository = UnitRepository(db)
        self.precision = precision
        self.logger = logger.bind(component="UnitService")

    def get_all_units(self) -> List[Dict[str, Any]]:
        """Get all units from database.

        Returns:
            List of unit dictionaries with all properties
        """
        units = self.repository.get_units()

        return [
            {
                "name": unit.name,
                "symbol": unit.symbol,
                "category": unit.category.name,
                "base_unit": unit.category.base_unit or unit.symbol,
                "aliases": unit.aliases or [],
                "conversion_factor": (
                    float(unit.conversion_factor) if unit.conversion_factor else 1.0
                ),
                "conversion_offset": (
                    float(unit.conversion_offset) if unit.conversion_offset else 0.0
                ),
                "metadata": unit.unit_metadata or {},
            }
            for unit in units
        ]

    def get_all_categories(self) -> List[Dict[str, Any]]:
        """Get all unit categories from database.

        Returns:
            List of category dictionaries
        """
        categories = self.repository.get_unit_categories()

        return [
            {
                "name": category.name,
                "description": category.description or "",
                "base_unit": category.base_unit or "",
                "special_conversions": category.special_conversions or False,
            }
            for category in categories
        ]

    def get_all_conversion_rules(self) -> List[Dict[str, Any]]:
        """Get all conversion rules from database.

        Returns:
            List of conversion rule dictionaries
        """
        # Get all conversion rules from repository
        from src.database.models import ConversionRule

        rules = (
            self.db.query(ConversionRule).filter(ConversionRule.is_active == True).all()
        )

        return [
            {
                "from_unit": rule.from_unit,
                "to_unit": rule.to_unit,
                "formula": rule.formula,
                "reverse_formula": rule.reverse_formula or None,
                "description": rule.description or None,
                "conditions": rule.conditions or {},
                "metadata": rule.rule_metadata or {},
            }
            for rule in rules
        ]

    def get_supported_units(self, category_name: str = None) -> List[Dict[str, Any]]:
        """Get list of supported units.

        Args:
            category_name: Optional category filter

        Returns:
            List of unit dictionaries
        """
        units = self.repository.get_units(category_name=category_name)

        return [
            {
                "symbol": unit.symbol,
                "name": unit.name,
                "category": unit.category.name,
                "base_unit": unit.category.base_unit,
                "aliases": unit.aliases or [],
            }
            for unit in units
        ]

    def get_unit_categories(self) -> List[Dict[str, Any]]:
        """Get all unit categories."""
        categories = self.repository.get_unit_categories()

        return [
            {
                "name": category.name,
                "base_unit": category.base_unit,
                "description": category.description,
                "special_conversions": category.special_conversions,
                "units_count": len(category.units),
            }
            for category in categories
        ]

    def convert(self, value: float, from_unit: str, to_unit: str) -> ConversionResult:
        """Convert value between units.

        Args:
            value: Value to convert
            from_unit: Source unit symbol
            to_unit: Target unit symbol

        Returns:
            ConversionResult with conversion details

        Raises:
            UnitConversionError: If conversion is not possible
        """
        try:
            # Convert to Decimal for precision
            decimal_value = Decimal(str(value))

            # Check if units are the same
            if from_unit == to_unit:
                return ConversionResult(
                    original_value=decimal_value,
                    original_unit=from_unit,
                    converted_value=decimal_value,
                    converted_unit=to_unit,
                    conversion_factor=Decimal("1"),
                    formula_used="identity",
                )

            # Get unit definitions
            from_unit_obj = self.repository.get_unit_by_symbol(from_unit)
            to_unit_obj = self.repository.get_unit_by_symbol(to_unit)

            if not from_unit_obj:
                raise UnitConversionError(f"Unknown unit: {from_unit}")
            if not to_unit_obj:
                raise UnitConversionError(f"Unknown unit: {to_unit}")

            # Check if units are compatible (same category)
            if from_unit_obj.category_id != to_unit_obj.category_id:
                # Try custom conversion rule
                return self._convert_with_custom_rule(decimal_value, from_unit, to_unit)

            # Standard conversion within same category
            return self._convert_within_category(
                decimal_value, from_unit_obj, to_unit_obj
            )

        except Exception as e:
            self.logger.error(
                "Unit conversion failed",
                error=str(e),
                from_unit=from_unit,
                to_unit=to_unit,
                value=value,
            )
            raise UnitConversionError(f"Conversion failed: {e}")

    def _convert_within_category(
        self, value: Decimal, from_unit: Unit, to_unit: Unit
    ) -> ConversionResult:
        """Convert between units in the same category."""
        # Handle temperature conversions (with offset)
        if (
            from_unit.category.special_conversions
            and from_unit.category.name == "temperature"
        ):
            return self._convert_temperature(value, from_unit, to_unit)

        # Standard conversion: value -> base_unit -> target_unit
        # Step 1: Convert to base unit
        base_value = value * Decimal(str(from_unit.conversion_factor))

        # Step 2: Convert from base unit to target unit
        converted_value = base_value / Decimal(str(to_unit.conversion_factor))

        # Round to specified precision
        converted_value = converted_value.quantize(
            Decimal("0." + "0" * self.precision), rounding=ROUND_HALF_UP
        )

        # Calculate overall conversion factor
        conversion_factor = Decimal(str(from_unit.conversion_factor)) / Decimal(
            str(to_unit.conversion_factor)
        )

        return ConversionResult(
            original_value=value,
            original_unit=from_unit.symbol,
            converted_value=converted_value,
            converted_unit=to_unit.symbol,
            conversion_factor=conversion_factor,
            formula_used=f"value * {from_unit.conversion_factor} / {to_unit.conversion_factor}",
        )

    def _convert_temperature(
        self, value: Decimal, from_unit: Unit, to_unit: Unit
    ) -> ConversionResult:
        """Convert temperature units with offset handling."""
        # Convert to Kelvin first
        kelvin_value = value * Decimal(str(from_unit.conversion_factor)) + Decimal(
            str(from_unit.conversion_offset)
        )

        # Convert from Kelvin to target unit
        converted_value = (
            kelvin_value - Decimal(str(to_unit.conversion_offset))
        ) / Decimal(str(to_unit.conversion_factor))

        # Round to specified precision
        converted_value = converted_value.quantize(
            Decimal("0." + "0" * self.precision), rounding=ROUND_HALF_UP
        )

        return ConversionResult(
            original_value=value,
            original_unit=from_unit.symbol,
            converted_value=converted_value,
            converted_unit=to_unit.symbol,
            conversion_factor=None,
            formula_used="temperature conversion via Kelvin",
        )

    def _convert_with_custom_rule(
        self, value: Decimal, from_unit: str, to_unit: str
    ) -> ConversionResult:
        """Convert using custom conversion rule."""
        rule = self.repository.get_conversion_rule(from_unit, to_unit)

        if not rule:
            # Try reverse rule
            reverse_rule = self.repository.get_conversion_rule(to_unit, from_unit)
            if reverse_rule and reverse_rule.reverse_formula:
                return self._apply_conversion_formula(
                    value, reverse_rule.reverse_formula, from_unit, to_unit
                )

            raise UnitConversionError(
                f"No conversion rule found between {from_unit} and {to_unit}"
            )

        return self._apply_conversion_formula(value, rule.formula, from_unit, to_unit)

    def _apply_conversion_formula(
        self, value: Decimal, formula: str, from_unit: str, to_unit: str
    ) -> ConversionResult:
        """Apply a conversion formula."""
        try:
            # Evaluate the formula using AST-based safe evaluator (F-001)
            converted_value = Decimal(str(safe_eval(formula, {"value": float(value)})))

            # Round to specified precision
            converted_value = converted_value.quantize(
                Decimal("0." + "0" * self.precision), rounding=ROUND_HALF_UP
            )

            # Calculate conversion factor
            conversion_factor = converted_value / value if value != 0 else Decimal("0")

            return ConversionResult(
                original_value=value,
                original_unit=from_unit,
                converted_value=converted_value,
                converted_unit=to_unit,
                conversion_factor=conversion_factor,
                formula_used=formula,
            )

        except Exception as e:
            raise UnitConversionError(
                f"Failed to apply conversion formula '{formula}': {e}"
            )

    def are_units_compatible(self, from_unit: str, to_unit: str) -> bool:
        """Check if two units are compatible for conversion."""
        try:
            # Same unit is always compatible
            if from_unit == to_unit:
                return True

            # Get unit definitions
            from_unit_obj = self.repository.get_unit_by_symbol(from_unit)
            to_unit_obj = self.repository.get_unit_by_symbol(to_unit)

            if not from_unit_obj or not to_unit_obj:
                return False

            # Same category units are compatible
            if from_unit_obj.category_id == to_unit_obj.category_id:
                return True

            # Check for custom conversion rule
            rule = self.repository.get_conversion_rule(from_unit, to_unit)
            if rule:
                return True

            # Check for reverse rule
            reverse_rule = self.repository.get_conversion_rule(to_unit, from_unit)
            if reverse_rule and reverse_rule.reverse_formula:
                return True

            return False

        except Exception:
            return False

    def get_conversion_path(self, from_unit: str, to_unit: str) -> List[str]:
        """Get the conversion path between two units."""
        if not self.are_units_compatible(from_unit, to_unit):
            return []

        if from_unit == to_unit:
            return [from_unit]

        # For now, return direct path
        # In a more complex system, this could find multi-step conversions
        return [from_unit, to_unit]
