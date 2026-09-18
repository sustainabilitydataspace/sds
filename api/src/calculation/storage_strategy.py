"""
Storage strategy interface for unit data.

This module defines the abstract interface for unit storage backends,
allowing the system to use different storage mechanisms (PostgreSQL, JSON, etc.)
with automatic fallback support.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class StorageInfo:
    """Information about the storage backend."""

    backend: str  # 'postgresql' or 'json'
    available: bool
    read_only: bool
    location: str  # Database URL or file path
    metadata: Dict[str, Any]


class StorageStrategy(ABC):
    """Abstract base class for unit storage strategies."""

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if storage backend is available.

        Returns:
            True if backend is accessible, False otherwise
        """
        pass

    @abstractmethod
    def load_units(self) -> List[Dict[str, Any]]:
        """
        Load all unit definitions from storage.

        Returns:
            List of unit definition dictionaries
        """
        pass

    @abstractmethod
    def load_categories(self) -> List[Dict[str, Any]]:
        """
        Load all unit categories from storage.

        Returns:
            List of category dictionaries
        """
        pass

    @abstractmethod
    def load_conversion_rules(self) -> List[Dict[str, Any]]:
        """
        Load all conversion rules from storage.

        Returns:
            List of conversion rule dictionaries
        """
        pass

    @abstractmethod
    def get_storage_info(self) -> StorageInfo:
        """
        Get information about the storage backend.

        Returns:
            StorageInfo object with backend details
        """
        pass

    def __repr__(self) -> str:
        """String representation of strategy."""
        info = self.get_storage_info()
        return f"<{self.__class__.__name__} backend={info.backend} available={info.available}>"
