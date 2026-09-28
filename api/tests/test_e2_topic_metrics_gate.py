from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "e2_metrics.py"
SOURCE_PATH = (
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "e2_crosswalks_esrs_gri_full_atomized.csv"
)
COMPLETENESS_PATH = (
    REPO_ROOT / "data" / "extracted" / "analysis" / "e2_completeness_by_topic.csv"
)
DETECTION_PATH = (
    REPO_ROOT / "data" / "extracted" / "analysis" / "e2_detection_by_topic.csv"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("e2_metrics", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


metrics_module = _load_module()


def test_e2_topic_metrics_are_derived_from_atomized_crosswalk_source():
    metrics = metrics_module.derive_topic_metrics(SOURCE_PATH)
    by_topic = {metric.topic: metric for metric in metrics}

    assert by_topic["energy"].total == 85
    assert by_topic["energy"].with_both == 80
    assert by_topic["energy"].coverage_pct == 94
    assert by_topic["energy"].with_any == 85
    assert by_topic["energy"].detection_pct == 100

    assert by_topic["ghg"].total == 154
    assert by_topic["ghg"].with_both == 146
    assert by_topic["ghg"].coverage_pct == 95
    assert by_topic["ghg"].with_any == 154
    assert by_topic["ghg"].detection_pct == 100

    assert by_topic["water"].total == 53
    assert by_topic["water"].with_both == 43
    assert by_topic["water"].coverage_pct == 81
    assert by_topic["water"].with_any == 53
    assert by_topic["water"].detection_pct == 100


def test_e2_summary_csvs_match_source_derived_metrics():
    metrics = metrics_module.derive_topic_metrics(SOURCE_PATH)

    completeness_errors = metrics_module.compare_summary(
        expected_rows=metrics_module.completeness_rows(metrics),
        actual_rows=metrics_module.read_summary_rows(COMPLETENESS_PATH),
        checked_columns=("total", "with_esrs", "with_gri", "with_both", "coverage_pct"),
    )
    detection_errors = metrics_module.compare_summary(
        expected_rows=metrics_module.detection_rows(metrics),
        actual_rows=metrics_module.read_summary_rows(DETECTION_PATH),
        checked_columns=(
            "total",
            "with_any",
            "with_esrs",
            "with_esrs_family",
            "with_gri",
            "detection_pct",
        ),
    )

    assert completeness_errors == []
    assert detection_errors == []


def test_e2_summary_comparison_rejects_stale_static_baseline():
    metrics = metrics_module.derive_topic_metrics(SOURCE_PATH)
    stale_rows = [
        {
            "topic": "ghg",
            "total": "163",
            "with_esrs": "161",
            "with_gri": "153",
            "with_both": "153",
            "coverage_pct": "94",
        },
        {
            "topic": "energy",
            "total": "85",
            "with_esrs": "85",
            "with_gri": "80",
            "with_both": "80",
            "coverage_pct": "94",
        },
        {
            "topic": "water",
            "total": "70",
            "with_esrs": "66",
            "with_gri": "56",
            "with_both": "56",
            "coverage_pct": "80",
        },
    ]

    errors = metrics_module.compare_summary(
        expected_rows=metrics_module.completeness_rows(metrics),
        actual_rows=stale_rows,
        checked_columns=("total", "with_esrs", "with_gri", "with_both", "coverage_pct"),
    )

    assert any("ghg total" in error for error in errors)
    assert any("water total" in error for error in errors)
