from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "api" / "scripts" / "import_standard_mappings.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "import_standard_mappings_script", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


import_standard_mappings = _load_module()


def test_default_json_output_points_to_bundled_mappings_snapshot():
    expected = REPO_ROOT / "api" / "src" / "data" / "mappings.json"
    assert import_standard_mappings.DEFAULT_JSON_OUTPUT == expected


def test_default_relationship_overrides_points_to_reviewed_cumulative_file():
    expected = (
        REPO_ROOT
        / "data"
        / "extracted"
        / "analysis"
        / "canonical-mapping-legacy-relationship-overrides-v42-reviewed-2026-05-11.csv"
    )
    assert import_standard_mappings.DEFAULT_RELATIONSHIP_OVERRIDES == expected


def test_map_row_to_mapping_uses_atomized_labels_and_flags():
    row = {
        "esrs": "BP-1_01",
        "gri": "GRI 2-2.a",
        "esg_dimension": "Transversal",
        "dataset": "General disclosures",
        "indicator": "Basis for preparation of sustainability statement",
        "ESRS_Label": "Basis for preparation of sustainability statement",
        "GRI_Label": "list all its entities included in its sustainability reporting",
        "WithBoth": "TRUE",
        "ESRS_Detected": "TRUE",
        "GRI_Detected": "TRUE",
        "SourceRow": "2",
        "interop_topic": "other",
    }

    mapping = import_standard_mappings.map_row_to_mapping(row)

    assert mapping is not None
    assert mapping["source_standard"] == "ESRS"
    assert mapping["source_code"] == "BP-1_01"
    assert (
        mapping["source_label"] == "Basis for preparation of sustainability statement"
    )
    assert mapping["target_standard"] == "GRI"
    assert mapping["target_code"] == "GRI 2-2.a"
    assert (
        mapping["target_label"]
        == "list all its entities included in its sustainability reporting"
    )
    assert mapping["relationship_type"] == "equivalent"
    assert mapping["confidence"] == 1.0
    assert mapping["source_row"] == 2
    assert mapping["mapping_metadata"]["with_both"] is True


def test_map_row_to_mapping_skips_rows_without_target_code():
    row = {
        "esrs": "BP-1_03",
        "gri": "",
        "esg_dimension": "Transversal",
    }
    assert import_standard_mappings.map_row_to_mapping(row) is None


def test_map_row_to_mapping_normalizes_atomic_gri_code_punctuation():
    row = {
        "esrs": "S1-7_08",
        "gri": "GRI 2-8.b-ii",
        "esg_dimension": "Social",
        "WithBoth": "TRUE",
    }

    mapping = import_standard_mappings.map_row_to_mapping(row)

    assert mapping is not None
    assert mapping["target_code"] == "GRI 2-8.b.ii"


def test_map_row_to_mapping_keeps_composite_gri_codes_unchanged():
    row = {
        "esrs": "E1-6_15",
        "gri": "GRI 305-1, 305-2; 305-3.1-e",
        "esg_dimension": "Environmental",
        "WithBoth": "TRUE",
    }

    mapping = import_standard_mappings.map_row_to_mapping(row)

    assert mapping is not None
    assert mapping["target_code"] == "GRI 305-1, 305-2; 305-3.1-e"


def test_map_row_to_mapping_applies_reviewed_relationship_override():
    row = {
        "esrs": "S1-2_01",
        "gri": "GRI 3-3.f",
        "esg_dimension": "Social",
        "WithBoth": "TRUE",
    }
    overrides = {
        ("ESRS", "S1-2_01", "GRI", "GRI 3-3.f"): {"relationship_type": "narrower"}
    }

    mapping = import_standard_mappings.map_row_to_mapping(row, overrides)

    assert mapping is not None
    assert mapping["relationship_type"] == "narrower"
    assert mapping["confidence"] == 1.0
    assert mapping["mapping_metadata"]["legacy_relationship_override"] is True


def test_load_relationship_overrides_normalizes_target_codes(tmp_path):
    overrides_path = tmp_path / "overrides.csv"
    overrides_path.write_text(
        "\n".join(
            [
                "source_standard,source_code,target_standard,target_code,relationship_type,confidence",
                "ESRS,S1-2_01,GRI,GRI 3-3.f,narrower,",
                "ESRS,S1-7_08,GRI,GRI 2-8.b-ii,narrower,0.75",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    overrides = import_standard_mappings.load_relationship_overrides(overrides_path)

    assert (
        overrides[("ESRS", "S1-2_01", "GRI", "GRI 3-3.f")]["relationship_type"]
        == "narrower"
    )
    assert overrides[("ESRS", "S1-7_08", "GRI", "GRI 2-8.b.ii")]["confidence"] == 0.75


def test_load_relationship_overrides_rejects_non_operational_relationships(tmp_path):
    overrides_path = tmp_path / "overrides.csv"
    overrides_path.write_text(
        "\n".join(
            [
                "source_standard,source_code,target_standard,target_code,relationship_type,confidence",
                "ESRS,E1-6_07,GRI,GRI 305-3.a,partial_overlap,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="partial_overlap"):
        import_standard_mappings.load_relationship_overrides(overrides_path)
