from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "summarize_canonical_mapping_defects.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "summarize_canonical_mapping_defects", SCRIPT_PATH
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_summarize_groups_legacy_mapping_defects():
    module = _load_module()
    rows = [
        {
            "classification": "legacy_mapping_defect",
            "issue_type": "relationship_mismatch",
            "source_code": "E1-5_02",
            "target_code": "GRI 302-1.e",
            "legacy_value": "equivalent",
            "canonical_value": "narrower",
        },
        {
            "classification": "legacy_mapping_defect",
            "issue_type": "confidence_mismatch",
            "source_code": "S1-9_03",
            "target_code": "GRI 405-1.b.ii",
            "legacy_value": "1.00",
            "canonical_value": "0.33",
        },
    ]

    enriched, summary = module.summarize(rows)

    assert summary["defect_count"] == 2
    assert summary["review_class_counts"] == {
        "legacy_confidence_overclaim": 1,
        "legacy_equivalence_overclaim_narrower": 1,
    }
    assert enriched[0]["source_prefix"] == "E1"
    assert enriched[0]["target_family"] == "GRI 302-1"
    assert "change legacy relationship" in enriched[0]["proposed_action"]


def test_write_csv_keeps_review_columns_first(tmp_path):
    module = _load_module()
    output = tmp_path / "summary.csv"
    rows = [
        {
            "review_class": "legacy_equivalence_overclaim_partial",
            "proposed_action": "review",
            "source_prefix": "E3",
            "target_family": "GRI 303-1",
            "issue_type": "relationship_mismatch",
            "source_standard": "ESRS",
            "source_code": "E3-3_04",
            "target_standard": "GRI",
            "target_code": "GRI 303-1.d",
            "field": "relationship_type",
            "legacy_value": "equivalent",
            "canonical_value": "partial",
            "classification_note": "note",
        }
    ]

    module.write_csv(output, rows)

    with output.open(encoding="utf-8", newline="") as handle:
        written = list(csv.DictReader(handle))

    assert written == rows
