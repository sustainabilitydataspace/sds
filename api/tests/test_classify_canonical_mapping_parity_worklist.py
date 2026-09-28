from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "classify_canonical_mapping_parity_worklist.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "classify_canonical_mapping_parity_worklist", SCRIPT_PATH
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_classifies_supported_issue_types_for_cutover_review():
    module = _load_module()
    rows = [
        {
            "issue_type": "code_mismatch",
            "status": "unclassified",
            "suggested_classification": "legacy_code_normalization_review_required",
            "source_standard": "ESRS",
            "source_code": "S1-7_08",
            "target_standard": "GRI",
            "target_code": "GRI 2-8.b.ii",
            "field": "target_code",
            "legacy_value": "GRI 2-8.b-ii",
            "canonical_value": "GRI 2-8.b.ii",
        },
        {
            "issue_type": "relationship_mismatch",
            "status": "unclassified",
            "suggested_classification": "semantic_relationship_review_required",
            "source_standard": "ESRS",
            "source_code": "S1-7_09",
            "target_standard": "GRI",
            "target_code": "GRI 2-8.c",
            "field": "relationship_type",
            "legacy_value": "equivalent",
            "canonical_value": "broader",
        },
        {
            "issue_type": "missing_in_canonical",
            "status": "unclassified",
            "suggested_classification": "canonical_coverage_gap_or_materialization_bug",
            "source_standard": "ESRS",
            "source_code": "BP-1_01",
            "target_standard": "GRI",
            "target_code": "GRI 2-2.a",
            "field": "",
            "legacy_value": "",
            "canonical_value": "",
        },
    ]

    classified, summary = module.classify_rows(rows)

    assert [row["status"] for row in classified] == ["classified"] * 3
    assert [row["classification"] for row in classified] == [
        "legacy_code_normalization",
        "legacy_mapping_defect",
        "atomizer_coverage_gap",
    ]
    assert summary["row_count"] == 3
    assert summary["classification_counts"] == {
        "atomizer_coverage_gap": 1,
        "legacy_code_normalization": 1,
        "legacy_mapping_defect": 1,
    }
    assert "code-normalization cleanup" in module.cutover_decision_text(summary)


def test_cutover_text_drops_code_cleanup_after_normalization():
    module = _load_module()
    summary = {
        "classification_counts": {
            "atomizer_coverage_gap": 393,
            "legacy_mapping_defect": 149,
            "expected_semantic_improvement": 2,
        }
    }

    assert "code-normalization cleanup" not in module.cutover_decision_text(summary)
    assert module.next_action_text(summary).startswith("review legacy mapping defects")


def test_writes_reviewable_classified_csv(tmp_path):
    module = _load_module()
    output = tmp_path / "classified.csv"
    rows = [
        {
            "issue_type": "extra_in_canonical",
            "status": "classified",
            "suggested_classification": "legacy_gap_or_semantic_expansion",
            "source_standard": "ESRS",
            "source_code": "E1-6_07",
            "target_standard": "GRI",
            "target_code": "GRI 305-1.a",
            "field": "",
            "legacy_value": "",
            "canonical_value": "",
            "classification": "expected_semantic_improvement",
            "classification_note": "reviewed pair absent from legacy",
        }
    ]

    module.write_classified(output, rows)

    with output.open(encoding="utf-8", newline="") as handle:
        written = list(csv.DictReader(handle))

    assert written == rows
