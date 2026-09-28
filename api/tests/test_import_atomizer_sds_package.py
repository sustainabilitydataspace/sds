from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "import_atomizer_sds_package.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "import_atomizer_sds_package", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


package_import = _load_module()
FIXTURE_ROOT = REPO_ROOT / "api" / "tests" / "fixtures" / "atomizer_packages"


def _write_standard_versioning_manifest(package_dir: Path) -> None:
    register = package_dir / "sds_dataset_register.csv"
    digest = _sha256(register)
    manifest = {
        "files": [
            {
                "filename": register.name,
                "path": register.name,
                "sha256": digest,
            }
        ],
        "standard_versioning": {
            "schema_version": "standard-versioning-v1",
            "release_manifest": {
                "standard_id": "ESRS",
                "release_id": "ESRS_SET1_2023_12_22",
                "official_name": "ESRS Set 1 delegated act",
                "publisher": "European Commission",
                "release_state": "active",
                "source_hash": "a" * 64,
                "source_hash_set": {"framework_datapoints": "b" * 64},
            },
            "delta_manifest": {"transition_kind": "initial_snapshot"},
            "affected_scope_plan": {"scope_states": {"current": 1}},
            "datapoint_lineage_map": [
                {
                    "sds_identifier": "urn:sds:reg:esrs:e2_5_01",
                    "standard_id": "ESRS",
                    "release_id": "ESRS_SET1_2023_12_22",
                    "datapoint_id": "E2-5_01",
                    "concept_id": "ESRS:E2-5_01",
                    "relationship": "maps_to_sds_identifier",
                }
            ],
            "tenant_activation_policy": {
                "dry_run_required": True,
                "real_import_requires_explicit_approval": True,
            },
        },
    }
    package_dir.joinpath("manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
    )
    package_dir.joinpath("MANIFEST.sha256").write_text(
        f"{digest} *{register.name}\n", encoding="utf-8"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_snapshot_member(
    command: list[str], flag: str, expected_name: str, original: Path
) -> None:
    value = Path(command[command.index(flag) + 1])
    assert value.name == expected_name
    assert value != original.resolve()
    assert "sds-atomizer-package-" in str(value)


def _write_package_integrity_files(package_dir: Path, filenames: list[str]) -> None:
    files = []
    checksum_lines = []
    for filename in filenames:
        digest = _sha256(package_dir / filename)
        files.append({"filename": filename, "path": filename, "sha256": digest})
        checksum_lines.append(f"{digest} *{filename}")
    package_dir.joinpath("manifest.json").write_text(
        '{"files":['
        + ",".join(
            '{"filename":"'
            + item["filename"]
            + '","path":"'
            + item["path"]
            + '","sha256":"'
            + item["sha256"]
            + '"}'
            for item in files
        )
        + "]}",
        encoding="utf-8",
    )
    package_dir.joinpath("MANIFEST.sha256").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="utf-8",
    )


def test_resolve_package_file_prefers_sds_values_csv(tmp_path):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    expected = package_dir / "sds_values.csv"
    expected.write_text("concept,entity,period,value,unit\n", encoding="utf-8")

    resolved = package_import.resolve_package_file(
        package_dir, None, package_import.VALUE_FILENAMES
    )

    assert resolved == expected.resolve()


def test_resolve_package_file_accepts_explicit_package_relative_path(tmp_path):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    expected = package_dir / "custom_values.csv"
    expected.write_text("concept,entity,period,value,unit\n", encoding="utf-8")

    resolved = package_import.resolve_package_file(
        package_dir,
        Path("custom_values.csv"),
        package_import.VALUE_FILENAMES,
    )

    assert resolved == expected.resolve()


def test_main_imports_indicators_and_skips_missing_optional_values(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 0
    assert len(calls) == 1
    assert str(package_import.INDICATOR_IMPORT_SCRIPT) in calls[0]
    assert "--dry-run" in calls[0]
    _assert_snapshot_member(calls[0], "--csv", register_csv.name, register_csv)


def test_main_bounds_import_subprocess_runtime(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    timeouts: list[int] = []

    def fake_call(command, cwd, timeout):
        timeouts.append(timeout)
        return 0

    monkeypatch.setattr(package_import.subprocess, "call", fake_call)

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 0
    assert timeouts == [900]


def test_main_rejects_manifestless_real_import_before_any_subprocess(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "sds_dataset_register.csv").write_text(
        "identifier,title\n", encoding="utf-8"
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir)])

    assert result == 1
    assert calls == []


