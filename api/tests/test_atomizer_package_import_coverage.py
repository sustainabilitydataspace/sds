from __future__ import annotations

import json

import pytest

from src.services.atomizer_package_import import (
    CALCULATION_CONTRACT_FILENAME,
    STANDARD_VERSIONING_SCHEMA_VERSION,
    AtomizerPackageImportError,
    resolve_calculation_contract_selection,
    validate_standard_versioning_manifest,
)


def _sha(char: str = "a") -> str:
    return char * 64


def _valid_standard_versioning() -> dict:
    return {
        "schema_version": STANDARD_VERSIONING_SCHEMA_VERSION,
        "release_manifest": {
            "standard_id": "ESRS",
            "release_id": "ESRS_SET1_2023_12_22",
            "release_state": "active",
            "source_hash": _sha("a"),
            "source_hash_set": {"standard_pdf": _sha("b")},
        },
        "delta_manifest": {"transition_kind": "initial_snapshot"},
        "affected_scope_plan": {"scope_states": {"E1": "active"}},
        "datapoint_lineage_map": [
            {
                "sds_identifier": "urn:sds:reg:esrs:e1_6",
                "standard_id": "ESRS",
                "release_id": "ESRS_SET1_2023_12_22",
                "datapoint_id": "E1-6",
            }
        ],
        "tenant_activation_policy": {
            "dry_run_required": True,
            "real_import_requires_explicit_approval": True,
        },
    }


def test_resolve_calculation_contract_selection_modes_and_locations(tmp_path):
    package_dir = tmp_path / "package"
    package_dir.mkdir()

    disabled = resolve_calculation_contract_selection(
        package_dir=package_dir,
        semantics_mode="off",
    )
    assert disabled.enabled is False
    assert disabled.reason == "disabled"

    with pytest.raises(AtomizerPackageImportError, match="semantics_mode"):
        resolve_calculation_contract_selection(
            package_dir=package_dir,
            semantics_mode="bad",
        )

    with pytest.raises(AtomizerPackageImportError, match="cannot be combined"):
        resolve_calculation_contract_selection(
            package_dir=package_dir,
            skip=True,
            require=True,
        )

    missing_optional = resolve_calculation_contract_selection(package_dir=package_dir)
    assert missing_optional.enabled is False
    assert missing_optional.reason.startswith("missing_optional:")

    contract_path = package_dir / CALCULATION_CONTRACT_FILENAME
    contract_path.write_text("{}", encoding="utf-8")
    found_default = resolve_calculation_contract_selection(package_dir=package_dir)
    assert found_default.enabled is True
    assert found_default.required is False
    assert found_default.path == contract_path.resolve()

    explicit = resolve_calculation_contract_selection(
        package_dir=package_dir,
        explicit_path=contract_path,
    )
    assert explicit.enabled is True
    assert explicit.required is True

    with pytest.raises(AtomizerPackageImportError, match="not found"):
        resolve_calculation_contract_selection(
            package_dir=package_dir,
            explicit_path=tmp_path / "missing.json",
        )


def test_resolve_calculation_contract_selection_finds_repo_relative_path(tmp_path):
    package_dir = tmp_path / "package"
    repo_root = tmp_path / "repo"
    package_dir.mkdir()
    (repo_root / "contracts").mkdir(parents=True)
    repo_contract = repo_root / "contracts" / "sds_calculation_contract.json"
    repo_contract.write_text("{}", encoding="utf-8")

    selection = resolve_calculation_contract_selection(
        package_dir=package_dir,
        explicit_path=repo_contract.relative_to(repo_root),
        repo_root=repo_root,
    )

    assert selection.enabled is True
    assert selection.required is True
    assert selection.path == repo_contract.resolve()


