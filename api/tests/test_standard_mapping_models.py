"""Tests for standard-agnostic mapping models and migration script."""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.database import models as db_models


def _load_migration_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "migrate_crosswalks_to_standard_mappings.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migrate_crosswalks_to_standard_mappings", module_path
    )
    assert spec and spec.loader, "Migration module spec not found"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_standard_mapping_model_schema():
    """Ensure standard mapping and standards registry models exist with expected columns."""
    assert hasattr(db_models, "StandardMapping")
    assert hasattr(db_models, "SustainabilityStandard")

    mapping_cols = set(db_models.StandardMapping.__table__.columns.keys())
    standard_cols = set(db_models.SustainabilityStandard.__table__.columns.keys())

    expected_mapping_cols = {
        "id",
        "source_standard",
        "source_code",
        "source_label",
        "target_standard",
        "target_code",
        "target_label",
        "esg_dimension",
        "relationship_type",
        "confidence",
        "dataset",
        "source_row",
        "mapping_metadata",
        "is_active",
        "created_at",
        "updated_at",
        "created_by",
    }
    expected_standard_cols = {
        "id",
        "name",
        "organization",
        "version",
        "url",
        "description",
        "is_active",
        "created_at",
    }

    assert expected_mapping_cols.issubset(mapping_cols)
    assert expected_standard_cols.issubset(standard_cols)
    assert db_models.StandardMapping.__tablename__ == "standard_mappings"
    assert db_models.SustainabilityStandard.__tablename__ == "sustainability_standards"


def test_migration_converts_crosswalks(monkeypatch):
    """Migration should seed standards and convert legacy crosswalks into standard mappings."""
    module = _load_migration_module()

    db = MagicMock()
    standards_query = MagicMock()
    crosswalk_query = MagicMock()

    def query_side_effect(model):
        if model is module.SustainabilityStandard:
            return standards_query
        if model is module.LegacyCrosswalk:
            return crosswalk_query
        return MagicMock()

    db.query.side_effect = query_side_effect
    standards_query.filter_by.return_value.first.return_value = None
    crosswalk_query.filter.return_value = crosswalk_query

    cw = SimpleNamespace(
        esrs_code="E1-1",
        indicator="GHG emissions",
        gri_code="305-1",
        gri_expanded="GRI 305-1a",
        esg_dimension="E",
        relationship_type=None,
        dataset="e2_crosswalks_esrs_gri.csv",
        source_row=10,
        is_active=True,
        created_at=datetime(2024, 1, 1),
    )
    crosswalk_query.all.return_value = [cw]

    monkeypatch.setattr(module, "SessionLocal", lambda: db)

    module.migrate_crosswalks()

    mapping_objects = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], module.StandardMapping)
    ]
    standard_objects = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], module.SustainabilityStandard)
    ]

    assert len(mapping_objects) == 1
    mapping = mapping_objects[0]
    assert mapping.source_standard == "ESRS"
    assert mapping.target_standard == "GRI"
    assert mapping.source_code == cw.esrs_code
    assert mapping.target_code == cw.gri_code
    assert mapping.source_label == cw.indicator
    assert mapping.target_label == cw.gri_expanded
    assert mapping.esg_dimension == cw.esg_dimension
    assert mapping.relationship_type == "equivalent"
    assert mapping.dataset == cw.dataset
    assert mapping.source_row == cw.source_row

    assert standard_objects
    assert db.commit.called
    db.close.assert_called_once()
