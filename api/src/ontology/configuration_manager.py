"""Configuration management for organizational and temporal hierarchies."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, declarative_base, sessionmaker

import structlog
from src.config.settings import settings

logger = structlog.get_logger(__name__)


def _utcnow_naive() -> datetime:
    """Return current UTC time as a naive datetime for DB storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


Base = declarative_base()


class HierarchyConfiguration(Base):
    """Database model for hierarchy configurations."""

    __tablename__ = "hierarchy_configurations"

    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False, index=True)
    hierarchy_type = Column(
        String, nullable=False
    )  # 'organizational' or 'geographical'
    name = Column(String, nullable=False)
    description = Column(Text)
    configuration = Column(Text, nullable=False)  # JSON string
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow_naive)
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)
    created_by = Column(String)


@dataclass
class HierarchyLevel:
    """Represents a level in a hierarchy."""

    id: str
    name: str
    parent: Optional[str] = None
    level: int = 0
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class HierarchyConfig:
    """Complete hierarchy configuration."""

    company_id: str
    hierarchy_type: str
    name: str
    description: str
    levels: List[HierarchyLevel]
    metadata: Optional[Dict[str, Any]] = None


class ConfigurationValidationError(Exception):
    """Exception raised for configuration validation errors."""

    pass


class ConfigurationManager:
    """Manages organizational and temporal hierarchy configurations."""

    def __init__(self, database_url: Optional[str] = None):
        """Initialize configuration manager.

        Args:
            database_url: Database connection URL. If None, uses settings.
        """
        self.database_url = database_url or settings.database_url.get_secret_value()
        self.engine = create_engine(self.database_url)
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        self.logger = logger.bind(component="ConfigurationManager")

        # Create tables if they don't exist
        self._create_tables()

    def _create_tables(self):
        """Create database tables if they don't exist."""
        try:
            Base.metadata.create_all(bind=self.engine)
            self.logger.info("Database tables created/verified")
        except Exception as e:
            self.logger.error("Failed to create database tables", error=str(e))
            raise

    def _get_session(self) -> Session:
        """Get database session."""
        return self.SessionLocal()

    def create_hierarchy_configuration(
        self, config: HierarchyConfig, created_by: Optional[str] = None
    ) -> str:
        """Create new hierarchy configuration.

        Args:
            config: Hierarchy configuration
            created_by: User who created the configuration

        Returns:
            Configuration ID

        Raises:
            ConfigurationValidationError: If configuration is invalid
        """
        try:
            self.logger.info(
                "Creating hierarchy configuration",
                company_id=config.company_id,
                hierarchy_type=config.hierarchy_type,
                name=config.name,
            )

            # Validate configuration
            self._validate_hierarchy_configuration(config)

            # Generate configuration ID
            config_id = self._generate_config_id(
                config.company_id, config.hierarchy_type, config.name
            )

            # Convert to database model
            db_config = HierarchyConfiguration(
                id=config_id,
                company_id=config.company_id,
                hierarchy_type=config.hierarchy_type,
                name=config.name,
                description=config.description,
                configuration=json.dumps(asdict(config)),
                created_by=created_by,
            )

            # Save to database
            session = self._get_session()
            try:
                session.add(db_config)
                session.commit()

                self.logger.info(
                    "Hierarchy configuration created successfully",
                    config_id=config_id,
                    company_id=config.company_id,
                )

                return config_id

            except SQLAlchemyError as e:
                session.rollback()
                self.logger.error(
                    "Failed to save configuration to database", error=str(e)
                )
                raise ConfigurationValidationError(f"Database error: {e}")
            finally:
                session.close()

        except Exception as e:
            self.logger.error(
                "Failed to create hierarchy configuration",
                error=str(e),
                company_id=config.company_id,
            )
            raise

    def _generate_config_id(
        self, company_id: str, hierarchy_type: str, name: str
    ) -> str:
        """Generate unique configuration ID."""
        timestamp = _utcnow_naive().strftime("%Y%m%d_%H%M%S")
        return f"{company_id}_{hierarchy_type}_{name}_{timestamp}".replace(
            " ", "_"
        ).lower()

    def _validate_hierarchy_configuration(self, config: HierarchyConfig):
        """Validate hierarchy configuration.

        Args:
            config: Configuration to validate

        Raises:
            ConfigurationValidationError: If configuration is invalid
        """
        errors = []

        # Basic validation
        if not config.company_id:
            errors.append("Company ID is required")

        if not config.hierarchy_type:
            errors.append("Hierarchy type is required")
        elif config.hierarchy_type not in ["organizational", "geographical"]:
            errors.append("Hierarchy type must be 'organizational' or 'geographical'")

        if not config.name:
            errors.append("Configuration name is required")

        if not config.levels:
            errors.append("At least one hierarchy level is required")

        # Validate levels
        if config.levels:
            level_ids = set()
            root_levels = []

            for level in config.levels:
                # Check for duplicate IDs
                if level.id in level_ids:
                    errors.append(f"Duplicate level ID: {level.id}")
                level_ids.add(level.id)

                # Check for empty names
                if not level.name:
                    errors.append(f"Level name is required for level {level.id}")

                # Track root levels
                if not level.parent:
                    root_levels.append(level.id)

            # Check for exactly one root level
            if len(root_levels) == 0:
                errors.append("Hierarchy must have exactly one root level (no parent)")
            elif len(root_levels) > 1:
                errors.append(
                    f"Hierarchy must have exactly one root level, found: {root_levels}"
                )

            # Check for circular dependencies
            circular_deps = self._check_circular_dependencies_in_config(config.levels)
            if circular_deps:
                errors.append(f"Circular dependencies found: {circular_deps}")

            # Check for orphaned levels
            orphaned_levels = self._find_orphaned_levels_in_config(config.levels)
            if orphaned_levels:
                errors.append(
                    f"Orphaned levels found (parent doesn't exist): {orphaned_levels}"
                )

        if errors:
            raise ConfigurationValidationError(
                f"Configuration validation failed: {'; '.join(errors)}"
            )

    def _check_circular_dependencies_in_config(
        self, levels: List[HierarchyLevel]
    ) -> List[str]:
        """Check for circular dependencies in hierarchy levels."""
        level_map = {level.id: level.parent for level in levels}
        visited = set()
        circular_deps = []

        for level_id in level_map:
            if level_id in visited:
                continue

            path = []
            current = level_id

            while current and current not in visited:
                if current in path:
                    # Found a cycle
                    cycle_start = path.index(current)
                    circular_deps.append(" -> ".join(path[cycle_start:] + [current]))
                    break

                path.append(current)
                current = level_map.get(current)

            visited.update(path)

        return circular_deps

    def _find_orphaned_levels_in_config(
        self, levels: List[HierarchyLevel]
    ) -> List[str]:
        """Find levels that reference non-existent parents."""
        level_ids = {level.id for level in levels}
        orphaned = []

        for level in levels:
            if level.parent and level.parent not in level_ids:
                orphaned.append(level.id)

        return orphaned

    def get_hierarchy_configuration(self, config_id: str) -> Optional[HierarchyConfig]:
        """Get hierarchy configuration by ID.

        Args:
            config_id: Configuration ID

        Returns:
            HierarchyConfig if found, None otherwise
        """
        try:
            session = self._get_session()
            try:
                db_config = (
                    session.query(HierarchyConfiguration)
                    .filter(HierarchyConfiguration.id == config_id)
                    .first()
                )

                if not db_config:
                    return None

                # Parse configuration JSON
                config_data = json.loads(db_config.configuration)

                # Convert levels back to HierarchyLevel objects
                levels = [
                    HierarchyLevel(**level_data) for level_data in config_data["levels"]
                ]

                return HierarchyConfig(
                    company_id=config_data["company_id"],
                    hierarchy_type=config_data["hierarchy_type"],
                    name=config_data["name"],
                    description=config_data["description"],
                    levels=levels,
                    metadata=config_data.get("metadata"),
                )

            finally:
                session.close()

        except Exception as e:
            self.logger.error(
                "Failed to get hierarchy configuration",
                error=str(e),
                config_id=config_id,
            )
            return None

    def list_hierarchy_configurations(
        self,
        company_id: Optional[str] = None,
        hierarchy_type: Optional[str] = None,
        active_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """List hierarchy configurations.

        Args:
            company_id: Filter by company ID
            hierarchy_type: Filter by hierarchy type
            active_only: Only return active configurations

        Returns:
            List of configuration summaries
        """
        try:
            session = self._get_session()
            try:
                query = session.query(HierarchyConfiguration)

                if company_id:
                    query = query.filter(
                        HierarchyConfiguration.company_id == company_id
                    )

                if hierarchy_type:
                    query = query.filter(
                        HierarchyConfiguration.hierarchy_type == hierarchy_type
                    )

                if active_only:
                    query = query.filter(HierarchyConfiguration.is_active == True)

                configs = query.order_by(HierarchyConfiguration.created_at.desc()).all()

                return [
                    {
                        "id": config.id,
                        "company_id": config.company_id,
                        "hierarchy_type": config.hierarchy_type,
                        "name": config.name,
                        "description": config.description,
                        "is_active": config.is_active,
                        "created_at": config.created_at,
                        "updated_at": config.updated_at,
                        "created_by": config.created_by,
                    }
                    for config in configs
                ]

            finally:
                session.close()

        except Exception as e:
            self.logger.error(
                "Failed to list hierarchy configurations",
                error=str(e),
                company_id=company_id,
            )
            return []

    def update_hierarchy_configuration(
        self, config_id: str, config: HierarchyConfig, updated_by: Optional[str] = None
    ) -> bool:
        """Update existing hierarchy configuration.

        Args:
            config_id: Configuration ID to update
            config: New configuration data
            updated_by: User who updated the configuration

        Returns:
            True if successful, False otherwise
        """
        try:
            self.logger.info(
                "Updating hierarchy configuration",
                config_id=config_id,
                company_id=config.company_id,
            )

            # Validate new configuration
            self._validate_hierarchy_configuration(config)

            session = self._get_session()
            try:
                db_config = (
                    session.query(HierarchyConfiguration)
                    .filter(HierarchyConfiguration.id == config_id)
                    .first()
                )

                if not db_config:
                    self.logger.warning(
                        "Configuration not found for update", config_id=config_id
                    )
                    return False

                # Update configuration
                db_config.name = config.name
                db_config.description = config.description
                db_config.configuration = json.dumps(asdict(config))
                db_config.updated_at = _utcnow_naive()

                session.commit()

                self.logger.info(
                    "Hierarchy configuration updated successfully", config_id=config_id
                )

                return True

            except SQLAlchemyError as e:
                session.rollback()
                self.logger.error(
                    "Failed to update configuration in database", error=str(e)
                )
                return False
            finally:
                session.close()

        except Exception as e:
            self.logger.error(
                "Failed to update hierarchy configuration",
                error=str(e),
                config_id=config_id,
            )
            return False

    def delete_hierarchy_configuration(self, config_id: str) -> bool:
        """Delete hierarchy configuration (soft delete by marking inactive).

        Args:
            config_id: Configuration ID to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            self.logger.info("Deleting hierarchy configuration", config_id=config_id)

            session = self._get_session()
            try:
                db_config = (
                    session.query(HierarchyConfiguration)
                    .filter(HierarchyConfiguration.id == config_id)
                    .first()
                )

                if not db_config:
                    self.logger.warning(
                        "Configuration not found for deletion", config_id=config_id
                    )
                    return False

                # Soft delete by marking inactive
                db_config.is_active = False
                db_config.updated_at = _utcnow_naive()

                session.commit()

                self.logger.info(
                    "Hierarchy configuration deleted successfully", config_id=config_id
                )
                return True

            except SQLAlchemyError as e:
                session.rollback()
                self.logger.error(
                    "Failed to delete configuration from database", error=str(e)
                )
                return False
            finally:
                session.close()

        except Exception as e:
            self.logger.error(
                "Failed to delete hierarchy configuration",
                error=str(e),
                config_id=config_id,
            )
            return False

    def get_active_configuration_for_company(
        self, company_id: str, hierarchy_type: str
    ) -> Optional[HierarchyConfig]:
        """Get active configuration for a company and hierarchy type.

        Args:
            company_id: Company ID
            hierarchy_type: Type of hierarchy

        Returns:
            Active HierarchyConfig if found, None otherwise
        """
        try:
            session = self._get_session()
            try:
                db_config = (
                    session.query(HierarchyConfiguration)
                    .filter(
                        HierarchyConfiguration.company_id == company_id,
                        HierarchyConfiguration.hierarchy_type == hierarchy_type,
                        HierarchyConfiguration.is_active == True,
                    )
                    .order_by(HierarchyConfiguration.created_at.desc())
                    .first()
                )

                if not db_config:
                    return None

                return self.get_hierarchy_configuration(db_config.id)

            finally:
                session.close()

        except Exception as e:
            self.logger.error(
                "Failed to get active configuration for company",
                error=str(e),
                company_id=company_id,
                hierarchy_type=hierarchy_type,
            )
            return None

    def validate_configuration_compatibility(
        self, old_config_id: str, new_config: HierarchyConfig
    ) -> Dict[str, Any]:
        """Validate compatibility between old and new configurations.

        Args:
            old_config_id: ID of existing configuration
            new_config: New configuration to validate

        Returns:
            Compatibility analysis results
        """
        try:
            old_config = self.get_hierarchy_configuration(old_config_id)
            if not old_config:
                return {"compatible": False, "error": "Old configuration not found"}

            # Validate new configuration
            self._validate_hierarchy_configuration(new_config)

            # Compare configurations
            old_level_ids = {level.id for level in old_config.levels}
            new_level_ids = {level.id for level in new_config.levels}

            added_levels = new_level_ids - old_level_ids
            removed_levels = old_level_ids - new_level_ids
            common_levels = old_level_ids & new_level_ids

            # Check for structural changes in common levels
            structural_changes = []
            for level_id in common_levels:
                old_level = next(l for l in old_config.levels if l.id == level_id)
                new_level = next(l for l in new_config.levels if l.id == level_id)

                if old_level.parent != new_level.parent:
                    structural_changes.append(
                        f"Level {level_id} parent changed from {old_level.parent} to {new_level.parent}"
                    )

            compatibility = {
                "compatible": len(removed_levels) == 0 and len(structural_changes) == 0,
                "added_levels": list(added_levels),
                "removed_levels": list(removed_levels),
                "structural_changes": structural_changes,
                "warnings": [],
            }

            if removed_levels:
                compatibility["warnings"].append(
                    f"Removing levels may break existing data: {removed_levels}"
                )

            if structural_changes:
                compatibility["warnings"].append(
                    "Structural changes may affect aggregations"
                )

            return compatibility

        except Exception as e:
            self.logger.error(
                "Failed to validate configuration compatibility",
                error=str(e),
                old_config_id=old_config_id,
            )
            return {"compatible": False, "error": str(e)}

    def export_configuration(self, config_id: str) -> Optional[Dict[str, Any]]:
        """Export configuration for backup or migration.

        Args:
            config_id: Configuration ID to export

        Returns:
            Configuration data as dictionary
        """
        try:
            session = self._get_session()
            try:
                db_config = (
                    session.query(HierarchyConfiguration)
                    .filter(HierarchyConfiguration.id == config_id)
                    .first()
                )

                if not db_config:
                    return None

                return {
                    "id": db_config.id,
                    "company_id": db_config.company_id,
                    "hierarchy_type": db_config.hierarchy_type,
                    "name": db_config.name,
                    "description": db_config.description,
                    "configuration": json.loads(db_config.configuration),
                    "is_active": db_config.is_active,
                    "created_at": db_config.created_at.isoformat(),
                    "updated_at": db_config.updated_at.isoformat(),
                    "created_by": db_config.created_by,
                    "export_timestamp": datetime.now(timezone.utc).isoformat(),
                }

            finally:
                session.close()

        except Exception as e:
            self.logger.error(
                "Failed to export configuration", error=str(e), config_id=config_id
            )
            return None

    def import_configuration(
        self, config_data: Dict[str, Any], imported_by: Optional[str] = None
    ) -> Optional[str]:
        """Import configuration from backup or migration.

        Args:
            config_data: Configuration data to import
            imported_by: User who imported the configuration

        Returns:
            New configuration ID if successful, None otherwise
        """
        try:
            # Parse configuration
            config_json = config_data["configuration"]
            levels = [
                HierarchyLevel(**level_data) for level_data in config_json["levels"]
            ]

            # Create config with modified name to avoid ID conflicts
            config = HierarchyConfig(
                company_id=config_json["company_id"],
                hierarchy_type=config_json["hierarchy_type"],
                name=f"{config_json['name']} (Imported)",
                description=config_json["description"],
                levels=levels,
                metadata=config_json.get("metadata"),
            )

            # Create new configuration (will generate new ID)
            return self.create_hierarchy_configuration(config, imported_by)

        except Exception as e:
            self.logger.error("Failed to import configuration", error=str(e))
            return None