def test_main_rejects_symlink_and_hardlink_package_members(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("identifier,title\n", encoding="utf-8")
    register = package_dir / "sds_dataset_register.csv"
    register.symlink_to(outside)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    assert package_import.main(["--package-dir", str(package_dir), "--dry-run"]) == 1
    register.unlink()
    os.link(outside, register)
    assert package_import.main(["--package-dir", str(package_dir), "--dry-run"]) == 1
    assert calls == []


def test_main_rejects_explicit_member_outside_package(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("identifier,title\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        [
            "--package-dir",
            str(package_dir),
            "--register-csv",
            str(outside),
            "--dry-run",
        ]
    )

    assert result == 1
    assert calls == []


def test_main_subprocesses_consume_snapshot_when_source_changes_after_validation(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register = package_dir / "sds_dataset_register.csv"
    original_bytes = b"identifier,title\nurn:sds:test,Original\n"
    register.write_bytes(original_bytes)
    _write_package_integrity_files(package_dir, [register.name])
    validate_integrity = package_import.validate_package_integrity

    def validate_then_mutate(*args, **kwargs):
        validate_integrity(*args, **kwargs)
        register.write_text(
            "identifier,title\nurn:sds:test,Mutated\n", encoding="utf-8"
        )

    def inspect_command(command, cwd, timeout):
        csv_path = Path(command[command.index("--csv") + 1])
        assert csv_path.read_bytes() == original_bytes
        return 0

    monkeypatch.setattr(
        package_import, "validate_package_integrity", validate_then_mutate
    )
    monkeypatch.setattr(package_import.subprocess, "call", inspect_command)

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 0


def test_main_rejects_duplicate_checksum_entries_before_any_subprocess(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register = package_dir / "sds_dataset_register.csv"
    register.write_text("identifier,title\n", encoding="utf-8")
    _write_package_integrity_files(package_dir, [register.name])
    checksum = package_dir / "MANIFEST.sha256"
    first = checksum.read_text(encoding="utf-8").splitlines()[0]
    checksum.write_text(
        checksum.read_text(encoding="utf-8") + first + "\n", encoding="utf-8"
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 1
    assert calls == []


def test_main_rejects_noncanonical_manifest_digest_before_any_subprocess(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register = package_dir / "sds_dataset_register.csv"
    register.write_text("identifier,title\n", encoding="utf-8")
    _write_package_integrity_files(package_dir, [register.name])
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = manifest["files"][0]["sha256"].upper()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 1
    assert calls == []


def test_main_verifies_every_declared_payload_even_when_import_step_is_skipped(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    payload = package_dir / "unused-evidence.json"
    payload.write_text('{"status":"original"}', encoding="utf-8")
    _write_package_integrity_files(package_dir, [payload.name])
    payload.write_text('{"status":"tampered"}', encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        [
            "--package-dir",
            str(package_dir),
            "--dry-run",
            "--skip-indicators",
            "--skip-values",
            "--skip-calculation-contract",
        ]
    )

    assert result == 1
    assert calls == []


def test_main_requires_package_dir_when_env_is_unset(monkeypatch):
    calls: list[list[str]] = []

    monkeypatch.delenv(package_import.PACKAGE_DIR_ENV, raising=False)
    monkeypatch.delenv(package_import.LEGACY_PACKAGE_DIR_ENV, raising=False)
    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--dry-run"])

    assert result == 1
    assert calls == []


def test_main_rejects_manifest_checksum_mismatch_before_any_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    register_csv.write_text(
        "identifier,title\nurn:sds:reg:test:water,Water\n", encoding="utf-8"
    )
    _write_package_integrity_files(package_dir, ["sds_dataset_register.csv"])
    register_csv.write_text(
        "identifier,title\nurn:sds:reg:test:water,Changed title\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 1
    assert calls == []


def test_main_accepts_package_dir_from_env(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setenv(package_import.PACKAGE_DIR_ENV, str(package_dir))
    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--dry-run"])

    assert result == 0
    assert len(calls) == 1
    _assert_snapshot_member(calls[0], "--csv", register_csv.name, register_csv)


def test_main_accepts_package_dir_from_legacy_env(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.delenv(package_import.PACKAGE_DIR_ENV, raising=False)
    monkeypatch.setenv(package_import.LEGACY_PACKAGE_DIR_ENV, str(package_dir))
    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--dry-run"])

    assert result == 0
    assert len(calls) == 1
    _assert_snapshot_member(calls[0], "--csv", register_csv.name, register_csv)


def test_main_requires_standard_versioning_before_any_import(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        [
            "--package-dir",
            str(package_dir),
            "--dry-run",
            "--require-standard-versioning",
        ]
    )

    assert result == 1
    assert calls == []


def test_main_accepts_standard_versioning_manifest(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    register_csv.write_text(
        "identifier,title\nurn:sds:reg:esrs:e2_5_01,Substances\n",
        encoding="utf-8",
    )
    _write_standard_versioning_manifest(package_dir)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        [
            "--package-dir",
            str(package_dir),
            "--dry-run",
            "--require-standard-versioning",
        ]
    )

    assert result == 0
    assert len(calls) == 2
    # F04 M2: the indicator catalog imports FIRST so a failed register does not
    # leave committed standard-versioning metadata behind; versioning runs second.
    assert str(package_import.INDICATOR_IMPORT_SCRIPT) in calls[0]
    _assert_snapshot_member(calls[0], "--csv", register_csv.name, register_csv)
    assert str(package_import.STANDARD_VERSIONING_IMPORT_SCRIPT) in calls[1]
    _assert_snapshot_member(calls[1], "--package-dir", "package", package_dir)
    _assert_snapshot_member(calls[1], "--register-csv", register_csv.name, register_csv)


def test_main_can_skip_standard_versioning_import(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    register_csv.write_text(
        "identifier,title\nurn:sds:reg:esrs:e2_5_01,Substances\n",
        encoding="utf-8",
    )
    _write_standard_versioning_manifest(package_dir)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        [
            "--package-dir",
            str(package_dir),
            "--dry-run",
            "--require-standard-versioning",
            "--skip-standard-versioning",
        ]
    )

    assert result == 0
    assert len(calls) == 1
    assert str(package_import.INDICATOR_IMPORT_SCRIPT) in calls[0]
    _assert_snapshot_member(calls[0], "--csv", register_csv.name, register_csv)


def test_main_imports_values_when_package_contains_values_csv(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    values_csv = package_dir / "sds_values.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    values_csv.write_text(
        "concept,entity,period,value,unit\n"
        "syg:Water_Cooling,test_company,2024-01-15,1.5,m3\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        [
            "--package-dir",
            str(package_dir),
            "--dry-run",
            "--default-entity",
            "test_company",
        ]
    )

    assert result == 0
    assert len(calls) == 2
    assert str(package_import.INDICATOR_IMPORT_SCRIPT) in calls[0]
    assert str(package_import.VALUE_IMPORT_SCRIPT) in calls[1]
    _assert_snapshot_member(calls[1], "--csv", values_csv.name, values_csv)
    assert "--default-entity" in calls[1]
    assert "test_company" in calls[1]
    assert "--dry-run" in calls[1]
    assert "--batch-size" not in calls[1]
    assert "--tenant-id" not in calls[1]


def test_main_rejects_real_values_import_without_tenant_before_any_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    values_csv = package_dir / "sds_values.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    values_csv.write_text(
        "concept,entity,period,value,unit\n"
        "syg:Water_Cooling,test_company,2024-01-15,1.5,m3\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir)])

    assert result == 1
    assert calls == []


def test_main_rejects_real_values_import_with_placeholder_tenant_before_any_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    values_csv = package_dir / "sds_values.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    values_csv.write_text(
        "concept,entity,period,value,unit\n"
        "syg:Water_Cooling,test_company,2024-01-15,1.5,m3\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        ["--package-dir", str(package_dir), "--tenant-id", "default"]
    )

    assert result == 1
    assert calls == []


def test_main_forwards_tenant_only_to_real_values_import(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    values_csv = package_dir / "sds_values.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    values_csv.write_text(
        "concept,entity,period,value,unit\n"
        "syg:Water_Cooling,test_company,2024-01-15,1.5,m3\n",
        encoding="utf-8",
    )
    _write_package_integrity_files(package_dir, [register_csv.name, values_csv.name])
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        ["--package-dir", str(package_dir), "--tenant-id", "tenant_acme"]
    )

    assert result == 0
    assert len(calls) == 2
    assert "--tenant-id" not in calls[0]
    assert str(package_import.VALUE_IMPORT_SCRIPT) in calls[1]
    assert calls[1][calls[1].index("--tenant-id") + 1] == "tenant_acme"


def test_main_forwards_values_batch_size_only_to_values_import(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    values_csv = package_dir / "sds_values.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    values_csv.write_text(
        "concept,entity,period,value,unit\n"
        "syg:Water_Cooling,test_company,2024-01-15,1.5,m3\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        [
            "--package-dir",
            str(package_dir),
            "--dry-run",
            "--batch-size",
            "17",
            "--values-batch-size",
            "43",
        ]
    )

    assert result == 0
    assert len(calls) == 2
    assert str(package_import.INDICATOR_IMPORT_SCRIPT) in calls[0]
    assert str(package_import.VALUE_IMPORT_SCRIPT) in calls[1]
    assert calls[0][calls[0].index("--batch-size") + 1] == "17"
    assert calls[1][calls[1].index("--batch-size") + 1] == "43"


def test_main_rejects_invalid_values_batch_size_before_any_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    values_csv = package_dir / "sds_values.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    values_csv.write_text(
        "concept,entity,period,value,unit\n"
        "syg:Water_Cooling,test_company,2024-01-15,1.5,m3\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        ["--package-dir", str(package_dir), "--values-batch-size", "0"]
    )

    assert result == 1
    assert calls == []


def test_main_rejects_invalid_indicator_batch_size_before_any_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        ["--package-dir", str(package_dir), "--batch-size", "0"]
    )

    assert result == 1
    assert calls == []


def test_main_blocks_real_retire_prefix_without_impact_approval(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    package_dir.joinpath("sds_dataset_register.csv").write_text(
        "identifier,title\nurn:sds:reg:ghg:scope1,Scope 1\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        [
            "--package-dir",
            str(package_dir),
            "--retire-indicator-prefix",
            "urn:sds:reg:ghg:",
        ]
    )

    assert result == 1
    assert calls == []


def test_main_forwards_retirement_impact_approval_to_indicator_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    package_dir.joinpath("sds_dataset_register.csv").write_text(
        "identifier,title\nurn:sds:reg:ghg:scope1,Scope 1\n",
        encoding="utf-8",
    )
    _write_package_integrity_files(package_dir, ["sds_dataset_register.csv"])
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        [
            "--package-dir",
            str(package_dir),
            "--retire-indicator-prefix",
            "urn:sds:reg:ghg:",
            "--approve-retirement-impact",
            "urn:sds:reg:ghg:=247",
        ]
    )

    assert result == 0
    assert len(calls) == 1
    assert "--retire-prefix" in calls[0]
    assert "urn:sds:reg:ghg:" in calls[0]
    assert "--approve-retirement-impact" in calls[0]
    assert "urn:sds:reg:ghg:=247" in calls[0]


def test_main_rejects_invalid_values_conversion_metadata_before_any_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    values_csv = package_dir / "sds_values.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    values_csv.write_text(
        "concept,entity,period,value,unit,currency,expected_currency,fx_policy_id\n"
        "finance:revenue,test_company,2024-12-31,100,gbp,gbp,eur,ecb-reference-monthly-average\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 1
    assert calls == []


def test_main_rejects_partial_value_period_window_before_any_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    values_csv = package_dir / "sds_values.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    values_csv.write_text(
        "concept,entity,period,value,unit,period_start\n"
        "finance:revenue,test_company,2024-12-31,100,gbp,2024-01-01\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 1
    assert calls == []


def test_main_imports_calculation_contract_between_register_and_values(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    contract_json = package_dir / "sds_calculation_contract.json"
    values_csv = package_dir / "sds_values.csv"
    register_csv.write_text(
        "identifier,title\nurn:sds:reg:test:weighted_training_score,Weighted score\n",
        encoding="utf-8",
    )
    contract_json.write_text(
        (
            '{"contract_version":"1.0","nodes":[{'
            '"node_id":"n1",'
            '"model_id":"m1",'
            '"indicator_identifier":"urn:sds:reg:test:weighted_training_score",'
            '"exposure":"public_register",'
            '"runtime_status":"audit_only"'
            "}]}"
        ),
        encoding="utf-8",
    )
    values_csv.write_text(
        "concept,entity,period,value,unit\n"
        "syg:Weighted_Score,test_company,2024-01-15,1.5,score\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 0
    assert len(calls) == 3
    assert str(package_import.INDICATOR_IMPORT_SCRIPT) in calls[0]
    assert str(package_import.CALCULATION_CONTRACT_IMPORT_SCRIPT) in calls[1]
    assert str(package_import.VALUE_IMPORT_SCRIPT) in calls[2]
    _assert_snapshot_member(calls[1], "--json", contract_json.name, contract_json)
    assert "--dry-run" in calls[1]
    assert "--known-register-csv" in calls[1]
    _assert_snapshot_member(
        calls[1], "--known-register-csv", register_csv.name, register_csv
    )
    assert "--retirement-scope" in calls[1]
    assert "incoming_keys" in calls[1]
    assert "--known-register-csv" in calls[2]
    _assert_snapshot_member(
        calls[2], "--known-register-csv", register_csv.name, register_csv
    )
    assert "--known-calculation-contract-json" in calls[2]
    _assert_snapshot_member(
        calls[2],
        "--known-calculation-contract-json",
        contract_json.name,
        contract_json,
    )

    calls.clear()
    result = package_import.main(
        [
            "--package-dir",
            str(package_dir),
            "--dry-run",
            "--calculation-contract-retirement-scope",
            "incoming_models",
        ]
    )

    assert result == 0
    assert "--retirement-scope" in calls[1]
    assert "incoming_models" in calls[1]


def test_main_accepts_conversion_contract_fixture_before_import(monkeypatch):
    package_dir = FIXTURE_ROOT / "conversion-contract-v1"
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 0
    assert len(calls) == 3
    assert str(package_import.INDICATOR_IMPORT_SCRIPT) in calls[0]
    assert str(package_import.CALCULATION_CONTRACT_IMPORT_SCRIPT) in calls[1]
    assert str(package_import.VALUE_IMPORT_SCRIPT) in calls[2]
    assert "--known-register-csv" in calls[1]
    _assert_snapshot_member(
        calls[1],
        "--known-register-csv",
        "sds_dataset_register.csv",
        package_dir / "sds_dataset_register.csv",
    )
    assert "--known-register-csv" in calls[2]
    _assert_snapshot_member(
        calls[2],
        "--known-register-csv",
        "sds_dataset_register.csv",
        package_dir / "sds_dataset_register.csv",
    )
    assert "--known-calculation-contract-json" in calls[2]
    _assert_snapshot_member(
        calls[2],
        "--known-calculation-contract-json",
        "sds_calculation_contract.json",
        package_dir / "sds_calculation_contract.json",
    )


def test_main_does_not_pass_known_register_csv_to_values_on_real_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    values_csv = package_dir / "sds_values.csv"
    register_csv.write_text(
        "identifier,title\nurn:sds:reg:test:water,Water\n",
        encoding="utf-8",
    )
    values_csv.write_text(
        "concept,entity,period,value,unit\n"
        "urn:sds:reg:test:water,madrid_plant,2024-01-15,1.5,m3\n",
        encoding="utf-8",
    )
    _write_package_integrity_files(package_dir, [register_csv.name, values_csv.name])
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        ["--package-dir", str(package_dir), "--tenant-id", "tenant_acme"]
    )

    assert result == 0
    assert len(calls) == 2
    assert str(package_import.VALUE_IMPORT_SCRIPT) in calls[1]
    assert "--known-register-csv" not in calls[1]
    assert "--known-calculation-contract-json" not in calls[1]


def test_main_rejects_invalid_contract_exposure_before_any_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    contract_json = package_dir / "sds_calculation_contract.json"
    register_csv.write_text(
        "identifier,title\nurn:sds:reg:test:revenue,Revenue\n", encoding="utf-8"
    )
    contract_json.write_text(
        (
            '{"contract_version":"1.0","nodes":[{'
            '"node_id":"n1",'
            '"model_id":"m1",'
            '"indicator_identifier":"urn:sds:reg:test:revenue",'
            '"exposure":"public",'
            '"runtime_status":"audit_only"'
            "}]}"
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 1
    assert calls == []


def test_main_rejects_invalid_contract_runtime_status_before_any_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    contract_json = package_dir / "sds_calculation_contract.json"
    register_csv.write_text(
        "identifier,title\nurn:sds:reg:test:revenue,Revenue\n", encoding="utf-8"
    )
    contract_json.write_text(
        (
            '{"contract_version":"1.0","nodes":[{'
            '"node_id":"n1",'
            '"model_id":"m1",'
            '"indicator_identifier":"urn:sds:reg:test:revenue",'
            '"exposure":"public_register",'
            '"runtime_status":"live"'
            "}]}"
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 1
    assert calls == []


def test_main_rejects_invalid_calculation_conversion_metadata_before_any_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    contract_json = package_dir / "sds_calculation_contract.json"
    register_csv.write_text(
        "identifier,title\nurn:sds:reg:test:revenue,Revenue\n", encoding="utf-8"
    )
    contract_json.write_text(
        (
            '{"contract_version":"1.0","nodes":[{'
            '"node_id":"n1",'
            '"model_id":"m1",'
            '"indicator_identifier":"urn:sds:reg:test:revenue",'
            '"exposure":"public_register",'
            '"runtime_status":"executable",'
            '"formula":{'
            '"kind":"formula",'
            '"runtime_expression":"revenue",'
            '"component_ids":["revenue"],'
            '"component_refs":[{'
            '"component_id":"revenue",'
            '"variable_uri":"input.revenue",'
            '"expected_currency":"EUR"'
            "}]}"
            "}]}"
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 1
    assert calls == []


def test_prevalidate_allows_runtime_external_component_target(tmp_path):
    contract_json = tmp_path / "sds_calculation_contract.json"
    contract_json.write_text(
        (
            '{"contract_version":"1.0","nodes":[{'
            '"node_id":"n1",'
            '"model_id":"m1",'
            '"indicator_identifier":"urn:sds:reg:test:water_intensity",'
            '"exposure":"public_register",'
            '"runtime_status":"executable",'
            '"formula":{'
            '"kind":"ratio",'
            '"runtime_expression":"water / net_revenue_million_eur",'
            '"component_ids":["water","input.net_revenue_million_eur"],'
            '"component_refs":['
            '{"component_id":"water","node_id":"n2","indicator_identifier":""},'
            '{"component_id":"input.net_revenue_million_eur",'
            '"node_id":"",'
            '"indicator_identifier":"",'
            '"variable_uri":"input.net_revenue_million_eur"}'
            "]"
            "}"
            "},{"
            '"node_id":"n2",'
            '"model_id":"m1",'
            '"indicator_identifier":"urn:sds:reg:test:water",'
            '"exposure":"public_register",'
            '"runtime_status":"semantic_only"'
            "}]}"
        ),
        encoding="utf-8",
    )

    package_import.prevalidate_calculation_contract_json(
        contract_json,
        register_identifiers={
            "urn:sds:reg:test:water_intensity",
            "urn:sds:reg:test:water",
        },
    )


def test_prevalidate_rejects_component_expected_currency_without_fx_policy(tmp_path):
    contract_json = tmp_path / "sds_calculation_contract.json"
    contract_json.write_text(
        (
            '{"contract_version":"1.0","nodes":[{'
            '"node_id":"n1",'
            '"model_id":"m1",'
            '"indicator_identifier":"urn:sds:reg:test:revenue",'
            '"exposure":"public_register",'
            '"runtime_status":"executable",'
            '"formula":{'
            '"kind":"formula",'
            '"runtime_expression":"revenue",'
            '"component_ids":["revenue"],'
            '"component_refs":['
            '{"component_id":"revenue",'
            '"variable_uri":"input.revenue",'
            '"expected_currency":"EUR"}'
            "]"
            "}"
            "}]}"
        ),
        encoding="utf-8",
    )

    try:
        package_import.prevalidate_calculation_contract_json(
            contract_json,
            register_identifiers={"urn:sds:reg:test:revenue"},
        )
    except ValueError as exc:
        assert "expected_currency requires fx_policy_id" in str(exc)
    else:
        raise AssertionError("expected_currency without fx_policy_id was accepted")


def test_prevalidate_rejects_invalid_conversion_policy_and_currency(tmp_path):
    contract_json = tmp_path / "sds_calculation_contract.json"
    contract_json.write_text(
        (
            '{"contract_version":"1.0","nodes":[{'
            '"node_id":"n1",'
            '"model_id":"m1",'
            '"indicator_identifier":"urn:sds:reg:test:revenue",'
            '"exposure":"public_register",'
            '"runtime_status":"executable",'
            '"result_currency":"EURO",'
            '"conversion_policy":"latest_available",'
            '"formula":{'
            '"kind":"formula",'
            '"runtime_expression":"revenue",'
            '"component_ids":["revenue"],'
            '"component_refs":['
            '{"component_id":"revenue",'
            '"variable_uri":"input.revenue",'
            '"currency":"GB1",'
            '"expected_currency":"EUR",'
            '"fx_policy_id":"ecb-monthly"}'
            "]"
            "}"
            "}]}"
        ),
        encoding="utf-8",
    )

    try:
        package_import.prevalidate_calculation_contract_json(
            contract_json,
            register_identifiers={"urn:sds:reg:test:revenue"},
        )
    except ValueError as exc:
        message = str(exc)
        assert (
            "unsupported conversion_policy" in message or "invalid currency" in message
        )
    else:
        raise AssertionError("invalid currency/conversion policy was accepted")


def test_main_requires_calculation_contract_before_running_any_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        ["--package-dir", str(package_dir), "--semantics-mode", "required"]
    )

    assert result == 1
    assert calls == []


def test_main_rejects_invalid_calculation_contract_before_running_any_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    contract_json = package_dir / "sds_calculation_contract.json"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    contract_json.write_text("{not-json", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir)])

    assert result == 1
    assert calls == []


def test_main_rejects_unknown_public_contract_indicator_before_import(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    contract_json = package_dir / "sds_calculation_contract.json"
    register_csv.write_text(
        "identifier,title\nurn:sds:reg:known,Known\n", encoding="utf-8"
    )
    contract_json.write_text(
        (
            '{"contract_version":"1.0","nodes":[{'
            '"node_id":"n1",'
            '"model_id":"m1",'
            '"indicator_identifier":"urn:sds:reg:missing",'
            '"exposure":"public_register",'
            '"runtime_status":"audit_only"'
            "}]}"
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(["--package-dir", str(package_dir)])

    assert result == 1
    assert calls == []


def test_main_semantics_mode_off_skips_calculation_contract(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    contract_json = package_dir / "sds_calculation_contract.json"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    contract_json.write_text(
        '{"contract_version":"1.0","nodes":[]}\n', encoding="utf-8"
    )
    _write_package_integrity_files(package_dir, [register_csv.name, contract_json.name])
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        ["--package-dir", str(package_dir), "--semantics-mode", "off"]
    )

    assert result == 0
    assert len(calls) == 1
    assert str(package_import.INDICATOR_IMPORT_SCRIPT) in calls[0]


def test_main_requires_values_before_running_any_import(tmp_path, monkeypatch):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    register_csv.write_text("identifier,title\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(
        package_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = package_import.main(
        ["--package-dir", str(package_dir), "--require-values"]
    )

    assert result == 1
    assert calls == []
