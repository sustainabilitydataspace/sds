from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "e2_ghg_crosswalk_gate.py"
CSV_PATH = (
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "e2_crosswalks_ghg_protocol_esrs_gri_strict_exact_v2026-05-14.csv"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("e2_ghg_crosswalk_gate", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_module()


def test_ghg_exact_crosswalk_snapshot_passes_gate():
    rows = gate.load_rows(CSV_PATH)

    assert gate.validate_rows(rows, expected_count=6) == []


def test_ghg_exact_crosswalk_snapshot_is_not_public_api_cutover():
    rows = gate.load_rows(CSV_PATH)

    assert {row["source_standard"] for row in rows} == {"GHG"}
    assert {row["target_standard"] for row in rows} == {"ESRS", "GRI"}
    assert {row["relationship_type"] for row in rows} == {"equivalent"}
    assert {row["publication_status"] for row in rows} == {"beta_evidence_only"}
    assert {row["sds_cutover_status"] for row in rows} == {"no_api_change"}


def test_ghg_exact_crosswalk_gate_rejects_scope3_rows():
    bad_rows = [
        {
            "mapping_id": "bad-001",
            "source_standard": "GHG",
            "source_release": "GHG_PROTOCOL_SCOPE3_STANDARD",
            "source_code": "scope3_total_emissions",
            "source_label": "Total Scope 3 emissions",
            "target_standard": "GRI",
            "target_release": "GRI_305_EMISSIONS_2016",
            "target_code": "GRI 305-3.a",
            "target_label": "Gross other indirect Scope 3 GHG emissions",
            "relationship_type": "equivalent",
            "confidence": "0.95",
            "approval_status": "approved_for_local_beta_package",
            "publication_status": "beta_evidence_only",
            "sds_cutover_status": "no_api_change",
            "source_package_id": "test",
            "source_assertion_id": "test",
            "rationale": "Scope 3 rows are deliberately deferred.",
        }
    ]

    errors = gate.validate_rows(bad_rows, expected_count=1)

    assert any("Scope 3" in error for error in errors)


def test_ghg_exact_crosswalk_gate_rejects_non_equivalent_relationship():
    rows = gate.load_rows(CSV_PATH)
    bad_rows = [dict(row) for row in rows]
    bad_rows[0]["relationship_type"] = "partial_overlap"

    errors = gate.validate_rows(bad_rows, expected_count=6)

    assert any("relationship_type" in error for error in errors)
