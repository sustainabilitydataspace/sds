"""Repository for hierarchy configuration database operations."""

from typing import Any, Dict, List, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..models import HierarchyConfiguration


class HierarchyRepository:
    """Repository for hierarchy configuration operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_hierarchy_configurations(
        self,
        company_id: str = None,
        hierarchy_type: str = None,
        active_only: bool = True,
    ) -> List[HierarchyConfiguration]:
        """Get hierarchy configurations with optional filters."""
        query = self.db.query(HierarchyConfiguration)

        if company_id:
            query = query.filter(HierarchyConfiguration.company_id == company_id)

        if hierarchy_type:
            query = query.filter(
                HierarchyConfiguration.hierarchy_type == hierarchy_type
            )

        if active_only:
            query = query.filter(HierarchyConfiguration.is_active == True)

        return query.order_by(HierarchyConfiguration.created_at.desc()).all()

    def get_hierarchy_configuration_by_id(
        self, config_id: str
    ) -> Optional[HierarchyConfiguration]:
        """Get hierarchy configuration by ID."""
        return (
            self.db.query(HierarchyConfiguration)
            .filter(HierarchyConfiguration.id == config_id)
            .first()
        )

    def create_hierarchy_configuration(
        self,
        config_id: str,
        company_id: str,
        hierarchy_type: str,
        name: str,
        configuration: str,
        description: str = None,
        created_by: str = None,
    ) -> HierarchyConfiguration:
        """Create a new hierarchy configuration."""
        config = HierarchyConfiguration(
            id=config_id,
            company_id=company_id,
            hierarchy_type=hierarchy_type,
            name=name,
            description=description,
            configuration=configuration,
            created_by=created_by,
        )
        self.db.add(config)
        self.db.commit()
        self.db.refresh(config)
        return config

    def update_hierarchy_configuration(
        self, config_id: str, **kwargs
    ) -> Optional[HierarchyConfiguration]:
        """Update a hierarchy configuration."""
        config = (
            self.db.query(HierarchyConfiguration)
            .filter(HierarchyConfiguration.id == config_id)
            .first()
        )

        if config:
            for key, value in kwargs.items():
                if hasattr(config, key):
                    setattr(config, key, value)
            self.db.commit()
            self.db.refresh(config)

        return config

    def delete_hierarchy_configuration(self, config_id: str) -> bool:
        """Soft delete a hierarchy configuration."""
        config = (
            self.db.query(HierarchyConfiguration)
            .filter(HierarchyConfiguration.id == config_id)
            .first()
        )

        if config:
            config.is_active = False
            self.db.commit()
            return True

        return False

    def get_active_hierarchy_configuration(
        self, company_id: str, hierarchy_type: str
    ) -> Optional[HierarchyConfiguration]:
        """Get the active hierarchy configuration for a company and type."""
        return (
            self.db.query(HierarchyConfiguration)
            .filter(
                and_(
                    HierarchyConfiguration.company_id == company_id,
                    HierarchyConfiguration.hierarchy_type == hierarchy_type,
                    HierarchyConfiguration.is_active == True,
                )
            )
            .order_by(HierarchyConfiguration.created_at.desc())
            .first()
        )

    def activate_hierarchy_configuration(
        self, config_id: str, company_id: str, hierarchy_type: str
    ) -> bool:
        """Activate a hierarchy configuration and deactivate others of the same type."""
        # First, deactivate all other configurations of the same type for the company
        self.db.query(HierarchyConfiguration).filter(
            and_(
                HierarchyConfiguration.company_id == company_id,
                HierarchyConfiguration.hierarchy_type == hierarchy_type,
                HierarchyConfiguration.id != config_id,
            )
        ).update({"is_active": False})

        # Then activate the specified configuration
        config = (
            self.db.query(HierarchyConfiguration)
            .filter(HierarchyConfiguration.id == config_id)
            .first()
        )

        if config:
            config.is_active = True
            self.db.commit()
            return True

        return False
