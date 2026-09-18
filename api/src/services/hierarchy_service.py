"""Hierarchy service for managing organizational hierarchies."""

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import structlog
from src.database.repositories.hierarchy_repository import HierarchyRepository

logger = structlog.get_logger(__name__)


class HierarchyService:
    """Service for hierarchy management."""

    def __init__(self, db: Session):
        """Initialize hierarchy service.

        Args:
            db: Database session
        """
        self.db = db
        self.repository = HierarchyRepository(db)
        self.logger = logger.bind(component="HierarchyService")

    def get_hierarchy_configurations(
        self, company_id: str = None, hierarchy_type: str = None
    ) -> List[Dict[str, Any]]:
        """Get hierarchy configurations."""
        configs = self.repository.get_hierarchy_configurations(
            company_id=company_id, hierarchy_type=hierarchy_type
        )

        return [
            {
                "id": config.id,
                "company_id": config.company_id,
                "hierarchy_type": config.hierarchy_type,
                "name": config.name,
                "description": config.description,
                "is_active": config.is_active,
                "created_at": (
                    config.created_at.isoformat() if config.created_at else None
                ),
            }
            for config in configs
        ]