def test_validate_standard_versioning_manifest_missing_and_invalid_shapes(tmp_path):
    missing_required = validate_standard_versioning_manifest(tmp_path, require=True)
    assert missing_required.valid is False
    assert missing_required.required is True
    assert "manifest.json not found" in missing_required.errors[0]

    (tmp_path / "manifest.json").write_text("{bad json", encoding="utf-8")
    invalid_json = validate_standard_versioning_manifest(tmp_path)
    assert invalid_json.valid is False
    assert "manifest.json is invalid" in invalid_json.errors[0]

    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")
    not_object = validate_standard_versioning_manifest(tmp_path)
    assert not_object.valid is False
    assert not_object.errors == ("manifest.json must be a JSON object",)

    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    optional_missing_block = validate_standard_versioning_manifest(tmp_path)
    assert optional_missing_block.valid is True
    assert optional_missing_block.present is False

    required_missing_block = validate_standard_versioning_manifest(
        tmp_path, require=True
    )
    assert required_missing_block.valid is False
    assert "missing standard_versioning" in required_missing_block.errors[0]

    (tmp_path / "manifest.json").write_text(
        json.dumps({"standard_versioning": "not-an-object"}),
        encoding="utf-8",
    )
    bad_block = validate_standard_versioning_manifest(tmp_path)
    assert bad_block.present is True
    assert bad_block.valid is False
    assert bad_block.errors == ("standard_versioning must be a JSON object",)


def test_validate_standard_versioning_manifest_valid_and_invalid_content(tmp_path):
    manifest = {"standard_versioning": _valid_standard_versioning()}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    valid = validate_standard_versioning_manifest(tmp_path, require=True)
    assert valid.as_dict() == {
        "present": True,
        "required": True,
        "valid": True,
        "standard_id": "ESRS",
        "release_id": "ESRS_SET1_2023_12_22",
        "release_state": "active",
        "errors": [],
    }

    broken = _valid_standard_versioning()
    broken["schema_version"] = "future"
    broken["release_manifest"]["release_state"] = "unknown"
    broken["release_manifest"]["source_hash"] = "not-a-sha"
    broken["release_manifest"]["source_hash_set"] = {"bad": "also-bad"}
    broken["delta_manifest"]["transition_kind"] = "silent_replace"
    broken["affected_scope_plan"]["scope_states"] = []
    broken["datapoint_lineage_map"] = ["bad-item", {}]
    broken["tenant_activation_policy"] = {
        "dry_run_required": False,
        "real_import_requires_explicit_approval": False,
    }
    (tmp_path / "manifest.json").write_text(
        json.dumps({"standard_versioning": broken}),
        encoding="utf-8",
    )

    invalid = validate_standard_versioning_manifest(tmp_path)

    assert invalid.present is True
    assert invalid.valid is False
    assert len(invalid.errors) >= 10
    assert any("schema_version" in error for error in invalid.errors)
    assert any("release_state" in error for error in invalid.errors)
    assert any("datapoint_lineage_map[1]" in error for error in invalid.errors)


def test_validate_standard_versioning_manifest_missing_required_nested_fields(tmp_path):
    broken = _valid_standard_versioning()
    broken["release_manifest"].pop("standard_id")
    broken["release_manifest"].pop("release_id")
    broken["release_manifest"].pop("source_hash")
    broken["release_manifest"]["source_hash_set"] = {}
    broken["delta_manifest"] = []
    broken["affected_scope_plan"] = []
    broken["datapoint_lineage_map"] = {}

    (tmp_path / "manifest.json").write_text(
        json.dumps({"standard_versioning": broken}),
        encoding="utf-8",
    )

    invalid = validate_standard_versioning_manifest(tmp_path)

    assert invalid.valid is False
    assert any("release_manifest.standard_id is required" in e for e in invalid.errors)
    assert any("release_manifest.release_id is required" in e for e in invalid.errors)
    assert any("release_manifest.source_hash is required" in e for e in invalid.errors)
    assert any(
        "source_hash_set must be a non-empty object" in e for e in invalid.errors
    )
    assert any("delta_manifest must be a JSON object" in e for e in invalid.errors)
    assert any("affected_scope_plan must be a JSON object" in e for e in invalid.errors)
    assert any("datapoint_lineage_map must be a list" in e for e in invalid.errors)
