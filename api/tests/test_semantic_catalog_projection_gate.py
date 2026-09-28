from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "api" / "scripts" / "gate_semantic_catalog_projection.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "gate_semantic_catalog_projection_script", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate_semantic_catalog_projection = _load_module()


def test_gate_script_help_works_without_jwt_secret_key():
    env = os.environ.copy()
    env.pop("JWT_SECRET_KEY", None)
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


def test_gate_script_does_not_import_settings_before_argparse():
    script = MODULE_PATH.read_text(encoding="utf-8")
    module_prefix = script.split("def _get_default_database_url", 1)[0]
    main_body_before_parse = script.split("args = parser.parse_args()", 1)[0]

    assert "from src.config.settings import settings" not in module_prefix
    assert "default=_get_default_database_url()" not in main_body_before_parse


def test_gate_fails_when_active_indicators_are_not_projected():
    failures = gate_semantic_catalog_projection.evaluate_projection_coverage(
        {
            "active_indicators": 3,
            "projected_active_indicators": 2,
            "missing_active_indicator_identifiers": ["urn:sds:reg:esrs:e1_6_01"],
            "ready": False,
        }
    )

    assert failures == [
        "Semantic catalog projection incomplete: 2/3 active indicators projected; "
        "missing examples: urn:sds:reg:esrs:e1_6_01"
    ]


def test_gate_passes_when_every_active_indicator_is_projected():
    failures = gate_semantic_catalog_projection.evaluate_projection_coverage(
        {
            "active_indicators": 3,
            "projected_active_indicators": 3,
            "missing_active_indicator_identifiers": [],
            "stale_active_indicator_identifiers": [],
            "active_disclosures": 2,
            "projected_disclosures": 2,
            "missing_disclosure_uris": [],
            "stale_disclosure_uris": [],
            "extra_disclosure_uris": [],
            "active_source_datapoints": 2,
            "projected_source_datapoints": 2,
            "missing_source_datapoint_uris": [],
            "stale_source_datapoint_uris": [],
            "extra_source_datapoint_uris": [],
            "unsupported_active_standard_ids": [],
            "ready": True,
        }
    )

    assert failures == []


def test_gate_fails_when_projection_is_stale():
    failures = gate_semantic_catalog_projection.evaluate_projection_coverage(
        {
            "active_indicators": 3,
            "projected_active_indicators": 3,
            "missing_active_indicator_identifiers": [],
            "stale_active_indicator_identifiers": ["urn:sds:reg:esrs:e1_6_01"],
            "ready": False,
        }
    )

    assert any("Stale projection detected" in f for f in failures)
    assert any("urn:sds:reg:esrs:e1_6_01" in f for f in failures)


def test_gate_fails_when_source_standard_datapoints_are_not_projected():
    failures = gate_semantic_catalog_projection.evaluate_projection_coverage(
        {
            "active_indicators": 3,
            "projected_active_indicators": 3,
            "missing_active_indicator_identifiers": [],
            "stale_active_indicator_identifiers": [],
            "active_source_datapoints": 2,
            "projected_source_datapoints": 1,
            "missing_source_datapoint_uris": [
                "urn:sds:standard-datapoint:csrd:esrs:2023-12-22:e5-5-ar-35"
            ],
            "stale_source_datapoint_uris": [],
            "ready": False,
        }
    )

    assert any("Source-standard catalog projection incomplete" in f for f in failures)
    assert any("urn:sds:standard-datapoint:csrd" in f for f in failures)


def test_gate_fails_when_disclosures_are_not_projected():
    failures = gate_semantic_catalog_projection.evaluate_projection_coverage(
        {
            "active_indicators": 3,
            "projected_active_indicators": 3,
            "missing_active_indicator_identifiers": [],
            "stale_active_indicator_identifiers": [],
            "active_disclosures": 2,
            "projected_disclosures": 1,
            "missing_disclosure_uris": ["urn:sds:disclosure:csrd:e1-6"],
            "stale_disclosure_uris": [],
            "extra_disclosure_uris": [],
            "ready": False,
        }
    )

    assert any("Disclosure catalog projection incomplete" in f for f in failures)
    assert any("urn:sds:disclosure:csrd:e1-6" in f for f in failures)


def test_gate_fails_when_projected_catalog_rows_are_extra():
    failures = gate_semantic_catalog_projection.evaluate_projection_coverage(
        {
            "active_indicators": 3,
            "projected_active_indicators": 3,
            "missing_active_indicator_identifiers": [],
            "stale_active_indicator_identifiers": [],
            "active_disclosures": 1,
            "projected_disclosures": 1,
            "missing_disclosure_uris": [],
            "stale_disclosure_uris": [],
            "extra_disclosure_uris": ["urn:sds:disclosure:csrd:old"],
            "active_source_datapoints": 1,
            "projected_source_datapoints": 1,
            "missing_source_datapoint_uris": [],
            "stale_source_datapoint_uris": [],
            "extra_source_datapoint_uris": [
                "urn:sds:standard-datapoint:csrd:esrs:old:obsolete"
            ],
            "ready": False,
        }
    )

    assert any("Extra disclosure projection rows detected" in f for f in failures)
    assert any("Extra source-standard projection rows detected" in f for f in failures)


def test_gate_fails_when_legacy_disclosure_is_not_allowlisted():
    failures = gate_semantic_catalog_projection.evaluate_projection_coverage(
        {
            "active_indicators": 3,
            "projected_active_indicators": 3,
            "missing_active_indicator_identifiers": [],
            "stale_active_indicator_identifiers": [],
            "active_disclosures": 1,
            "projected_disclosures": 1,
            "missing_disclosure_uris": [],
            "stale_disclosure_uris": [],
            "extra_disclosure_uris": [],
            "unsupported_legacy_disclosure_uris": ["csrd:UNCONTROLLED_DISCLOSURE"],
            "ready": False,
        }
    )

    assert any("Unsupported legacy disclosure rows detected" in f for f in failures)
    assert any("csrd:UNCONTROLLED_DISCLOSURE" in f for f in failures)


def test_gate_fails_when_unknown_active_standard_is_silently_skipped():
    failures = gate_semantic_catalog_projection.evaluate_projection_coverage(
        {
            "active_indicators": 3,
            "projected_active_indicators": 3,
            "missing_active_indicator_identifiers": [],
            "stale_active_indicator_identifiers": [],
            "unsupported_active_standard_ids": ["CUSTOM_UNKNOWN"],
            "ready": False,
        }
    )

    assert any("Unsupported active source standard(s)" in f for f in failures)
    assert any("CUSTOM_UNKNOWN" in f for f in failures)
