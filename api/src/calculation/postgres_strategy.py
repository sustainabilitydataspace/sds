"""
PostgreSQL storage strategy for unit data.

This module implements the PostgreSQL backend for unit storage,
providing access to units, categories, and conversion rules stored
in the PostgreSQL database.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import structlog
from src.calculation.storage_strategy import StorageInfo, StorageStrategy
from src.config.settings import settings

logger = structlog.get_logger(__name__)


class PostgresStrategy(StorageStrategy):
    """PostgreSQL storage strategy for units."""

    def __init__(self, db_session: Optional[Session] = None):
        """
        Initialize PostgreSQL storage strategy.

        Args:
            db_session: SQLAlchemy session. If None, creates new session.
        """
        self.db_session = db_session
        self.unit_service = None
        self.logger = logger.bind(component="PostgresStrategy")
        self._available = None  # Cache availability check

    def _ensure_session(self) -> Session:
        """Get or create the SQLAlchemy session used by this strategy."""
        if self.db_session is None:
            from src.database.session import SessionLocal

            self.db_session = SessionLocal()
        return self.db_session

    def is_available(self) -> bool:
        """
        Check if PostgreSQL is available.

        Returns:
            True if PostgreSQL connection works, False otherwise
        """
        if self._available is not None:
            return self._available

        try:
            session = self._ensure_session()

            # Test connection with simple query
            from sqlalchemy import text

            session.execute(text("SELECT 1"))
            self._available = True
            self.logger.info("PostgreSQL storage available")
            return True

        except Exception as e:
            self._available = False
            self.logger.debug(
                "PostgreSQL storage not available",
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    def _get_unit_service(self):
        """Get or create UnitService instance."""
        if self.unit_service is None:
            from src.services.unit_service import UnitService

            self.unit_service = UnitService(self._ensure_session())
        return self.unit_service

    def load_units(self) -> List[Dict[str, Any]]:
        """
        Load all units from PostgreSQL.

        Returns:
            List of unit dictionaries with all properties
        """
        try:
            service = self._get_unit_service()
            units = service.get_all_units()

            self.logger.debug("Units loaded from PostgreSQL", count=len(units))

            return units

        except Exception as e:
            self.logger.error("Failed to load units from PostgreSQL", error=str(e))
            raise

    def load_categories(self) -> List[Dict[str, Any]]:
        """
        Load all unit categories from PostgreSQL.

        Returns:
            List of category dictionaries
        """
        try:
            service = self._get_unit_service()
            categories = service.get_all_categories()

            self.logger.debug(
                "Categories loaded from PostgreSQL", count=len(categories)
            )

            return categories

        except Exception as e:
            self.logger.error("Failed to load categories from PostgreSQL", error=str(e))
            raise

    def load_conversion_rules(self) -> List[Dict[str, Any]]:
        """
        Load all conversion rules from PostgreSQL.

        Returns:
            List of conversion rule dictionaries
        """
        try:
            service = self._get_unit_service()
            rules = service.get_all_conversion_rules()

            self.logger.debug(
                "Conversion rules loaded from PostgreSQL", count=len(rules)
            )

            return rules

        except Exception as e:
            self.logger.error(
                "Failed to load conversion rules from PostgreSQL", error=str(e)
            )
            raise

    def get_storage_info(self) -> StorageInfo:
        """
        Get information about PostgreSQL storage.

        Returns:
            StorageInfo with PostgreSQL details
        """
        _url = settings.database_url.get_secret_value()
        return StorageInfo(
            backend="postgresql",
            available=self.is_available(),
            read_only=False,  # PostgreSQL supports writes
            location=_url.split("@")[-1] if "@" in _url else _url,
            metadata={
                "session_active": self.db_session is not None,
                "service_initialized": self.unit_service is not None,
            },
        )

    def close(self):
        """Close database session."""
        if self.db_session is not None:
            try:
                self.db_session.close()
                self.logger.debug("PostgreSQL session closed")
            except Exception as e:
                self.logger.warning("Error closing PostgreSQL session", error=str(e))
            finally:
                self.db_session = None
                self.unit_service = None
