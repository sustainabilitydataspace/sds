"""Tests for the canonical Sygris mapping knowledge-base foundation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src.database import models as db_models
from src.services.canonical_mapping_package import (
    CanonicalMappingPackageError,
    parse_canonical_mapping_manifest,
)


def _load_migration_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "015_create_canonical_mapping_tables.py"
    )
    spec = importlib.util.spec_from_file_location(
        "create_canonical_mapping_tables", module_path
    )
    assert spec and spec.loader, "Migration module spec not found"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_mapping_model_tables_and_columns():
    expected = {
        db_models.StandardRelease: {
            "standard_id",
            "name",
            "version",
            "release_date",
            "lifecycle_status",
            "provenance",
        },
        db_models.StandardDatapoint: {
            "standard_release_id",
            "code",
            "label",
            "disclosure_text",
            "datapoint_type",
            "lifecycle_status",
            "metadata_json",
        },
        db_models.MappingAssertionGroup: {
            "source_datapoint_id",
            "mapping_profile",
            "relationship_type",
            "coverage_status",
            "confidence",
            "coverage_summary",
            "difference_summary",
            "valid_from",
            "valid_to",
            "approval_status",
            "publication_status",
            "assertion_hash",
            "package_snapshot_id",
        },
        db_models.MappingAssertionComponent: {
            "assertion_group_id",
            "canonical_concept_id",
            "component_order",
            "component_role",
            "coverage_fraction",
            "match_scope",
            "mismatch_scope",
        },
        db_models.MaterializedPairwiseMapping: {
            "source_datapoint_id",
            "target_datapoint_id",
            "source_assertion_group_id",
            "target_assertion_group_id",
            "source_standard",
            "source_code",
            "target_standard",
            "target_code",
            "derivation_method",
            "generated_from_hash",
            "is_current",
        },
    }

    for model, expected_columns in expected.items():
        actual_columns = set(model.__table__.columns.keys())
        assert expected_columns.issubset(actual_columns)


def test_canonical_mapping_models_keep_legacy_standard_mappings_separate():
    assert db_models.StandardMapping.__tablename__ == "standard_mappings"
    assert db_models.MaterializedPairwiseMapping.__tablename__ == (
        "materialized_pairwise_mappings"
    )
    assert "source_standard" in db_models.StandardMapping.__table__.columns
    assert "source_assertion_group_id" in (
        db_models.MaterializedPairwiseMapping.__table__.columns
    )


def test_canonical_mapping_migration_chains_after_canonical_concepts():
    module = _load_migration_module()

    assert module.revision == "015_create_canonical_mapping_tables"
    assert module.down_revision == "014_create_canonical_concept_tables"


def test_parse_canonical_mapping_manifest_accepts_current_contract():
    manifest = parse_canonical_mapping_manifest(
        {
            "package_schema_version": "1.0",
            "files": [
                {"name": "sds_standard_releases.csv"},
                {"name": "sds_standard_datapoints.csv"},
                {"name": "sds_mapping_assertion_groups.csv"},
                {"name": "sds_mapping_assertion_components.csv"},
            ],
        }
    )

    assert manifest.package_schema_version == "1.0"
    assert "sds_standard_datapoints.csv" in manifest.files


def test_parse_canonical_mapping_manifest_accepts_path_only_entries():
    manifest = parse_canonical_mapping_manifest(
        {
            "package_schema_version": "1.0",
            "files": [
                {"path": "mappings/sds_standard_releases.csv"},
                {"path": "mappings/sds_standard_datapoints.csv"},
                {"path": "mappings/sds_mapping_assertion_groups.csv"},
                {"path": "mappings/sds_mapping_assertion_components.csv"},
            ],
        }
    )

    assert manifest.files == (
        "sds_standard_releases.csv",
        "sds_standard_datapoints.csv",
        "sds_mapping_assertion_groups.csv",
        "sds_mapping_assertion_components.csv",
    )


def test_parse_canonical_mapping_manifest_rejects_future_contract():
    with pytest.raises(CanonicalMappingPackageError, match="newer than supported"):
        parse_canonical_mapping_manifest(
            {
                "package_schema_version": "2.0",
                "files": [
                    {"name": "sds_standard_releases.csv"},
                    {"name": "sds_standard_datapoints.csv"},
                    {"name": "sds_mapping_assertion_groups.csv"},
                    {"name": "sds_mapping_assertion_components.csv"},
                ],
            }
        )


def test_parse_canonical_mapping_manifest_rejects_missing_or_invalid_contract_fields():
    with pytest.raises(CanonicalMappingPackageError, match="package_schema_version"):
        parse_canonical_mapping_manifest({"files": []})

    with pytest.raises(CanonicalMappingPackageError, match="invalid"):
        parse_canonical_mapping_manifest(
            {"package_schema_version": "1.bad", "files": []}
        )

    with pytest.raises(CanonicalMappingPackageError, match="files must be a list"):
        parse_canonical_mapping_manifest({"package_schema_version": "1.0", "files": {}})


def test_parse_canonical_mapping_manifest_rejects_missing_required_files():
    with pytest.raises(CanonicalMappingPackageError, match="missing required files"):
        parse_canonical_mapping_manifest(
            {
                "package_schema_version": "1.0",
                "files": [{"name": "sds_standard_releases.csv"}],
            }
        )
