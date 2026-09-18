"""Unit database management system."""

import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
from src.calculation.unit_converter import (
    ConversionRule,
    UnitCategory,
    UnitConversionError,
    UnitDefinition,
)

logger = structlog.get_logger(__name__)


class UnitDatabase:
    """Manages unit definitions and conversion rules from database files."""

    def __init__(self, database_path: Optional[str] = None):
        """Initialize unit database.

        Args:
            database_path: Path to units database file. If None, uses default.
        """
        self.logger = logger.bind(component="UnitDatabase")

        if database_path is None:
            # Use default database file
            current_dir = Path(__file__).parent
            self.database_path = current_dir.parent / "data" / "units_database.json"
        else:
            self.database_path = Path(database_path)

        self.database_data: Dict[str, Any] = {}
        self.custom_units: Dict[str, Dict[str, Any]] = {}
        self.custom_rules: List[Dict[str, Any]] = []

        # Load database
        self.load_database()

    def load_database(self):
        """Load units database from file."""
        try:
            if not self.database_path.exists():
                self.logger.warning(
                    "Database file not found, creating empty database",
                    path=str(self.database_path),
                )
                self.database_data = self._create_empty_database()
                return

            with open(self.database_path, "r", encoding="utf-8") as f:
                self.database_data = json.load(f)

            self.logger.info(
                "Units database loaded successfully",
                path=str(self.database_path),
                version=self.database_data.get("version", "unknown"),
                categories_count=len(self.database_data.get("categories", {})),
            )

        except Exception as e:
            self.logger.error(
                "Failed to load units database",
                error=str(e),
                path=str(self.database_path),
            )
            raise UnitConversionError(f"Failed to load units database: {e}")

    def save_database(self):
        """Save current database to file."""
        try:
            # Update metadata
            self.database_data["last_updated"] = datetime.now().isoformat()

            # Ensure directory exists
            self.database_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.database_path, "w", encoding="utf-8") as f:
                json.dump(self.database_data, f, indent=2, ensure_ascii=False)

            self.logger.info(
                "Units database saved successfully", path=str(self.database_path)
            )

        except Exception as e:
            self.logger.error(
                "Failed to save units database",
                error=str(e),
                path=str(self.database_path),
            )
            raise UnitConversionError(f"Failed to save units database: {e}")

    def _create_empty_database(self) -> Dict[str, Any]:
        """Create empty database structure."""
        return {
            "version": "1.0.0",
            "last_updated": datetime.now().isoformat(),
            "description": "Empty units database",
            "categories": {},
            "custom_conversion_rules": [],
        }

    def get_unit_definitions(self) -> List[UnitDefinition]:
        """Get all unit definitions from database.

        Returns:
            List of UnitDefinition objects
        """
        definitions = []

        categories = self.database_data.get("categories", {})

        for category_name, category_data in categories.items():
            try:
                category_enum = UnitCategory(category_name)
            except ValueError:
                self.logger.warning(
                    "Unknown unit category in database", category=category_name
                )
                continue

            base_unit = category_data.get("base_unit", "")
            units = category_data.get("units", {})

            for unit_symbol, unit_data in units.items():
                try:
                    definition = UnitDefinition(
                        symbol=unit_data["symbol"],
                        name=unit_data["name"],
                        category=category_enum,
                        base_unit=base_unit,
                        conversion_factor=Decimal(str(unit_data["conversion_factor"])),
                        conversion_offset=Decimal(
                            str(unit_data.get("conversion_offset", 0))
                        ),
                        aliases=unit_data.get("aliases", []),
                        metadata=unit_data.get("metadata", {}),
                    )
                    definitions.append(definition)

                except Exception as e:
                    self.logger.warning(
                        "Failed to create unit definition",
                        unit_symbol=unit_symbol,
                        error=str(e),
                    )

        # Add custom units
        for category_name, units in self.custom_units.items():
            try:
                category_enum = UnitCategory(category_name)
            except ValueError:
                continue

            for unit_symbol, unit_data in units.items():
                try:
                    definition = UnitDefinition(
                        symbol=unit_data["symbol"],
                        name=unit_data["name"],
                        category=category_enum,
                        base_unit=unit_data["base_unit"],
                        conversion_factor=Decimal(str(unit_data["conversion_factor"])),
                        conversion_offset=Decimal(
                            str(unit_data.get("conversion_offset", 0))
                        ),
                        aliases=unit_data.get("aliases", []),
                        metadata=unit_data.get("metadata", {"custom": True}),
                    )
                    definitions.append(definition)

                except Exception as e:
                    self.logger.warning(
                        "Failed to create custom unit definition",
                        unit_symbol=unit_symbol,
                        error=str(e),
                    )

        return definitions

    def get_conversion_rules(self) -> List[ConversionRule]:
        """Get all conversion rules from database.

        Returns:
            List of ConversionRule objects
        """
        rules = []

        # Standard rules from database
        custom_rules = self.database_data.get("custom_conversion_rules", [])

        for rule_data in custom_rules:
            try:
                rule = ConversionRule(
                    from_unit=rule_data["from_unit"],
                    to_unit=rule_data["to_unit"],
                    formula=rule_data["formula"],
                    reverse_formula=rule_data.get("reverse_formula"),
                    conditions=rule_data.get("conditions", {}),
                    metadata=rule_data.get("metadata", {}),
                )
                rules.append(rule)

            except Exception as e:
                self.logger.warning(
                    "Failed to create conversion rule",
                    rule_data=rule_data,
                    error=str(e),
                )

        # Custom rules
        for rule_data in self.custom_rules:
            try:
                rule = ConversionRule(
                    from_unit=rule_data["from_unit"],
                    to_unit=rule_data["to_unit"],
                    formula=rule_data["formula"],
                    reverse_formula=rule_data.get("reverse_formula"),
                    conditions=rule_data.get("conditions", {}),
                    metadata=rule_data.get("metadata", {"custom": True}),
                )
                rules.append(rule)

            except Exception as e:
                self.logger.warning(
                    "Failed to create custom conversion rule",
                    rule_data=rule_data,
                    error=str(e),
                )

        return rules

    def add_unit(
        self,
        symbol: str,
        name: str,
        category: str,
        base_unit: str,
        conversion_factor: float,
        conversion_offset: float = 0,
        aliases: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        save_to_database: bool = False,
    ) -> bool:
        """Add a new unit definition.

        Args:
            symbol: Unit symbol
            name: Unit name
            category: Unit category
            base_unit: Base unit for the category
            conversion_factor: Conversion factor to base unit
            conversion_offset: Conversion offset (for temperature)
            aliases: List of aliases
            metadata: Additional metadata
            save_to_database: Whether to save to permanent database

        Returns:
            True if unit was added successfully
        """
        try:
            # Validate category
            try:
                UnitCategory(category)
            except ValueError:
                raise UnitConversionError(f"Invalid unit category: {category}")

            unit_data = {
                "symbol": symbol,
                "name": name,
                "base_unit": base_unit,
                "conversion_factor": conversion_factor,
                "conversion_offset": conversion_offset,
                "aliases": aliases or [],
                "metadata": metadata or {},
            }

            if save_to_database:
                # Add to permanent database
                if "categories" not in self.database_data:
                    self.database_data["categories"] = {}

                if category not in self.database_data["categories"]:
                    self.database_data["categories"][category] = {
                        "base_unit": base_unit,
                        "units": {},
                    }

                self.database_data["categories"][category]["units"][symbol] = unit_data
                self.save_database()
            else:
                # Add to custom units (temporary)
                if category not in self.custom_units:
                    self.custom_units[category] = {}

                self.custom_units[category][symbol] = unit_data

            self.logger.info(
                "Unit added successfully",
                symbol=symbol,
                category=category,
                permanent=save_to_database,
            )

            return True

        except Exception as e:
            self.logger.error("Failed to add unit", symbol=symbol, error=str(e))
            return False

    def add_conversion_rule(
        self,
        from_unit: str,
        to_unit: str,
        formula: str,
        reverse_formula: Optional[str] = None,
        conditions: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        save_to_database: bool = False,
    ) -> bool:
        """Add a new conversion rule.

        Args:
            from_unit: Source unit
            to_unit: Target unit
            formula: Conversion formula
            reverse_formula: Reverse conversion formula
            conditions: Conditions for rule application
            metadata: Additional metadata
            save_to_database: Whether to save to permanent database

        Returns:
            True if rule was added successfully
        """
        try:
            rule_data = {
                "from_unit": from_unit,
                "to_unit": to_unit,
                "formula": formula,
                "reverse_formula": reverse_formula,
                "conditions": conditions or {},
                "metadata": metadata or {},
            }

            if save_to_database:
                # Add to permanent database
                if "custom_conversion_rules" not in self.database_data:
                    self.database_data["custom_conversion_rules"] = []

                self.database_data["custom_conversion_rules"].append(rule_data)
                self.save_database()
            else:
                # Add to custom rules (temporary)
                self.custom_rules.append(rule_data)

            self.logger.info(
                "Conversion rule added successfully",
                from_unit=from_unit,
                to_unit=to_unit,
                permanent=save_to_database,
            )

            return True

        except Exception as e:
            self.logger.error(
                "Failed to add conversion rule",
                from_unit=from_unit,
                to_unit=to_unit,
                error=str(e),
            )
            return False

    def remove_unit(
        self, symbol: str, category: str, from_database: bool = False
    ) -> bool:
        """Remove a unit definition.

        Args:
            symbol: Unit symbol to remove
            category: Unit category
            from_database: Whether to remove from permanent database

        Returns:
            True if unit was removed successfully
        """
        try:
            if from_database:
                # Remove from permanent database
                if category in self.database_data.get(
                    "categories", {}
                ) and symbol in self.database_data["categories"][category].get(
                    "units", {}
                ):

                    del self.database_data["categories"][category]["units"][symbol]

                    # Remove category if empty
                    if not self.database_data["categories"][category]["units"]:
                        del self.database_data["categories"][category]

                    self.save_database()

                    self.logger.info(
                        "Unit removed from database", symbol=symbol, category=category
                    )
                    return True
            else:
                # Remove from custom units
                if (
                    category in self.custom_units
                    and symbol in self.custom_units[category]
                ):

                    del self.custom_units[category][symbol]

                    # Remove category if empty
                    if not self.custom_units[category]:
                        del self.custom_units[category]

                    self.logger.info(
                        "Custom unit removed", symbol=symbol, category=category
                    )
                    return True

            return False

        except Exception as e:
            self.logger.error(
                "Failed to remove unit", symbol=symbol, category=category, error=str(e)
            )
            return False

    def get_database_info(self) -> Dict[str, Any]:
        """Get information about the database.

        Returns:
            Database information dictionary
        """
        categories = self.database_data.get("categories", {})

        info = {
            "version": self.database_data.get("version", "unknown"),
            "last_updated": self.database_data.get("last_updated", "unknown"),
            "description": self.database_data.get("description", ""),
            "database_path": str(self.database_path),
            "categories_count": len(categories),
            "categories": {},
            "custom_units_count": sum(
                len(units) for units in self.custom_units.values()
            ),
            "custom_rules_count": len(self.custom_rules),
            "total_conversion_rules": len(
                self.database_data.get("custom_conversion_rules", [])
            ),
        }

        # Category details
        for category_name, category_data in categories.items():
            info["categories"][category_name] = {
                "base_unit": category_data.get("base_unit", ""),
                "units_count": len(category_data.get("units", {})),
                "units": list(category_data.get("units", {}).keys()),
            }

        return info

    def export_database(self, export_path: str) -> bool:
        """Export current database to a file.

        Args:
            export_path: Path to export file

        Returns:
            True if export was successful
        """
        try:
            export_data = self.database_data.copy()

            # Add custom units and rules to export
            if self.custom_units:
                for category, units in self.custom_units.items():
                    if category not in export_data.get("categories", {}):
                        export_data.setdefault("categories", {})[category] = {
                            "base_unit": "",
                            "units": {},
                        }

                    export_data["categories"][category]["units"].update(units)

            if self.custom_rules:
                export_data.setdefault("custom_conversion_rules", []).extend(
                    self.custom_rules
                )

            # Update metadata
            export_data["last_updated"] = datetime.now().isoformat()
            export_data["exported_from"] = str(self.database_path)

            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            self.logger.info("Database exported successfully", export_path=export_path)

            return True

        except Exception as e:
            self.logger.error(
                "Failed to export database", export_path=export_path, error=str(e)
            )
            return False

    def import_database(self, import_path: str, merge: bool = True) -> bool:
        """Import database from a file.

        Args:
            import_path: Path to import file
            merge: Whether to merge with existing data or replace

        Returns:
            True if import was successful
        """
        try:
            with open(import_path, "r", encoding="utf-8") as f:
                import_data = json.load(f)

            if merge:
                # Merge with existing data
                categories = self.database_data.setdefault("categories", {})
                import_categories = import_data.get("categories", {})

                for category_name, category_data in import_categories.items():
                    if category_name not in categories:
                        categories[category_name] = category_data
                    else:
                        # Merge units
                        categories[category_name]["units"].update(
                            category_data.get("units", {})
                        )

                # Merge conversion rules
                existing_rules = self.database_data.setdefault(
                    "custom_conversion_rules", []
                )
                import_rules = import_data.get("custom_conversion_rules", [])

                # Avoid duplicates
                existing_rule_keys = {
                    (rule["from_unit"], rule["to_unit"]) for rule in existing_rules
                }

                for rule in import_rules:
                    rule_key = (rule["from_unit"], rule["to_unit"])
                    if rule_key not in existing_rule_keys:
                        existing_rules.append(rule)
            else:
                # Replace existing data
                self.database_data = import_data

            # Update metadata
            self.database_data["last_updated"] = datetime.now().isoformat()
            self.database_data["imported_from"] = import_path

            self.save_database()

            self.logger.info(
                "Database imported successfully", import_path=import_path, merge=merge
            )

            return True

        except Exception as e:
            self.logger.error(
                "Failed to import database", import_path=import_path, error=str(e)
            )
            return False
