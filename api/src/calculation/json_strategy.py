"""
JSON file storage strategy for unit data.

This module implements the JSON file backend for unit storage,
providing fallback access to units when PostgreSQL is unavailable.
This is primarily used for testing and development.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
from src.calculation.storage_strategy import StorageInfo, StorageStrategy

logger = structlog.get_logger(__name__)


class JSONStrategy(StorageStrategy):
    """JSON file storage strategy for units (fallback/testing)."""

    def __init__(self, json_path: Optional[str] = None):
        """
        Initialize JSON storage strategy.

        Args:
            json_path: Path to JSON file. If None, uses default location.
        """
        self.json_path = json_path
        self.unit_database = None
        self.logger = logger.bind(component="JSONStrategy")
        self._available = None  # Cache availability check

    def is_available(self) -> bool:
        """
        Check if JSON file is available.

        Returns:
            True if JSON file exists and is readable, False otherwise
        """
        if self._available is not None:
            return self._available

        try:
            from src.calculation.unit_database import UnitDatabase

            self.unit_database = UnitDatabase(self.json_path)
            self._available = True
            self.logger.info("JSON storage available")
            return True

        except Exception as e:
            self._available = False
            self.logger.debug(
                "JSON storage not available", error=str(e), error_type=type(e).__name__
            )
            return False

    def _get_unit_database(self):
        """Get or create UnitDatabase instance."""
        if self.unit_database is None:
            from src.calculation.unit_database import UnitDatabase

            self.unit_database = UnitDatabase(self.json_path)
        return self.unit_database

    def load_units(self) -> List[Dict[str, Any]]:
        """
        Load all units from JSON file.

        Returns:
            List of unit dictionaries
        """
        try:
            database = self._get_unit_database()

            # Get unit definitions and convert to dict format
            unit_definitions = database.get_unit_definitions()

            units = []
            for unit_def in unit_definitions:
                units.append(
                    {
                        "symbol": unit_def.symbol,
                        "name": unit_def.name,
                        "category": unit_def.category.value,
                        "base_unit": unit_def.base_unit,
                        "conversion_factor": float(unit_def.conversion_factor),
                        "conversion_offset": float(unit_def.conversion_offset),
                        "aliases": unit_def.aliases,
                        "metadata": unit_def.metadata,
                    }
                )

            self.logger.debug("Units loaded from JSON", count=len(units))

            return units

        except Exception as e:
            self.logger.error("Failed to load units from JSON", error=str(e))
            raise

    def load_categories(self) -> List[Dict[str, Any]]:
        """
        Load all unit categories from JSON file.

        Returns:
            List of category dictionaries
        """
        try:
            database = self._get_unit_database()

            # Extract categories from database data
            categories_data = database.database_data.get("categories", {})

            categories = []
            for cat_name, cat_info in categories_data.items():
                categories.append(
                    {
                        "name": cat_name,
                        "base_unit": cat_info.get("base_unit", ""),
                        "description": cat_info.get("description", ""),
                        "special_conversions": cat_info.get(
                            "special_conversions", False
                        ),
                    }
                )

            self.logger.debug("Categories loaded from JSON", count=len(categories))

            return categories

        except Exception as e:
            self.logger.error("Failed to load categories from JSON", error=str(e))
            raise

    def load_conversion_rules(self) -> List[Dict[str, Any]]:
        """
        Load all conversion rules from JSON file.

        Returns:
            List of conversion rule dictionaries
        """
        try:
            database = self._get_unit_database()

            # Get conversion rules
            rule_definitions = database.get_conversion_rules()

            rules = []
            for rule_def in rule_definitions:
                rules.append(
                    {
                        "from_unit": rule_def.from_unit,
                        "to_unit": rule_def.to_unit,
                        "formula": rule_def.formula,
                        "reverse_formula": rule_def.reverse_formula,
                        "conditions": rule_def.conditions,
                        "metadata": rule_def.metadata,
                    }
                )

            self.logger.debug("Conversion rules loaded from JSON", count=len(rules))

            return rules

        except Exception as e:
            self.logger.error("Failed to load conversion rules from JSON", error=str(e))
            raise

    def get_storage_info(self) -> StorageInfo:
        """
        Get information about JSON storage.

        Returns:
            StorageInfo with JSON file details
        """
        from pathlib import Path

        # Get actual file path
        if self.unit_database:
            file_path = str(self.unit_database.database_path)
        else:
            # Default path
            file_path = str(
                Path(__file__).parent.parent / "data" / "units_database.json"
            )

        return StorageInfo(
            backend="json",
            available=self.is_available(),
            read_only=True,  # JSON is read-only (fallback mode)
            location=file_path,
            metadata={
                "database_initialized": self.unit_database is not None,
                "file_exists": Path(file_path).exists() if file_path else False,
            },
        )
