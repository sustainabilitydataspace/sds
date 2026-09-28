"""Tests for configuration manager module."""

import os
import tempfile
from datetime import datetime
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.ontology.configuration_manager import (
    ConfigurationManager,
    ConfigurationValidationError,
    HierarchyConfig,
    HierarchyLevel,
)


class TestConfigurationManager:
    """Test cases for ConfigurationManager class."""

    def setup_method(self):
        """Set up test fixtures."""
        # Use in-memory SQLite database for testing
        self.test_db_url = "sqlite:///:memory:"
        self.config_manager = ConfigurationManager(database_url=self.test_db_url)

        # Create test hierarchy configuration
        self.test_levels = [
            HierarchyLevel(id="global", name="Global", parent=None),
            HierarchyLevel(id="europe", name="Europe", parent="global"),
            HierarchyLevel(id="spain", name="Spain", parent="europe"),
            HierarchyLevel(id="madrid", name="Madrid Plant", parent="spain"),
        ]

        self.test_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="organizational",
            name="Test Hierarchy",
            description="Test organizational hierarchy",
            levels=self.test_levels,
        )

    def test_init(self):
        """Test ConfigurationManager initialization."""
        assert self.config_manager.database_url == self.test_db_url
        assert self.config_manager.engine is not None
        assert self.config_manager.SessionLocal is not None

    def test_create_hierarchy_configuration(self):
        """Test creating hierarchy configuration."""
        config_id = self.config_manager.create_hierarchy_configuration(
            self.test_config, created_by="test_user"
        )

        assert config_id is not None
        assert isinstance(config_id, str)
        assert "test_corp" in config_id
        assert "organizational" in config_id

    def test_get_hierarchy_configuration(self):
        """Test getting hierarchy configuration."""
        # Create configuration first
        config_id = self.config_manager.create_hierarchy_configuration(self.test_config)

        # Retrieve configuration
        retrieved_config = self.config_manager.get_hierarchy_configuration(config_id)

        assert retrieved_config is not None
        assert retrieved_config.company_id == self.test_config.company_id
        assert retrieved_config.hierarchy_type == self.test_config.hierarchy_type
        assert retrieved_config.name == self.test_config.name
        assert len(retrieved_config.levels) == len(self.test_config.levels)

        # Check levels
        for original, retrieved in zip(
            self.test_config.levels, retrieved_config.levels
        ):
            assert original.id == retrieved.id
            assert original.name == retrieved.name
            assert original.parent == retrieved.parent

    def test_get_nonexistent_configuration(self):
        """Test getting non-existent configuration."""
        result = self.config_manager.get_hierarchy_configuration("nonexistent_id")
        assert result is None

    def test_list_hierarchy_configurations(self):
        """Test listing hierarchy configurations."""
        # Create multiple configurations
        config1 = self.config_manager.create_hierarchy_configuration(self.test_config)

        config2_data = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="geographical",
            name="Geographical Hierarchy",
            description="Test geographical hierarchy",
            levels=[
                HierarchyLevel(id="world", name="World", parent=None),
                HierarchyLevel(id="europe", name="Europe", parent="world"),
            ],
        )
        config2 = self.config_manager.create_hierarchy_configuration(config2_data)

        # List all configurations
        all_configs = self.config_manager.list_hierarchy_configurations()
        assert len(all_configs) == 2

        # List by company
        company_configs = self.config_manager.list_hierarchy_configurations(
            company_id="test_corp"
        )
        assert len(company_configs) == 2

        # List by hierarchy type
        org_configs = self.config_manager.list_hierarchy_configurations(
            hierarchy_type="organizational"
        )
        assert len(org_configs) == 1
        assert org_configs[0]["hierarchy_type"] == "organizational"

    def test_update_hierarchy_configuration(self):
        """Test updating hierarchy configuration."""
        # Create configuration
        config_id = self.config_manager.create_hierarchy_configuration(self.test_config)

        # Update configuration
        updated_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="organizational",
            name="Updated Test Hierarchy",
            description="Updated description",
            levels=self.test_levels
            + [HierarchyLevel(id="barcelona", name="Barcelona Plant", parent="spain")],
        )

        result = self.config_manager.update_hierarchy_configuration(
            config_id, updated_config, updated_by="test_user"
        )

        assert result is True

        # Verify update
        retrieved_config = self.config_manager.get_hierarchy_configuration(config_id)
        assert retrieved_config.name == "Updated Test Hierarchy"
        assert retrieved_config.description == "Updated description"
        assert len(retrieved_config.levels) == 5

    def test_delete_hierarchy_configuration(self):
        """Test deleting hierarchy configuration."""
        # Create configuration
        config_id = self.config_manager.create_hierarchy_configuration(self.test_config)

        # Delete configuration
        result = self.config_manager.delete_hierarchy_configuration(config_id)
        assert result is True

        # Verify soft delete (should not appear in active list)
        active_configs = self.config_manager.list_hierarchy_configurations(
            active_only=True
        )
        assert len(active_configs) == 0

        # Should appear in inactive list
        all_configs = self.config_manager.list_hierarchy_configurations(
            active_only=False
        )
        assert len(all_configs) == 1
        assert all_configs[0]["is_active"] is False

    def test_validate_hierarchy_configuration_valid(self):
        """Test validation of valid hierarchy configuration."""
        # Should not raise exception
        self.config_manager._validate_hierarchy_configuration(self.test_config)

    def test_validate_hierarchy_configuration_missing_company_id(self):
        """Test validation with missing company ID."""
        invalid_config = HierarchyConfig(
            company_id="",
            hierarchy_type="organizational",
            name="Test",
            description="Test",
            levels=self.test_levels,
        )

        with pytest.raises(
            ConfigurationValidationError, match="Company ID is required"
        ):
            self.config_manager._validate_hierarchy_configuration(invalid_config)

    def test_validate_hierarchy_configuration_invalid_hierarchy_type(self):
        """Test validation with invalid hierarchy type."""
        invalid_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="invalid_type",
            name="Test",
            description="Test",
            levels=self.test_levels,
        )

        with pytest.raises(
            ConfigurationValidationError, match="Hierarchy type must be"
        ):
            self.config_manager._validate_hierarchy_configuration(invalid_config)

    def test_validate_hierarchy_configuration_missing_hierarchy_type(self):
        """Test validation with missing hierarchy type."""
        invalid_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="",
            name="Test",
            description="Test",
            levels=self.test_levels,
        )

        with pytest.raises(
            ConfigurationValidationError, match="Hierarchy type is required"
        ):
            self.config_manager._validate_hierarchy_configuration(invalid_config)

    def test_validate_hierarchy_configuration_missing_name(self):
        """Test validation with missing configuration name."""
        invalid_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="organizational",
            name="",
            description="Test",
            levels=self.test_levels,
        )

        with pytest.raises(
            ConfigurationValidationError, match="Configuration name is required"
        ):
            self.config_manager._validate_hierarchy_configuration(invalid_config)

    def test_validate_hierarchy_configuration_no_levels(self):
        """Test validation with no levels."""
        invalid_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="organizational",
            name="Test",
            description="Test",
            levels=[],
        )

        with pytest.raises(
            ConfigurationValidationError,
            match="At least one hierarchy level is required",
        ):
            self.config_manager._validate_hierarchy_configuration(invalid_config)

    def test_validate_hierarchy_configuration_duplicate_ids(self):
        """Test validation with duplicate level IDs."""
        invalid_levels = [
            HierarchyLevel(id="global", name="Global", parent=None),
            HierarchyLevel(id="global", name="Global Duplicate", parent=None),
        ]

        invalid_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="organizational",
            name="Test",
            description="Test",
            levels=invalid_levels,
        )

        with pytest.raises(ConfigurationValidationError, match="Duplicate level ID"):
            self.config_manager._validate_hierarchy_configuration(invalid_config)

    def test_validate_hierarchy_configuration_level_missing_name(self):
        """Test validation when a hierarchy level has no name."""
        invalid_levels = [
            HierarchyLevel(id="global", name="Global", parent=None),
            HierarchyLevel(id="child", name="", parent="global"),
        ]

        invalid_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="organizational",
            name="Test",
            description="Test",
            levels=invalid_levels,
        )

        with pytest.raises(
            ConfigurationValidationError, match="Level name is required"
        ):
            self.config_manager._validate_hierarchy_configuration(invalid_config)

    def test_validate_hierarchy_configuration_multiple_roots(self):
        """Test validation with multiple root levels."""
        invalid_levels = [
            HierarchyLevel(id="global1", name="Global 1", parent=None),
            HierarchyLevel(id="global2", name="Global 2", parent=None),
        ]

        invalid_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="organizational",
            name="Test",
            description="Test",
            levels=invalid_levels,
        )

        with pytest.raises(
            ConfigurationValidationError, match="exactly one root level"
        ):
            self.config_manager._validate_hierarchy_configuration(invalid_config)

    def test_validate_hierarchy_configuration_orphaned_levels(self):
        """Test validation with orphaned levels."""
        invalid_levels = [
            HierarchyLevel(id="global", name="Global", parent=None),
            HierarchyLevel(id="orphan", name="Orphan", parent="nonexistent"),
        ]

        invalid_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="organizational",
            name="Test",
            description="Test",
            levels=invalid_levels,
        )

        with pytest.raises(ConfigurationValidationError, match="Orphaned levels found"):
            self.config_manager._validate_hierarchy_configuration(invalid_config)

    def test_validate_hierarchy_configuration_no_root_and_cycle(self):
        """Test validation when no root exists (and a cycle is present)."""
        invalid_levels = [
            HierarchyLevel(id="a", name="A", parent="b"),
            HierarchyLevel(id="b", name="B", parent="a"),
        ]

        invalid_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="organizational",
            name="Test",
            description="Test",
            levels=invalid_levels,
        )

        with pytest.raises(
            ConfigurationValidationError, match="Circular dependencies found"
        ):
            self.config_manager._validate_hierarchy_configuration(invalid_config)

    def test_check_circular_dependencies(self):
        """Test circular dependency detection."""
        circular_levels = [
            HierarchyLevel(id="a", name="A", parent="c"),
            HierarchyLevel(id="b", name="B", parent="a"),
            HierarchyLevel(id="c", name="C", parent="b"),
        ]

        circular_deps = self.config_manager._check_circular_dependencies_in_config(
            circular_levels
        )
        assert len(circular_deps) > 0

    def test_get_hierarchy_configuration_returns_none_on_error(self, monkeypatch):
        """Test get_hierarchy_configuration returns None when an exception occurs."""
        monkeypatch.setattr(
            self.config_manager,
            "_get_session",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert self.config_manager.get_hierarchy_configuration("any") is None

    def test_list_hierarchy_configurations_returns_empty_on_error(self, monkeypatch):
        """Test list_hierarchy_configurations returns [] when an exception occurs."""
        monkeypatch.setattr(
            self.config_manager,
            "_get_session",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert self.config_manager.list_hierarchy_configurations() == []

    def test_create_hierarchy_configuration_raises_on_db_error(self, monkeypatch):
        """Test create raises ConfigurationValidationError on SQLAlchemy failure."""
        session = Mock()
        session.add = Mock()
        session.commit = Mock(side_effect=SQLAlchemyError("boom"))
        session.rollback = Mock()
        session.close = Mock()
        monkeypatch.setattr(self.config_manager, "_get_session", lambda: session)

        with pytest.raises(ConfigurationValidationError, match="Database error"):
            self.config_manager.create_hierarchy_configuration(self.test_config)

        session.rollback.assert_called_once()
        session.close.assert_called_once()

    def test_init_raises_when_create_tables_fails(self, monkeypatch):
        """Test __init__ raises when table creation fails."""
        import src.ontology.configuration_manager as cm

        monkeypatch.setattr(
            cm.Base.metadata,
            "create_all",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        with pytest.raises(RuntimeError, match="fail"):
            cm.ConfigurationManager(database_url=self.test_db_url)

    def test_get_active_configuration_for_company(self):
        """Test getting active configuration for company."""
        # Create configuration
        config_id = self.config_manager.create_hierarchy_configuration(self.test_config)

        # Get active configuration
        active_config = self.config_manager.get_active_configuration_for_company(
            "test_corp", "organizational"
        )

        assert active_config is not None
        assert active_config.company_id == "test_corp"
        assert active_config.hierarchy_type == "organizational"

    def test_get_active_configuration_for_nonexistent_company(self):
        """Test getting active configuration for non-existent company."""
        result = self.config_manager.get_active_configuration_for_company(
            "nonexistent_corp", "organizational"
        )
        assert result is None

    def test_get_active_configuration_returns_none_on_error(self, monkeypatch):
        """Test get_active_configuration_for_company returns None on exception."""
        monkeypatch.setattr(
            self.config_manager,
            "_get_session",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert (
            self.config_manager.get_active_configuration_for_company(
                "test_corp", "organizational"
            )
            is None
        )

    def test_update_hierarchy_configuration_returns_false_when_missing(
        self, monkeypatch
    ):
        """Test update returns False when configuration does not exist."""
        session = Mock()
        session.query.return_value.filter.return_value.first.return_value = None
        session.close = Mock()
        monkeypatch.setattr(self.config_manager, "_get_session", lambda: session)

        assert (
            self.config_manager.update_hierarchy_configuration(
                "missing", self.test_config
            )
            is False
        )

    def test_update_hierarchy_configuration_returns_false_on_validation_error(self):
        """Test update returns False when validation fails."""
        invalid_config = HierarchyConfig(
            company_id="",
            hierarchy_type="organizational",
            name="Test",
            description="Test",
            levels=self.test_levels,
        )

        assert (
            self.config_manager.update_hierarchy_configuration("any", invalid_config)
            is False
        )

    def test_update_hierarchy_configuration_returns_false_on_db_error(
        self, monkeypatch
    ):
        """Test update returns False on SQLAlchemy error."""
        db_config = Mock()
        session = Mock()
        session.query.return_value.filter.return_value.first.return_value = db_config
        session.commit = Mock(side_effect=SQLAlchemyError("boom"))
        session.rollback = Mock()
        session.close = Mock()
        monkeypatch.setattr(self.config_manager, "_get_session", lambda: session)

        assert (
            self.config_manager.update_hierarchy_configuration("any", self.test_config)
            is False
        )
        session.rollback.assert_called_once()

    def test_delete_hierarchy_configuration_returns_false_when_missing(
        self, monkeypatch
    ):
        """Test delete returns False when configuration does not exist."""
        session = Mock()
        session.query.return_value.filter.return_value.first.return_value = None
        session.close = Mock()
        monkeypatch.setattr(self.config_manager, "_get_session", lambda: session)

        assert self.config_manager.delete_hierarchy_configuration("missing") is False

    def test_delete_hierarchy_configuration_returns_false_on_db_error(
        self, monkeypatch
    ):
        """Test delete returns False on SQLAlchemy error."""
        db_config = Mock()
        session = Mock()
        session.query.return_value.filter.return_value.first.return_value = db_config
        session.commit = Mock(side_effect=SQLAlchemyError("boom"))
        session.rollback = Mock()
        session.close = Mock()
        monkeypatch.setattr(self.config_manager, "_get_session", lambda: session)

        assert self.config_manager.delete_hierarchy_configuration("any") is False
        session.rollback.assert_called_once()

    def test_delete_hierarchy_configuration_returns_false_on_error(self, monkeypatch):
        """Test delete returns False when an exception occurs before querying."""
        monkeypatch.setattr(
            self.config_manager,
            "_get_session",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert self.config_manager.delete_hierarchy_configuration("any") is False

    def test_validate_configuration_compatibility(self):
        """Test configuration compatibility validation."""
        # Create original configuration
        config_id = self.config_manager.create_hierarchy_configuration(self.test_config)

        # Create compatible new configuration (only adds levels)
        compatible_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="organizational",
            name="Compatible Config",
            description="Compatible configuration",
            levels=self.test_levels
            + [HierarchyLevel(id="barcelona", name="Barcelona Plant", parent="spain")],
        )

        compatibility = self.config_manager.validate_configuration_compatibility(
            config_id, compatible_config
        )

        assert compatibility["compatible"] is True
        assert "barcelona" in compatibility["added_levels"]
        assert len(compatibility["removed_levels"]) == 0
        assert len(compatibility["structural_changes"]) == 0

    def test_validate_configuration_compatibility_incompatible(self):
        """Test configuration compatibility with incompatible changes."""
        # Create original configuration
        config_id = self.config_manager.create_hierarchy_configuration(self.test_config)

        # Create incompatible configuration (removes levels)
        incompatible_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="organizational",
            name="Incompatible Config",
            description="Incompatible configuration",
            levels=self.test_levels[:-1],  # Remove last level
        )

        compatibility = self.config_manager.validate_configuration_compatibility(
            config_id, incompatible_config
        )

        assert compatibility["compatible"] is False
        assert "madrid" in compatibility["removed_levels"]

    def test_validate_configuration_compatibility_old_config_missing(self):
        """Test compatibility returns error when old config is missing."""
        compatibility = self.config_manager.validate_configuration_compatibility(
            "missing",
            self.test_config,
        )

        assert compatibility["compatible"] is False
        assert compatibility["error"] == "Old configuration not found"

    def test_validate_configuration_compatibility_detects_parent_change(self):
        """Test compatibility detects structural changes in parents."""
        config_id = self.config_manager.create_hierarchy_configuration(self.test_config)

        structurally_changed = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="organizational",
            name="Changed Parent",
            description="Parent change",
            levels=[
                HierarchyLevel(id="global", name="Global", parent=None),
                HierarchyLevel(id="europe", name="Europe", parent="global"),
                HierarchyLevel(id="spain", name="Spain", parent="europe"),
                # change madrid parent from spain -> europe
                HierarchyLevel(id="madrid", name="Madrid Plant", parent="europe"),
            ],
        )

        compatibility = self.config_manager.validate_configuration_compatibility(
            config_id,
            structurally_changed,
        )

        assert compatibility["compatible"] is False
        assert compatibility["structural_changes"]
        assert "Structural changes may affect aggregations" in compatibility["warnings"]

    def test_validate_configuration_compatibility_returns_error_on_exception(self):
        """Test compatibility returns error dict on exception (e.g., new config invalid)."""
        config_id = self.config_manager.create_hierarchy_configuration(self.test_config)

        invalid_new_config = HierarchyConfig(
            company_id="test_corp",
            hierarchy_type="",
            name="Invalid",
            description="Invalid",
            levels=self.test_levels,
        )

        compatibility = self.config_manager.validate_configuration_compatibility(
            config_id,
            invalid_new_config,
        )

        assert compatibility["compatible"] is False
        assert "Configuration validation failed" in compatibility["error"]

    def test_export_configuration(self):
        """Test configuration export."""
        # Create configuration
        config_id = self.config_manager.create_hierarchy_configuration(
            self.test_config, created_by="test_user"
        )

        # Export configuration
        exported_data = self.config_manager.export_configuration(config_id)

        assert exported_data is not None
        assert exported_data["id"] == config_id
        assert exported_data["company_id"] == "test_corp"
        assert exported_data["hierarchy_type"] == "organizational"
        assert "configuration" in exported_data
        assert "export_timestamp" in exported_data

    def test_export_configuration_returns_none_when_missing(self):
        """Test export returns None when configuration does not exist."""
        assert self.config_manager.export_configuration("missing") is None

    def test_export_configuration_returns_none_on_error(self, monkeypatch):
        """Test export returns None when an exception occurs."""
        monkeypatch.setattr(
            self.config_manager,
            "_get_session",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert self.config_manager.export_configuration("any") is None

    def test_import_configuration(self):
        """Test configuration import."""
        # Create and export configuration
        original_config_id = self.config_manager.create_hierarchy_configuration(
            self.test_config, created_by="test_user"
        )
        exported_data = self.config_manager.export_configuration(original_config_id)

        # Import configuration
        imported_config_id = self.config_manager.import_configuration(
            exported_data, imported_by="import_user"
        )

        assert imported_config_id is not None
        assert imported_config_id != original_config_id  # Should be different ID

        # Verify imported configuration
        imported_config = self.config_manager.get_hierarchy_configuration(
            imported_config_id
        )
        assert imported_config.company_id == self.test_config.company_id
        assert imported_config.hierarchy_type == self.test_config.hierarchy_type
        assert len(imported_config.levels) == len(self.test_config.levels)

    def test_import_configuration_returns_none_on_error(self):
        """Test import returns None when data is invalid."""
        assert self.config_manager.import_configuration({}) is None

    def test_generate_config_id(self):
        """Test configuration ID generation."""
        config_id = self.config_manager._generate_config_id(
            "test_corp", "organizational", "Test Config"
        )

        assert "test_corp" in config_id
        assert "organizational" in config_id
        assert "test_config" in config_id
        assert len(config_id) > 20  # Should include timestamp
