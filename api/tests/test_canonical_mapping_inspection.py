"""Tests for report-only canonical mapping package inspection."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

from src.services import canonical_mapping_inspection as inspection
from src.services.canonical_mapping_import import (
    CanonicalMappingPackageRows,
    load_canonical_mapping_package_rows,
    validate_canonical_mapping_package,
)
from src.services.canonical_mapping_inspection import (
    _parse_json_object,
    _target_identity,
    inspect_canonical_mapping_package,
)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _sha256_for(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_package(
    package_dir: Path,
    *,
    relationship_type: str = "partial_overlap",
    mapping_profile: str = "default",
    target_identity: bool = False,
    declare_target_datapoint: bool = True,
) -> None:
    target_fields = (
        {
            "target_standard_id": "ESRS",
            "target_standard_version": "ESRS_Set1_2023-12-22_OJ:E1-6-v4-proof",
            "target_code": "significant_scope_3_category_emissions",
            "target_datapoint_id": "esrs:e1-6-significant-scope3-category",
            "mapping_direction": "source_to_target",
        }
        if target_identity
        else {}
    )
    release_rows = [
        {
            "standard_id": "GHG",
            "name": "GHG Protocol",
            "version": "scope3_reporting_v1",
        }
    ]
    datapoint_rows = [
        {
            "standard_id": "GHG",
            "standard_version": "scope3_reporting_v1",
            "code": "scope3_total_emissions_by_category",
            "label": "Scope 3 total emissions by category",
        }
    ]
    if target_identity and declare_target_datapoint:
        release_rows.append(
            {
                "standard_id": "ESRS",
                "name": "European Sustainability Reporting Standards",
                "version": "ESRS_Set1_2023-12-22_OJ:E1-6-v4-proof",
            }
        )
        datapoint_rows.append(
            {
                "standard_id": "ESRS",
                "standard_version": "ESRS_Set1_2023-12-22_OJ:E1-6-v4-proof",
                "code": "significant_scope_3_category_emissions",
                "label": "Significant Scope 3 category emissions",
            }
        )
    files = {
        "sds_standard_releases.csv": release_rows,
        "sds_standard_datapoints.csv": datapoint_rows,
        "sds_mapping_assertion_groups.csv": [
            {
                "source_standard_id": "GHG",
                "source_standard_version": "scope3_reporting_v1",
                "source_code": "scope3_total_emissions_by_category",
                "mapping_profile": mapping_profile,
                **target_fields,
                "relationship_type": relationship_type,
                "coverage_status": "partial",
                "confidence": "0.74",
                "rationale": "Non-exact review assertion.",
                "coverage_summary": "Category-level Scope 3 emissions.",
                "difference_summary": "Target has a different gate.",
                "valid_from": "2026-05-13T19:00:00Z",
                "valid_to": "",
                "approval_status": "draft",
                "publication_status": "internal",
                "assertion_hash": "a" * 64,
                "provenance": '{"seed":"unit-test"}',
            }
        ],
        "sds_mapping_assertion_components.csv": [
            {
                "source_standard_id": "GHG",
                "source_standard_version": "scope3_reporting_v1",
                "source_code": "scope3_total_emissions_by_category",
                "mapping_profile": mapping_profile,
                "component_order": "1",
                "sygris_canonical_uri": "syg:Scope3CategoryGreenhouseGasEmissions",
                "sygris_revision": "1",
                "component_role": "primary",
                "coverage_fraction": "1.0000",
                "match_scope": "Category-level Scope 3 emissions.",
                "mismatch_scope": "Target has a different gate.",
                "transformation_rule": "Do not infer equivalence.",
                "rationale": "Review-only pivot.",
            }
        ],
    }
    for filename, rows in files.items():
        _write_csv(package_dir / filename, rows)

    manifest = {
        "package_schema_version": "1.0",
        "files": [
            {"filename": filename, "sha256": _sha256_for(package_dir / filename)}
            for filename in files
        ],
    }
    (package_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (package_dir / "MANIFEST.sha256").write_text(
        "\n".join(
            f"{_sha256_for(package_dir / filename)} *{filename}" for filename in files
        )
        + "\n",
        encoding="utf-8",
    )


def test_inspect_canonical_mapping_package_lists_non_operational_candidate(tmp_path):
    _write_package(tmp_path)

    report = inspect_canonical_mapping_package(tmp_path)
    payload = report.as_dict()

    assert report.valid is True
    assert report.operational_eligible is False
    assert report.candidate_count == 1
    assert report.non_operational_candidate_count == 1
    assert payload["target_identity_status_counts"] == {"not_declared_in_package": 1}
    candidate = payload["candidates"][0]
    assert candidate["relationship_type"] == "partial_overlap"
    assert candidate["operational_eligible"] is False
    assert candidate["target_identity_status"] == "not_declared_in_package"
    assert candidate["components"][0]["sygris_canonical_uri"] == (
        "syg:Scope3CategoryGreenhouseGasEmissions"
    )
    assert candidate["provenance"] == {"seed": "unit-test"}


def test_inspect_canonical_mapping_package_marks_profile_only_target_identity(
    tmp_path,
):
    _write_package(
        tmp_path, relationship_type="component_of", mapping_profile="target:gri_305_3_a"
    )

    report = inspect_canonical_mapping_package(tmp_path)
    candidate = report.as_dict()["candidates"][0]

    assert candidate["relationship_type"] == "component_of"
    assert candidate["target_identity_status"] == "profile_only"
    assert candidate["target_profile"] == "target:gri_305_3_a"


def test_inspect_canonical_mapping_package_reports_declared_target_identity(
    tmp_path,
):
    _write_package(
        tmp_path,
        relationship_type="partial_overlap",
        mapping_profile="target:esrs_significant_scope3_category",
        target_identity=True,
    )

    report = inspect_canonical_mapping_package(tmp_path)
    payload = report.as_dict()
    candidate = payload["candidates"][0]

    assert payload["target_identity_status_counts"] == {"declared_in_package": 1}
    assert candidate["target_identity_status"] == "declared_in_package"
    assert candidate["target_profile"] == "target:esrs_significant_scope3_category"
    assert candidate["target_standard_id"] == "ESRS"
    assert candidate["target_code"] == "significant_scope_3_category_emissions"
    assert candidate["mapping_direction"] == "source_to_target"


def test_inspect_canonical_mapping_package_reports_missing_declared_target(
    tmp_path,
):
    _write_package(
        tmp_path,
        relationship_type="partial_overlap",
        mapping_profile="target:esrs_significant_scope3_category",
        target_identity=True,
        declare_target_datapoint=False,
    )

    report = inspect_canonical_mapping_package(tmp_path)
    payload = report.as_dict()
    candidate = payload["candidates"][0]

    assert payload["target_identity_status_counts"] == {"declared_missing_target": 1}
    assert candidate["target_identity_status"] == "declared_missing_target"
    assert candidate["target_standard_id"] == "ESRS"
    assert candidate["target_code"] == "significant_scope_3_category_emissions"


def test_inspect_canonical_mapping_package_can_include_operational_candidates(
    tmp_path,
):
    _write_package(tmp_path, relationship_type="equivalent")

    default_report = inspect_canonical_mapping_package(tmp_path)
    full_report = inspect_canonical_mapping_package(tmp_path, include_operational=True)

    assert default_report.candidate_count == 0
    assert full_report.candidate_count == 1
    assert full_report.as_dict()["candidates"][0]["operational_eligible"] is True


def test_inspect_canonical_mapping_package_script_emits_json(tmp_path):
    _write_package(tmp_path, mapping_profile="target:gri_305_3_a")
    api_root = Path(__file__).resolve().parents[1]
    script = api_root / "scripts" / "inspect_canonical_mapping_package.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--package-dir",
            str(tmp_path),
            "--json",
        ],
        cwd=api_root,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert payload["operational_eligible"] is False
    assert payload["candidates"][0]["target_identity_status"] == "profile_only"


def test_inspect_canonical_mapping_package_invalid_and_limit_branches(tmp_path):
    invalid_report = inspect_canonical_mapping_package(tmp_path)
    assert invalid_report.valid is False
    assert invalid_report.candidate_count == 0

    _write_package(tmp_path / "valid")
    with pytest.raises(ValueError, match="positive"):
        inspect_canonical_mapping_package(tmp_path / "valid", limit=0)


def test_inspection_validation_and_rows_use_one_private_snapshot(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    replacement_dir = tmp_path / "replacement"
    parked_dir = tmp_path / "parked"
    _write_package(package_dir, mapping_profile="source-profile")
    _write_package(replacement_dir, mapping_profile="replacement-profile")
    original_validate = inspection.validate_canonical_mapping_package
    swapped = False

    def validate_then_swap(candidate):
        nonlocal swapped
        report = original_validate(candidate)
        if not swapped:
            package_dir.rename(parked_dir)
            replacement_dir.rename(package_dir)
            swapped = True
        return report

    monkeypatch.setattr(
        inspection, "validate_canonical_mapping_package", validate_then_swap
    )

    report = inspect_canonical_mapping_package(package_dir)

    assert report.valid is True
    assert report.candidates[0].mapping_profile == "source-profile"


def test_inspection_rejects_symlink_package_dir(tmp_path):
    package_dir = tmp_path / "package"
    _write_package(package_dir)
    package_link = tmp_path / "package-link"
    package_link.symlink_to(package_dir, target_is_directory=True)

    report = inspect_canonical_mapping_package(package_link)

    assert report.valid is False


def test_inspection_applies_bounded_default_and_reports_truncation(
    monkeypatch, tmp_path
):
    package_dir = tmp_path / "package"
    _write_package(package_dir)
    validation = validate_canonical_mapping_package(package_dir)
    loaded = load_canonical_mapping_package_rows(package_dir)
    rows = CanonicalMappingPackageRows(
        releases=loaded.releases,
        datapoints=loaded.datapoints,
        assertion_groups=loaded.assertion_groups * 1001,
        assertion_components=loaded.assertion_components,
    )
    private_snapshot = tmp_path / "private-snapshot"
    monkeypatch.setattr(
        inspection,
        "canonical_mapping_package_snapshot",
        lambda _package_dir: nullcontext(private_snapshot),
        raising=False,
    )
    monkeypatch.setattr(
        inspection, "validate_canonical_mapping_package", lambda _dir: validation
    )
    monkeypatch.setattr(
        inspection, "load_canonical_mapping_package_rows", lambda _dir: rows
    )

    report = inspect_canonical_mapping_package(package_dir)

    assert report.candidate_count == inspection.DEFAULT_INSPECTION_LIMIT
    assert report.truncated is True


def test_inspection_target_identity_and_json_empty_helpers():
    assert _target_identity(
        {
            "mapping_profile": "target:partial",
            "target_standard_id": "ESRS",
            "target_standard_version": "",
            "target_code": "",
            "target_datapoint_id": "",
        },
        declared_datapoints=set(),
    ) == ("declared_missing_target", "target:partial")
    assert _parse_json_object("") is None
