"""Tests for canonical mapping package shadow validation."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.services import canonical_mapping_import as cmi
from src.services.canonical_mapping_import import validate_canonical_mapping_package


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
    tamper_checksum: bool = False,
    relationship_type: str = "equivalent",
) -> None:
    files = {
        "sds_standard_releases.csv": [
            {
                "standard_id": "ESRS",
                "name": "European Sustainability Reporting Standards",
                "version": "2024",
                "release_date": "2024-01-01",
                "source_url": "https://example.invalid/esrs",
                "lifecycle_status": "active",
            }
        ],
        "sds_standard_datapoints.csv": [
            {
                "standard_id": "ESRS",
                "standard_version": "2024",
                "code": "E1-1",
                "label": "Transition plan",
                "disclosure_text": "Plan disclosure",
                "datapoint_type": "disclosure",
                "unit": "",
                "lifecycle_status": "active",
            }
        ],
        "sds_mapping_assertion_groups.csv": [
            {
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": "E1-1",
                "mapping_profile": "default",
                "relationship_type": relationship_type,
                "coverage_status": "complete",
                "confidence": "1.0",
                "rationale": "Same canonical Sygris concept.",
                "coverage_summary": "Full coverage.",
                "difference_summary": "",
                "valid_from": "2026-05-08T00:00:00Z",
                "valid_to": "",
                "approval_status": "approved",
                "publication_status": "internal",
            }
        ],
        "sds_mapping_assertion_components.csv": [
            {
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": "E1-1",
                "mapping_profile": "default",
                "component_order": "1",
                "sygris_canonical_uri": "syg:Transition_Plan",
                "sygris_revision": "1",
                "component_role": "primary",
                "coverage_fraction": "1.0",
                "match_scope": "Full statement",
                "mismatch_scope": "",
                "transformation_rule": "",
                "rationale": "Same requirement.",
            }
        ],
    }
    for filename, rows in files.items():
        _write_csv(package_dir / filename, rows)

    manifest = {
        "package_schema_version": "1.0",
        "files": [
            {
                "filename": filename,
                "sha256": _sha256_for(package_dir / filename),
            }
            for filename in files
        ],
    }
    (package_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    checksum_lines = []
    for filename in files:
        digest = _sha256_for(package_dir / filename)
        if tamper_checksum and filename == "sds_mapping_assertion_groups.csv":
            digest = "0" * 64
        checksum_lines.append(f"{digest} *{filename}")
    (package_dir / "MANIFEST.sha256").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )


def _refresh_package_hashes(package_dir: Path) -> None:
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = [item["filename"] for item in manifest["files"]]
    for item in manifest["files"]:
        item["sha256"] = _sha256_for(package_dir / item["filename"])
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (package_dir / "MANIFEST.sha256").write_text(
        "\n".join(f"{_sha256_for(package_dir / name)} *{name}" for name in names)
        + "\n",
        encoding="utf-8",
    )


def _refresh_checksum_sidecar_only(package_dir: Path) -> None:
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    names = [item["filename"] for item in manifest["files"]]
    (package_dir / "MANIFEST.sha256").write_text(
        "\n".join(f"{_sha256_for(package_dir / name)} *{name}" for name in names)
        + "\n",
        encoding="utf-8",
    )


def test_validate_canonical_mapping_package_accepts_valid_package(tmp_path):
    _write_package(tmp_path)

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is True
    assert report.mode == "shadow_report_only"
    assert report.package_schema_version == "1.0"
    assert report.file_counts["sds_mapping_assertion_components.csv"] == 1
    assert report.checksum_count == 4
    assert report.operational_eligible is True
    assert report.relationship_type_counts == {"equivalent": 1}
    assert report.non_operational_relationship_type_counts == {}
    assert report.operational_blockers == []
    assert report.errors == []

    rows = cmi.load_canonical_mapping_package_rows(tmp_path)
    assert rows.releases[0]["standard_id"] == "ESRS"
    assert rows.datapoints[0]["code"] == "E1-1"
    assert rows.assertion_groups[0]["mapping_profile"] == "default"
    assert rows.assertion_components[0]["component_order"] == "1"


def test_validate_canonical_mapping_package_rejects_unsafe_package_objects(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    _write_package(package)

    outside = tmp_path / "outside-manifest.json"
    outside.write_bytes((package / "manifest.json").read_bytes())
    (package / "manifest.json").unlink()
    (package / "manifest.json").symlink_to(outside)
    symlink_report = validate_canonical_mapping_package(package)
    assert symlink_report.valid is False
    assert any(issue.code == "unsafe_package_object" for issue in symlink_report.errors)

    (package / "manifest.json").unlink()
    os.link(outside, package / "manifest.json")
    hardlink_report = validate_canonical_mapping_package(package)
    assert hardlink_report.valid is False
    assert any(
        issue.code == "unsafe_package_object" for issue in hardlink_report.errors
    )


def test_validate_canonical_mapping_package_rejects_open_inventory_and_duplicate_json(
    tmp_path,
):
    _write_package(tmp_path)
    (tmp_path / "undeclared.txt").write_text("unexpected", encoding="utf-8")

    extra_report = validate_canonical_mapping_package(tmp_path)
    assert extra_report.valid is False
    assert any(
        issue.code == "package_inventory_mismatch" for issue in extra_report.errors
    )

    (tmp_path / "undeclared.txt").unlink()
    manifest = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    duplicate = manifest.replace(
        '"package_schema_version": "1.0",',
        '"package_schema_version": "1.0",\n  "package_schema_version": "1.0",',
    )
    (tmp_path / "manifest.json").write_text(duplicate, encoding="utf-8")

    duplicate_report = validate_canonical_mapping_package(tmp_path)
    assert duplicate_report.valid is False
    assert any(issue.code == "manifest_invalid" for issue in duplicate_report.errors)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("invalid_utf8", "manifest_invalid"),
        ("not_object", "manifest_invalid"),
        ("files_not_list", "manifest_invalid"),
        ("item_not_object", "manifest_invalid"),
        ("conflicting_name_aliases", "manifest_invalid"),
        ("duplicate_filename", "manifest_invalid"),
        ("escaped_filename", "manifest_path_outside_package"),
        ("invalid_digest", "manifest_invalid"),
        ("missing_csv", "package_inventory_mismatch"),
    ],
)
def test_canonical_mapping_manifest_rejects_ambiguous_file_authority(
    tmp_path, mutation, expected_code
):
    package = tmp_path / mutation
    _write_package(package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if mutation == "invalid_utf8":
        raw = b"\xff"
    elif mutation == "not_object":
        raw = b"[]"
    else:
        if mutation == "files_not_list":
            manifest["files"] = {}
        elif mutation == "item_not_object":
            manifest["files"][0] = "sds_standard_releases.csv"
        elif mutation == "conflicting_name_aliases":
            manifest["files"][0]["path"] = "sds_standard_datapoints.csv"
        elif mutation == "duplicate_filename":
            manifest["files"].append(dict(manifest["files"][0]))
        elif mutation == "escaped_filename":
            manifest["files"][0]["filename"] = "../sds_standard_releases.csv"
        elif mutation == "invalid_digest":
            manifest["files"][0]["sha256"] = "A" * 64
        elif mutation == "missing_csv":
            manifest["files"].pop()
        raw = json.dumps(manifest).encode("utf-8")
    manifest_path.write_bytes(raw)

    report = validate_canonical_mapping_package(package)
    assert report.valid is False
    assert expected_code in {issue.code for issue in report.errors}


def test_canonical_mapping_snapshot_is_the_only_consumed_package(tmp_path):
    _write_package(tmp_path)
    source = tmp_path / "sds_mapping_assertion_groups.csv"

    with cmi.canonical_mapping_package_snapshot(tmp_path) as snapshot:
        original = (snapshot / source.name).read_bytes()
        source.write_text("mutated after snapshot\n", encoding="utf-8")
        rows = cmi.load_canonical_mapping_package_rows(snapshot)

    assert original != source.read_bytes()
    assert rows.assertion_groups[0]["source_code"] == "E1-1"


def test_canonical_mapping_snapshot_binds_source_directory_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_dir = tmp_path / "package"
    replacement = tmp_path / "replacement"
    _write_package(package_dir)
    _write_package(replacement)
    release_csv = replacement / "sds_standard_releases.csv"
    release_csv.write_text(
        release_csv.read_text(encoding="utf-8").replace(
            "European Sustainability Reporting Standards", "ATTACK"
        ),
        encoding="utf-8",
    )
    _refresh_package_hashes(replacement)

    original_open = cmi._open_directory_without_symlinks

    def replace_source_after_open(path: Path) -> int:
        descriptor = original_open(path)
        package_dir.rename(tmp_path / "original-package")
        replacement.rename(package_dir)
        return descriptor

    monkeypatch.setattr(
        cmi, "_open_directory_without_symlinks", replace_source_after_open
    )

    rows = cmi.load_canonical_mapping_package_rows(package_dir)

    assert rows.releases[0]["name"] == "European Sustainability Reporting Standards"


def test_validate_canonical_mapping_package_rejects_duplicate_checksum_entries(
    tmp_path,
):
    _write_package(tmp_path)
    checksum = tmp_path / "MANIFEST.sha256"
    first = checksum.read_text(encoding="utf-8").splitlines()[0]
    checksum.write_text(
        checksum.read_text(encoding="utf-8") + first + "\n", encoding="utf-8"
    )

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is False
    assert any(issue.code == "checksum_invalid" for issue in report.errors)


def test_validate_rejects_manifest_sidecar_digest_disagreement(tmp_path):
    _write_package(tmp_path)
    payload = tmp_path / "sds_standard_releases.csv"
    payload.write_bytes(payload.read_bytes() + b"\n")
    _refresh_checksum_sidecar_only(tmp_path)

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is False
    assert any(issue.code == "manifest_checksum_mismatch" for issue in report.errors)


def test_validate_canonical_mapping_package_rejects_noncanonical_manifest_digest(
    tmp_path,
):
    _write_package(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = manifest["files"][0]["sha256"].upper()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is False
    assert any(issue.code == "manifest_invalid" for issue in report.errors)


def test_validate_canonical_mapping_package_rejects_duplicate_csv_headers(tmp_path):
    _write_package(tmp_path)
    releases = tmp_path / "sds_standard_releases.csv"
    lines = releases.read_text(encoding="utf-8").splitlines()
    lines[0] += ",standard_id"
    lines[1] += ",SHADOW"
    releases.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _refresh_package_hashes(tmp_path)

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is False
    assert any(issue.code == "duplicate_headers" for issue in report.errors)


def test_validate_rejects_duplicate_keys_in_embedded_json(tmp_path):
    _write_package(tmp_path)
    groups = tmp_path / "sds_mapping_assertion_groups.csv"
    lines = groups.read_text(encoding="utf-8").splitlines()
    lines[0] += ",provenance"
    lines[1] += ',"{""source"":""first"",""source"":""second""}"'
    groups.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _refresh_package_hashes(tmp_path)

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is False
    assert any(issue.code == "invalid_value" for issue in report.errors)


def test_validate_canonical_mapping_package_enforces_row_budget(tmp_path, monkeypatch):
    _write_package(tmp_path)
    monkeypatch.setattr(cmi, "MAX_CSV_ROWS", 0, raising=False)

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is False
    assert any(issue.code == "package_resource_limit" for issue in report.errors)


def test_validate_canonical_mapping_package_reports_non_operational_relationships(
    tmp_path,
):
    _write_package(tmp_path, relationship_type="partial_overlap")

    report = validate_canonical_mapping_package(tmp_path)
    payload = report.as_dict()

    assert report.valid is True
    assert report.operational_eligible is False
    assert report.relationship_type_counts["partial_overlap"] == 1
    assert report.non_operational_relationship_type_counts["partial_overlap"] == 1
    assert "partial_overlap" in report.operational_blockers[0]
    assert payload["operational_eligible"] is False
    assert payload["non_operational_relationship_type_counts"] == {"partial_overlap": 1}


def test_canonical_mapping_manifest_resolution_and_checksum_edges(tmp_path):
    errors: list[cmi.CanonicalMappingImportIssue] = []
    manifest = {
        "files": [
            "skip",
            {},
            {"filename": "sds_standard_releases.csv", "path": "../escape.csv"},
            {
                "filename": "sds_standard_datapoints.csv",
                "path": "sds_standard_datapoints.csv",
            },
        ]
    }

    resolved = cmi._resolve_manifest_files(tmp_path.resolve(), manifest, errors)

    assert cmi.DATAPOINTS_FILE in resolved
    assert errors[0].code == "manifest_path_outside_package"

    missing_errors, checked = cmi._validate_checksum_file(
        tmp_path / "MANIFEST.sha256", []
    )
    assert checked == 0
    assert missing_errors[0].code == "checksum_missing"

    present = tmp_path / "present.csv"
    present.write_text("value\n1\n", encoding="utf-8")
    absent = tmp_path / "absent.csv"
    checksum = tmp_path / "MANIFEST.sha256"
    checksum.write_text(
        "\n".join(
            [
                "not-a-valid-line",
                f"{'0' * 64} *present.csv",
                f"{'1' * 64} *absent.csv",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    checksum_errors, checked = cmi._validate_checksum_file(
        checksum, [present, absent, tmp_path / "unlisted.csv"]
    )

    assert checked == 0
    assert {issue.code for issue in checksum_errors} == {"checksum_invalid"}

    checksum.write_text("\n\nnot-a-valid-line\n", encoding="utf-8")
    assert cmi._read_checksum_file(checksum) == {}


def test_canonical_mapping_low_level_invalid_values_and_unknown_group_source():
    errors: list[cmi.CanonicalMappingImportIssue] = []
    warnings: list[cmi.CanonicalMappingImportIssue] = []

    group_keys = cmi._validate_groups(
        [
            {
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": "MISSING",
                "mapping_profile": "default",
                "relationship_type": "equivalent",
                "coverage_status": "complete",
                "confidence": "bad-decimal",
                "valid_from": "2026-05-23T00:00:00Z",
                "approval_status": "approved",
                "publication_status": "internal",
            }
        ],
        datapoint_keys=set(),
        errors=errors,
        warnings=warnings,
    )

    assert group_keys == {("ESRS", "2024", "MISSING", "default")}
    assert "unknown_source_datapoint" in {issue.code for issue in errors}
    assert "invalid_value" in {issue.code for issue in errors}

    cmi._validate_date("not-a-date", "file.csv", 2, "release_date", errors)
    assert errors[-1].code == "invalid_value"


def test_canonical_mapping_manifest_loader_rejects_missing_and_non_object(
    tmp_path,
):
    try:
        cmi._load_manifest(tmp_path / "manifest.json")
    except FileNotFoundError as exc:
        assert "manifest not found" in str(exc)
    else:
        raise AssertionError("missing manifest should fail")

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("[]", encoding="utf-8")
    try:
        cmi._load_manifest(manifest_path)
    except cmi.CanonicalMappingPackageError as exc:
        assert "manifest must be a JSON object" in str(exc)
    else:
        raise AssertionError("non-object manifest should fail")


def test_canonical_mapping_csv_loader_and_row_validators_cover_error_contracts(
    tmp_path,
):
    errors: list[cmi.CanonicalMappingImportIssue] = []
    counts: dict[str, int] = {}

    assert (
        cmi._load_required_csv(
            None, cmi.RELEASES_FILE, cmi.RELEASE_HEADERS, errors, counts
        )
        == []
    )
    assert errors[-1].code == "file_missing"
    assert counts["sds_standard_releases.csv"] == 0

    missing_path = tmp_path / "missing.csv"
    assert (
        cmi._load_required_csv(
            missing_path, cmi.DATAPOINTS_FILE, cmi.DATAPOINT_HEADERS, errors, counts
        )
        == []
    )
    assert errors[-1].code == "file_missing"

    bad_headers = tmp_path / "bad_headers.csv"
    bad_headers.write_text("standard_id\nESRS\n", encoding="utf-8")
    assert (
        cmi._load_required_csv(
            bad_headers,
            cmi.DATAPOINTS_FILE,
            cmi.DATAPOINT_HEADERS,
            errors,
            counts,
        )
        == []
    )
    assert errors[-1].code == "missing_headers"

    empty = tmp_path / "empty.csv"
    empty.write_text(",".join(cmi.RELEASE_HEADERS) + "\n", encoding="utf-8")
    assert (
        cmi._load_required_csv(
            empty, cmi.RELEASES_FILE, cmi.RELEASE_HEADERS, errors, counts
        )
        == []
    )
    assert errors[-1].code == "empty_file"

    groups_errors: list[cmi.CanonicalMappingImportIssue] = []
    groups_warnings: list[cmi.CanonicalMappingImportIssue] = []
    group_keys = cmi._validate_groups(
        [
            {
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": "E1-1",
                "mapping_profile": "default",
                "relationship_type": "equivalent",
                "coverage_status": "gap",
                "confidence": "2",
                "valid_from": "not-a-date",
                "valid_to": "also-not-a-date",
                "approval_status": "approved",
                "publication_status": "internal",
                "provenance": "[]",
            },
            {
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": "E1-1",
                "mapping_profile": "default",
                "relationship_type": "equivalent",
                "coverage_status": "complete",
                "confidence": "1.0",
                "valid_from": "2026-05-23T00:00:00Z",
                "approval_status": "approved",
                "publication_status": "internal",
                "provenance": "{}",
            },
        ],
        datapoint_keys={("ESRS", "2024", "E1-1")},
        errors=groups_errors,
        warnings=groups_warnings,
    )
    assert ("ESRS", "2024", "E1-1", "default") in group_keys
    assert "invalid_value" in {issue.code for issue in groups_errors}
    assert "duplicate_key" in {issue.code for issue in groups_errors}
    assert groups_warnings[0].code == "approved_gap_or_exclusion"

    component_errors: list[cmi.CanonicalMappingImportIssue] = []
    cmi._validate_components(
        [
            {
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": "E1-404",
                "mapping_profile": "default",
                "component_order": "bad",
                "sygris_canonical_uri": "bad-uri",
                "sygris_revision": "0",
                "component_role": "primary",
                "coverage_fraction": "NaN",
            },
            {
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": "E1-1",
                "mapping_profile": "default",
                "component_order": "1",
                "sygris_canonical_uri": "syg:Emissions",
                "sygris_revision": "1",
                "component_role": "primary",
            },
            {
                "source_standard_id": "ESRS",
                "source_standard_version": "2024",
                "source_code": "E1-1",
                "mapping_profile": "default",
                "component_order": "1",
                "sygris_canonical_uri": "https://sustainabilitydataspace.com/sygris#Energy",
                "sygris_revision": "1",
                "component_role": "secondary",
            },
        ],
        group_keys=group_keys,
        errors=component_errors,
    )
    assert "unknown_assertion_group" in {issue.code for issue in component_errors}
    assert "invalid_sygris_canonical_uri" in {issue.code for issue in component_errors}
    assert "duplicate_component_order" in {issue.code for issue in component_errors}


def test_canonical_mapping_low_level_validators_cover_empty_and_valid_edges():
    errors: list[cmi.CanonicalMappingImportIssue] = []
    cmi._require_values(
        {"standard_id": "", "name": "ESRS"},
        ["standard_id", "name"],
        "sds_standard_releases.csv",
        2,
        errors,
    )
    cmi._validate_json_object_field(
        {"metadata_json": ""},
        "metadata_json",
        "sds_standard_datapoints.csv",
        2,
        errors,
    )
    cmi._validate_json_object_field(
        {"metadata_json": "[]"},
        "metadata_json",
        "sds_standard_datapoints.csv",
        3,
        errors,
    )
    cmi._validate_sygris_canonical_uri(
        "",
        "sds_mapping_assertion_components.csv",
        2,
        errors,
    )
    cmi._validate_sygris_canonical_uri(
        "https://sustainabilitydataspace.com/sygris#Energy",
        "sds_mapping_assertion_components.csv",
        3,
        errors,
    )

    assert errors[0].code == "missing_required_values"
    assert errors[1].code == "invalid_value"
    assert len(errors) == 2


def test_load_canonical_mapping_package_rows_rejects_unloadable_package(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"files": [{"filename": "sds_standard_releases.csv"}]}),
        encoding="utf-8",
    )

    try:
        cmi.load_canonical_mapping_package_rows(tmp_path)
    except cmi.CanonicalMappingPackageError as exc:
        assert "cannot load canonical mapping package rows" in str(exc)
    else:
        raise AssertionError("expected package row loading to fail")


def test_validate_canonical_mapping_package_reports_bad_reference(tmp_path):
    _write_package(tmp_path)
    path = tmp_path / "sds_mapping_assertion_components.csv"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("E1-1", "E1-404"), encoding="utf-8")

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is False
    assert any(error.code == "unknown_assertion_group" for error in report.errors)
    assert any(error.code == "checksum_mismatch" for error in report.errors)


def test_validate_canonical_mapping_package_rejects_future_schema(tmp_path):
    _write_package(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["package_schema_version"] = "2.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is False
    assert any(error.code == "manifest_invalid" for error in report.errors)


def test_validate_canonical_mapping_package_reports_checksum_mismatch(tmp_path):
    _write_package(tmp_path, tamper_checksum=True)

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is False
    assert any(error.code == "checksum_mismatch" for error in report.errors)


def test_validate_canonical_mapping_package_accepts_manifest_path_entries(tmp_path):
    _write_package(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["files"]:
        item["path"] = item.pop("filename")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is True
    assert report.checksum_count == 4


def test_validate_canonical_mapping_package_rejects_path_escape(tmp_path):
    _write_package(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0] = {"path": "../sds_standard_releases.csv"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is False
    assert any(error.code == "manifest_path_outside_package" for error in report.errors)


def test_validate_canonical_mapping_package_rejects_non_sygris_component(tmp_path):
    _write_package(tmp_path)
    path = tmp_path / "sds_mapping_assertion_components.csv"
    text = path.read_text(encoding="utf-8")
    text = text.replace("syg:Transition_Plan", "gri:Transition_Plan")
    text = text.replace(",1,primary,", ",0,primary,")
    path.write_text(text, encoding="utf-8")

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is False
    assert any(error.code == "invalid_sygris_canonical_uri" for error in report.errors)
    assert any(
        error.code == "invalid_value" and "sygris_revision" in error.message
        for error in report.errors
    )


def test_validate_canonical_mapping_package_rejects_invalid_json_metadata(tmp_path):
    _write_package(tmp_path)
    path = tmp_path / "sds_standard_datapoints.csv"
    text = path.read_text(encoding="utf-8")
    text = text.replace("lifecycle_status\r\n", "lifecycle_status,metadata_json\r\n")
    text = text.replace("lifecycle_status\n", "lifecycle_status,metadata_json\n")
    text = text.replace("active\r\n", "active,{bad-json}\r\n")
    text = text.replace("active\n", "active,{bad-json}\n")
    path.write_text(text, encoding="utf-8")

    report = validate_canonical_mapping_package(tmp_path)

    assert report.valid is False
    assert any(
        error.code == "invalid_value" and "metadata_json" in error.message
        for error in report.errors
    )


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (
            "key,"
            + ",".join(f"field_{n}" for n in range(cmi.MAX_CSV_COLUMNS))
            + "\na,\n",
            "package_resource_limit",
        ),
        ("key\na,extra\n", "invalid_row_shape"),
        (
            "key\n" + "x" * (cmi.MAX_CSV_FIELD_CHARS + 1) + "\n",
            "package_resource_limit",
        ),
    ],
)
def test_required_mapping_csv_rejects_resource_and_shape_faults_without_partial_rows(
    tmp_path, payload, expected_code
):
    path = tmp_path / "candidate.csv"
    path.write_text(payload, encoding="utf-8")
    previous_limit = csv.field_size_limit()
    errors, file_counts = [], {}
    rows = cmi._load_required_csv(path, "candidate", ("key",), errors, file_counts)
    assert rows == []
    assert any(issue.code == expected_code for issue in errors)
    assert csv.field_size_limit() == previous_limit


def test_mapping_checksum_sidecar_rejects_invalid_utf8_without_partial_acceptance(
    tmp_path,
):
    sidecar = tmp_path / "MANIFEST.sha256"
    sidecar.write_bytes(b"\xff")
    issues, checked = cmi._validate_checksum_file(sidecar, [tmp_path / "candidate.csv"])
    assert checked == 0
    assert [issue.code for issue in issues] == ["checksum_invalid"]


def test_mapping_checksum_sidecar_rejects_missing_declared_file(tmp_path):
    sidecar = tmp_path / "MANIFEST.sha256"
    missing = tmp_path / "candidate.csv"
    sidecar.write_text("0" * 64 + "  candidate.csv\n", encoding="utf-8")
    issues, checked = cmi._validate_checksum_file(sidecar, [missing])
    assert checked == 0
    assert [issue.code for issue in issues] == ["file_missing"]


def test_validate_canonical_mapping_package_script_emits_json(tmp_path):
    _write_package(tmp_path)
    api_root = Path(__file__).resolve().parents[1]
    script = api_root / "scripts" / "validate_canonical_mapping_package.py"

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
    assert payload["mode"] == "shadow_report_only"
    assert payload["operational_eligible"] is True
