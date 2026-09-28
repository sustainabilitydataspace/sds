from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "run_e6_gate.py"
spec = importlib.util.spec_from_file_location("run_e6_gate", MODULE_PATH)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

pytestmark = pytest.mark.docs_only

EXPECTED_REGISTER_HEADERS = [
    "identifier",
    "title",
    "indicator",
    "description",
    "dimension",
    "unitName",
    "unitType",
    "periodicity",
    "periodType",
    "sourceRef",
    "codeESRS",
    "codeGRI",
    "codeGRI_expanded",
    "evidencePath",
    "sourceRow",
    "owner",
    "accessRights",
    "validationMethod",
    "doubleMateriality",
    "valueType",
    "purpose",
    "region",
    "policyId",
]


def _test_input_lock_path(package_dir: Path) -> Path:
    return package_dir.parent / f"{package_dir.name}-register-input-lock.json"


def _expected_commands(register_csv: Path | None = None):
    checker_command = [sys.executable, "scripts/e6_check_governance.py", "--strict"]
    if register_csv is not None:
        checker_command.extend(["--register-csv", str(register_csv.resolve())])
    return [
        (
            [
                sys.executable,
                "-m",
                "pytest",
                "api/tests/test_e6_governance_pack.py",
                "-q",
            ],
            REPO_ROOT,
        ),
        (
            [sys.executable, "-m", "pytest", "tests/test_policy_enforcement.py", "-q"],
            REPO_ROOT / "api",
        ),
        (
            [
                sys.executable,
                "-m",
                "pytest",
                "api/tests/test_edc_bundle_input_security.py",
                "-q",
            ],
            REPO_ROOT,
        ),
        (
            [
                sys.executable,
                "-m",
                "pytest",
                "api/tests/test_e6_check_governance.py",
                "-q",
            ],
            REPO_ROOT,
        ),
        (checker_command, REPO_ROOT),
    ]


def _write_manifest_bound_register_package(package_dir: Path) -> Path:
    package_dir.mkdir()
    register_csv = package_dir / "sds_dataset_register.csv"
    row = {header: f"value-{header}" for header in EXPECTED_REGISTER_HEADERS}
    row.update(
        {
            "identifier": "urn:sds:reg:test:water",
            "title": "Water",
            "policyId": "policy-reporting-365d-retention",
        }
    )
    with register_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_REGISTER_HEADERS)
        writer.writeheader()
        writer.writerow(row)
    register_json = package_dir / "sds_dataset_register.json"
    register_json.write_text(json.dumps([row]), encoding="utf-8")
    payloads = (register_csv, register_json)
    digests = {
        payload.name: hashlib.sha256(payload.read_bytes()).hexdigest()
        for payload in payloads
    }
    manifest = {
        "contract_version": "atomizer-sds-register-package-v1",
        "contract_mode": "register",
        "row_count": 1,
        "sha256": digests[register_csv.name],
        "source_project": "atomizer",
        "source_version": "test-source",
        "source_ref": "test-source",
        "framework_filter": "",
        "policy": {
            "canonical_inputs": [register_csv.name],
            "preferred_boundary": register_csv.name,
        },
        "sync": {"atomizer_project_version": "test-source"},
        "files": [
            {
                "filename": payload.name,
                "size_bytes": payload.stat().st_size,
                "sha256": digests[payload.name],
                "row_count": 1 if payload == register_csv else 0,
                "required_headers": (
                    EXPECTED_REGISTER_HEADERS if payload == register_csv else []
                ),
                "headers": EXPECTED_REGISTER_HEADERS if payload == register_csv else [],
            }
            for payload in payloads
        ],
    }
    (package_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (package_dir / "MANIFEST.sha256").write_text(
        "".join(f"{digests[payload.name]} *{payload.name}\n" for payload in payloads),
        encoding="utf-8",
    )
    manifest_bytes = (package_dir / "manifest.json").read_bytes()
    checksum_bytes = (package_dir / "MANIFEST.sha256").read_bytes()
    _test_input_lock_path(package_dir).write_text(
        json.dumps(
            {
                "schema_version": "sds-e6-register-input-lock-v1",
                "contract_version": "atomizer-sds-register-package-v1",
                "source_project": "atomizer",
                "source_version": "test-source",
                "row_count": 1,
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "manifest_size_bytes": len(manifest_bytes),
                "checksum_sha256": hashlib.sha256(checksum_bytes).hexdigest(),
                "checksum_size_bytes": len(checksum_bytes),
                "payloads": {
                    payload.name: {
                        "sha256": digests[payload.name],
                        "size_bytes": payload.stat().st_size,
                    }
                    for payload in payloads
                },
                "ancillary_payloads": {},
            }
        ),
        encoding="utf-8",
    )
    return register_csv


def _refresh_package_integrity(package_dir: Path, *, row_count: int) -> None:
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["row_count"] = row_count
    checksum_lines = []
    for item in manifest["files"]:
        payload = package_dir / item["filename"]
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        item["sha256"] = digest
        item["size_bytes"] = payload.stat().st_size
        if payload.name == "sds_dataset_register.csv":
            item["row_count"] = row_count
            manifest["sha256"] = digest
        checksum_lines.append(f"{digest} *{payload.name}\n")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (package_dir / "MANIFEST.sha256").write_text(
        "".join(checksum_lines), encoding="utf-8"
    )


def _bind_test_input_lock(monkeypatch, package_dir: Path) -> None:
    monkeypatch.setattr(
        gate,
        "REGISTER_INPUT_LOCK_PATH",
        _test_input_lock_path(package_dir),
        raising=False,
    )


def test_register_schema_is_owned_by_sds_not_self_declared(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    csv_entry = next(
        item
        for item in manifest["files"]
        if item["filename"] == "sds_dataset_register.csv"
    )
    csv_entry["required_headers"] = ["identifier", "title"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="required_headers do not match SDS contract"):
        gate.resolve_register_package(package_dir)


def test_resolver_accepts_separately_controlled_input_lock(tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)

    assert (
        gate.resolve_register_package(
            package_dir,
            lock_path=_test_input_lock_path(package_dir),
        )
        == register_csv.resolve()
    )


def test_resolver_rejects_symlinked_input_lock(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    input_lock = _test_input_lock_path(package_dir)
    target_lock = package_dir.parent / "register-input-lock-target.json"
    target_lock.write_bytes(input_lock.read_bytes())
    input_lock.unlink()
    input_lock.symlink_to(target_lock.name)

    with pytest.raises(ValueError, match="input lock must be a regular file"):
        gate.resolve_register_package(package_dir, lock_path=input_lock)


def test_resolver_rejects_oversized_input_lock(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    monkeypatch.setattr(gate, "MAX_INPUT_LOCK_BYTES", 32, raising=False)

    with pytest.raises(ValueError, match="input lock exceeds size limit"):
        gate.resolve_register_package(
            package_dir,
            lock_path=_test_input_lock_path(package_dir),
        )


def test_input_lock_rejects_self_rehashed_different_register(tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    with register_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["description"] = "self-rehashed different description"
    with register_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_REGISTER_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    _refresh_package_integrity(package_dir, row_count=1)

    with pytest.raises(ValueError, match="package does not match SDS input lock"):
        gate.resolve_register_package(
            package_dir,
            lock_path=_test_input_lock_path(package_dir),
        )


def test_input_lock_must_bind_optional_summary_when_present(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    (package_dir / "summary.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="package does not match SDS input lock"):
        gate.resolve_register_package(
            package_dir,
            lock_path=_test_input_lock_path(package_dir),
        )


def test_validated_register_is_snapshotted_outside_external_package(tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    validation = gate.resolve_register_package(
        package_dir,
        lock_path=_test_input_lock_path(package_dir),
        return_validation=True,
    )
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    snapshot = gate.snapshot_locked_register(
        validation,
        snapshot_dir=snapshot_dir,
    )

    assert snapshot.register_csv == snapshot_dir / "sds_dataset_register.csv"
    assert snapshot.register_csv.read_bytes() == register_csv.read_bytes()
    assert snapshot.sha256 == validation.register_sha256
    assert snapshot.size_bytes == validation.register_size_bytes


@pytest.mark.parametrize("rebound_target", ["register", "lock"])
def test_snapshot_rejects_same_byte_regular_file_rebound_after_validation(
    tmp_path, rebound_target
):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    input_lock = _test_input_lock_path(package_dir)
    validation = gate.resolve_register_package(
        package_dir,
        lock_path=input_lock,
        return_validation=True,
    )
    target = register_csv if rebound_target == "register" else input_lock
    replacement = tmp_path / f"replacement-{target.name}"
    replacement.write_bytes(target.read_bytes())
    os.replace(replacement, target)
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    with pytest.raises(ValueError, match="identity changed after validation"):
        gate.snapshot_locked_register(validation, snapshot_dir=snapshot_dir)


@pytest.mark.parametrize("rebound_target", ["register", "lock"])
def test_validation_receipt_binds_identity_from_same_descriptor(
    monkeypatch, tmp_path, rebound_target
):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    input_lock = _test_input_lock_path(package_dir)
    target = register_csv if rebound_target == "register" else input_lock
    replacement = tmp_path / f"replacement-during-read-{target.name}"
    replacement.write_bytes(target.read_bytes())
    original_read = gate._read_regular_file_bounded_with_identity

    def replacing_read(path, **kwargs):
        content, identity = original_read(path, **kwargs)
        is_register_read = (
            rebound_target == "register"
            and kwargs.get("dir_fd") is not None
            and Path(path).name == target.name
        )
        if (is_register_read or path == target.absolute()) and replacement.exists():
            os.replace(replacement, target)
        return content, identity

    monkeypatch.setattr(
        gate, "_read_regular_file_bounded_with_identity", replacing_read
    )
    if rebound_target == "register":
        with pytest.raises(
            ValueError,
            match="package (inventory|member).*(changed|validation)",
        ):
            gate.resolve_register_package(
                package_dir,
                lock_path=input_lock,
                return_validation=True,
            )
        return
    validation = gate.resolve_register_package(
        package_dir,
        lock_path=input_lock,
        return_validation=True,
    )
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    with pytest.raises(ValueError, match="identity changed after validation"):
        gate.snapshot_locked_register(validation, snapshot_dir=snapshot_dir)


def test_package_members_are_opened_relative_to_a_held_directory_descriptor(
    monkeypatch, tmp_path
):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    original_open = gate.os.open

    def reject_absolute_member_open(path, flags, *args, **kwargs):
        candidate = Path(path)
        if candidate != package_dir and package_dir in candidate.parents:
            raise AssertionError(f"package member reopened by pathname: {candidate}")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(gate.os, "open", reject_absolute_member_open)

    assert gate.resolve_register_package(package_dir) == register_csv.absolute()


def test_package_inventory_is_streamed_from_the_held_directory_descriptor(
    monkeypatch, tmp_path
):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)

    monkeypatch.setattr(
        gate.os,
        "listdir",
        lambda *args, **kwargs: pytest.fail("unbounded directory list materialized"),
    )

    assert gate.resolve_register_package(package_dir) == register_csv.absolute()


def test_package_rejects_member_added_after_initial_inventory(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    original_read = gate._read_regular_file_bounded_with_identity
    injected = False

    def inject_late_member(path, **kwargs):
        nonlocal injected
        if not injected and Path(path).name == gate.MANIFEST_FILENAME:
            (package_dir / "late-extra.bin").write_bytes(b"late")
            injected = True
        return original_read(path, **kwargs)

    monkeypatch.setattr(
        gate, "_read_regular_file_bounded_with_identity", inject_late_member
    )

    with pytest.raises(ValueError, match="package inventory changed during validation"):
        gate.resolve_register_package(package_dir)


def test_package_member_opens_are_nonblocking(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    original_open = gate.os.open
    observed_member_open = False

    def assert_nonblocking_member_open(path, flags, *args, **kwargs):
        nonlocal observed_member_open
        if kwargs.get("dir_fd") is not None and str(path) != ".":
            observed_member_open = True
            assert flags & gate.os.O_NONBLOCK
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(gate.os, "open", assert_nonblocking_member_open)

    assert gate.resolve_register_package(package_dir) == register_csv.absolute()
    assert observed_member_open


def test_package_rejects_csv_field_over_byte_limit(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    with register_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        row = next(reader)
    row["title"] = "😀" * 17
    with register_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_REGISTER_HEADERS)
        writer.writeheader()
        writer.writerow(row)
    _refresh_package_integrity(package_dir, row_count=1)
    monkeypatch.setattr(gate, "MAX_CSV_FIELD_BYTES", 64)

    with pytest.raises(ValueError, match="CSV field exceeds byte limit"):
        gate.resolve_register_package(package_dir)


def test_trust_paths_are_never_canonicalized_by_following_links(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    input_lock = _test_input_lock_path(package_dir)
    original_resolve = Path.resolve

    def reject_trust_path_resolve(path, *args, **kwargs):
        if path in {package_dir, input_lock}:
            raise AssertionError(f"trust path passed through resolve(): {path}")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", reject_trust_path_resolve)

    assert (
        gate.resolve_register_package(
            package_dir,
            lock_path=input_lock,
        )
        == register_csv.absolute()
    )


@pytest.mark.parametrize("linked_target", ["register", "lock"])
def test_validation_rejects_hardlinked_trust_inputs(tmp_path, linked_target):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    input_lock = _test_input_lock_path(package_dir)
    target = register_csv if linked_target == "register" else input_lock
    alias = tmp_path / f"hardlink-{target.name}"
    try:
        os.link(target, alias)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="must have exactly one hard link"):
        gate.resolve_register_package(package_dir, lock_path=input_lock)


def test_snapshot_rejects_register_changed_after_validation(tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    validation = gate.resolve_register_package(
        package_dir,
        lock_path=_test_input_lock_path(package_dir),
        return_validation=True,
    )
    register_csv.write_bytes(register_csv.read_bytes() + b"\n")
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    with pytest.raises(ValueError, match="changed after validation"):
        gate.snapshot_locked_register(
            validation,
            snapshot_dir=snapshot_dir,
        )


@pytest.mark.parametrize(
    "member_name",
    [gate.MANIFEST_FILENAME, "sds_dataset_register.json"],
)
def test_snapshot_rejects_nonregister_package_member_changed_after_validation(
    tmp_path, member_name
):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    input_lock = _test_input_lock_path(package_dir)
    validation = gate.resolve_register_package(
        package_dir,
        lock_path=input_lock,
        return_validation=True,
    )
    member = package_dir / member_name
    content = bytearray(member.read_bytes())
    content[-1] = (content[-1] + 1) % 256
    member.write_bytes(content)
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    with pytest.raises(ValueError, match="package member changed after validation"):
        gate.snapshot_locked_register(validation, snapshot_dir=snapshot_dir)


def test_snapshot_rejects_input_lock_swapped_after_validation(tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    input_lock = _test_input_lock_path(package_dir)
    validation = gate.resolve_register_package(
        package_dir, lock_path=input_lock, return_validation=True
    )
    target_lock = tmp_path / "swapped-lock-target.json"
    target_lock.write_bytes(input_lock.read_bytes())
    input_lock.unlink()
    input_lock.symlink_to(target_lock.name)
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    with pytest.raises(ValueError, match="input lock must be a regular file"):
        gate.snapshot_locked_register(
            validation,
            snapshot_dir=snapshot_dir,
        )


def test_snapshot_rechecks_input_lock_after_copy(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    input_lock = _test_input_lock_path(package_dir)
    validation = gate.resolve_register_package(
        package_dir,
        lock_path=input_lock,
        return_validation=True,
    )
    original_read = gate._read_regular_file_bounded
    mutated = False

    def mutate_lock_after_register_read(path, **kwargs):
        nonlocal mutated
        content = original_read(path, **kwargs)
        if kwargs.get("label") == "validated register" and not mutated:
            lock_content = bytearray(input_lock.read_bytes())
            lock_content[-1] = (lock_content[-1] + 1) % 256
            input_lock.write_bytes(lock_content)
            mutated = True
        return content

    monkeypatch.setattr(
        gate, "_read_regular_file_bounded", mutate_lock_after_register_read
    )
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    with pytest.raises(ValueError, match="input lock changed after validation"):
        gate.snapshot_locked_register(validation, snapshot_dir=snapshot_dir)


def test_snapshot_rechecks_complete_package_after_creation(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    summary_path = package_dir / "summary.json"
    summary_path.write_text('{"status":"qualified"}\n', encoding="utf-8")
    input_lock = _test_input_lock_path(package_dir)
    lock = json.loads(input_lock.read_text(encoding="utf-8"))
    summary_bytes = summary_path.read_bytes()
    lock["ancillary_payloads"] = {
        "summary.json": {
            "sha256": hashlib.sha256(summary_bytes).hexdigest(),
            "size_bytes": len(summary_bytes),
        }
    }
    input_lock.write_text(json.dumps(lock), encoding="utf-8")
    validation = gate.resolve_register_package(
        package_dir,
        lock_path=input_lock,
        return_validation=True,
    )
    original_hash = gate._hash_regular_file_bounded_with_identity
    mutated = False

    def mutate_after_snapshot_hash(path, **kwargs):
        nonlocal mutated
        result = original_hash(path, **kwargs)
        if kwargs.get("label") == "register snapshot" and not mutated:
            summary_path.write_text('{"status":"tampered!"}\n', encoding="utf-8")
            mutated = True
        return result

    monkeypatch.setattr(
        gate,
        "_hash_regular_file_bounded_with_identity",
        mutate_after_snapshot_hash,
    )
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    with pytest.raises(ValueError, match="package member changed after validation"):
        gate.snapshot_locked_register(validation, snapshot_dir=snapshot_dir)


def test_snapshot_rejects_regular_lock_and_register_rebound_after_validation(tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    input_lock = _test_input_lock_path(package_dir)
    validation = gate.resolve_register_package(
        package_dir,
        lock_path=input_lock,
        return_validation=True,
    )
    register_csv.write_text(
        ",".join(EXPECTED_REGISTER_HEADERS)
        + "\nurn:test:replacement,Title,Indicator,Description,Dimension,kg,"
        "mass,annual,duration,source,,,,evidence,1,owner,public,reviewed,"
        "materiality,decimal,analytics,global,policy-purpose-analytics\n",
        encoding="utf-8",
    )
    replacement = register_csv.read_bytes()
    lock = json.loads(input_lock.read_text(encoding="utf-8"))
    lock["payloads"]["sds_dataset_register.csv"] = {
        "sha256": hashlib.sha256(replacement).hexdigest(),
        "size_bytes": len(replacement),
    }
    input_lock.write_text(json.dumps(lock), encoding="utf-8")
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    with pytest.raises(ValueError, match="input lock changed after validation"):
        gate.snapshot_locked_register(validation, snapshot_dir=snapshot_dir)


def test_snapshot_rejects_register_swapped_to_symlink_after_validation(tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    input_lock = _test_input_lock_path(package_dir)
    validation = gate.resolve_register_package(
        package_dir, lock_path=input_lock, return_validation=True
    )
    target_register = tmp_path / "swapped-register.csv"
    target_register.write_bytes(register_csv.read_bytes())
    register_csv.unlink()
    register_csv.symlink_to(target_register)
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    with pytest.raises(
        ValueError, match="register package entry must be a regular file"
    ):
        gate.snapshot_locked_register(
            validation,
            snapshot_dir=snapshot_dir,
        )


def test_snapshot_does_not_follow_preexisting_destination_symlink(tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    validation = gate.resolve_register_package(
        package_dir,
        lock_path=_test_input_lock_path(package_dir),
        return_validation=True,
    )
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    outside_target = tmp_path / "outside.csv"
    outside_target.write_text("preserve", encoding="utf-8")
    (snapshot_dir / "sds_dataset_register.csv").symlink_to(outside_target)

    with pytest.raises(ValueError, match="snapshot destination already exists"):
        gate.snapshot_locked_register(
            validation,
            snapshot_dir=snapshot_dir,
        )
    assert outside_target.read_text(encoding="utf-8") == "preserve"


def test_snapshot_receipt_rejects_same_byte_replacement(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    validation = gate.resolve_register_package(
        package_dir,
        lock_path=_test_input_lock_path(package_dir),
        return_validation=True,
    )
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    snapshot = gate.snapshot_locked_register(validation, snapshot_dir=snapshot_dir)
    replacement = tmp_path / "replacement-snapshot.csv"
    replacement.write_bytes(snapshot.register_csv.read_bytes())
    os.replace(replacement, snapshot.register_csv)

    with pytest.raises(ValueError, match="identity changed after validation"):
        gate.assert_locked_snapshot(snapshot)


def test_every_declared_payload_is_checksum_verified(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    (package_dir / "sds_dataset_register.json").write_text(
        '[{"identifier":"tampered"}]', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="sha256 does not match declared payload"):
        gate.resolve_register_package(package_dir)


def test_package_rejects_uninventoried_extra_files(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    (package_dir / "unexpected.bin").write_bytes(b"not declared")

    with pytest.raises(ValueError, match="unexpected package entries"):
        gate.resolve_register_package(package_dir)


def test_package_rejects_inventory_over_entry_limit(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    monkeypatch.setattr(gate, "MAX_PACKAGE_ENTRIES", 3, raising=False)

    with pytest.raises(ValueError, match="package contains too many entries"):
        gate.resolve_register_package(package_dir)


def test_manifest_rejects_too_many_declared_payloads(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    template = manifest["files"][0]
    manifest["files"] = [
        {**template, "filename": f"payload-{index}.csv"}
        for index in range(gate.MAX_PACKAGE_ENTRIES + 1)
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest declares too many payloads"):
        gate.resolve_register_package(package_dir)


def test_package_rejects_total_bytes_over_budget(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    monkeypatch.setattr(gate, "MAX_TOTAL_PACKAGE_BYTES", 1, raising=False)

    with pytest.raises(ValueError, match="package exceeds total size limit"):
        gate.resolve_register_package(package_dir)


def test_package_rejects_payloads_over_the_size_limit(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    monkeypatch.setattr(gate, "MAX_PACKAGE_PAYLOAD_BYTES", 32, raising=False)

    with pytest.raises(ValueError, match="exceeds size limit"):
        gate.resolve_register_package(package_dir)


def test_bounded_regular_file_reader_rejects_content_over_limit(tmp_path):
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"12345")

    with pytest.raises(ValueError, match="payload exceeds size limit"):
        gate._read_regular_file_bounded(payload, max_bytes=4, label="payload")


def test_bounded_regular_file_hasher_streams_without_path_read(monkeypatch, tmp_path):
    payload = tmp_path / "payload.bin"
    content = b"streamed-payload"
    payload.write_bytes(content)
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(AssertionError("unbounded read")),
    )

    digest, size = gate._hash_regular_file_bounded(
        payload, max_bytes=len(content), label="payload"
    )

    assert digest == hashlib.sha256(content).hexdigest()
    assert size == len(content)


def test_resolver_never_uses_unbounded_path_read_for_register(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path):
        if path == register_csv:
            raise AssertionError("unbounded register read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    assert gate.resolve_register_package(package_dir) == register_csv.resolve()


def test_register_rejects_row_count_over_the_limit(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    with register_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    second_row = dict(rows[0])
    second_row["identifier"] = "urn:sds:reg:test:second"
    with register_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_REGISTER_HEADERS)
        writer.writeheader()
        writer.writerows([rows[0], second_row])
    _refresh_package_integrity(package_dir, row_count=2)
    monkeypatch.setattr(gate, "MAX_REGISTER_ROWS", 1, raising=False)

    with pytest.raises(ValueError, match="row count exceeds limit"):
        gate.resolve_register_package(package_dir)


def test_package_rejects_symlinked_control_files(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    manifest_path = package_dir / "manifest.json"
    target_path = package_dir / "manifest-target.json"
    target_path.write_bytes(manifest_path.read_bytes())
    manifest_path.unlink()
    manifest_path.symlink_to(target_path.name)

    with pytest.raises(
        ValueError, match="register package entry must be a regular file"
    ):
        gate.resolve_register_package(package_dir)


def test_package_rejects_symlinked_package_directory(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    package_link = tmp_path / "package-link"
    package_link.symlink_to(package_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink or reparse point"):
        gate.resolve_register_package(package_link)


def test_package_rejects_windows_reparse_point_directory(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    monkeypatch.setattr(gate, "_is_reparse_point", lambda file_stat: True)

    with pytest.raises(
        ValueError, match="package directory must not be a reparse point"
    ):
        gate.resolve_register_package(package_dir)


def test_package_rejects_oversized_control_files(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    monkeypatch.setattr(gate, "MAX_CONTROL_FILE_BYTES", 32, raising=False)

    with pytest.raises(ValueError, match="control file exceeds size limit"):
        gate.resolve_register_package(package_dir)


def test_register_rejects_fields_over_the_size_limit(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    with register_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["description"] = "x" * 64
    with register_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_REGISTER_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    _refresh_package_integrity(package_dir, row_count=1)
    monkeypatch.setattr(gate, "MAX_CSV_FIELD_BYTES", 32, raising=False)

    with pytest.raises(ValueError, match="CSV field exceeds limit"):
        gate.resolve_register_package(package_dir)


def test_manifest_rejects_duplicate_json_keys(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    manifest_path = package_dir / "manifest.json"
    raw_manifest = manifest_path.read_text(encoding="utf-8")
    raw_manifest = raw_manifest.replace(
        '"source_project": "atomizer"',
        '"source_project": "atomizer", "source_project": "atomizer"',
        1,
    )
    manifest_path.write_text(raw_manifest, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON key: source_project"):
        gate.resolve_register_package(package_dir)


def test_checksum_manifest_rejects_non_sha256_digest(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    checksum_path = package_dir / "MANIFEST.sha256"
    checksum_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    checksum_lines[0] = f"{'0' * 63} *sds_dataset_register.csv"
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid SHA-256 digest"):
        gate.resolve_register_package(package_dir)


def test_manifest_requires_canonical_csv_boundary_policy(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["policy"]["preferred_boundary"] = "sds_dataset_register.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="policy must bind the canonical CSV"):
        gate.resolve_register_package(package_dir)


def test_manifest_requires_coherent_atomizer_source_version(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_ref"] = "different-source"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="source version metadata is inconsistent"):
        gate.resolve_register_package(package_dir)


def test_manifest_requires_full_unfiltered_register_snapshot(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["framework_filter"] = "ESRS"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="framework_filter must be empty"):
        gate.resolve_register_package(package_dir)


def test_register_rejects_duplicate_identifiers(tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    rows = register_csv.read_text(encoding="utf-8").splitlines()
    register_csv.write_text("\n".join([*rows, rows[1]]) + "\n", encoding="utf-8")
    _refresh_package_integrity(package_dir, row_count=2)

    with pytest.raises(ValueError, match="duplicate identifier"):
        gate.resolve_register_package(package_dir)


def test_register_rejects_empty_identifiers(tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    with register_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["identifier"] = ""
    with register_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_REGISTER_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    _refresh_package_integrity(package_dir, row_count=1)

    with pytest.raises(ValueError, match="identifier must be non-empty"):
        gate.resolve_register_package(package_dir)


def test_register_rejects_rows_with_missing_columns(tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    lines = register_csv.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].rsplit(",", 1)[0]
    register_csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _refresh_package_integrity(package_dir, row_count=1)

    with pytest.raises(ValueError, match="column count does not match header"):
        gate.resolve_register_package(package_dir)


def test_subprocess_timeout_fails_closed(monkeypatch, tmp_path, capsys):
    command = ["synthetic-consumer"]

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(gate.subprocess, "run", timeout)

    assert gate.run(command, cwd=tmp_path) == 124
    assert "timed out" in capsys.readouterr().err


def test_gate_requires_external_register_package(monkeypatch, capsys):
    monkeypatch.setattr(gate, "check_prerequisites", lambda: 0)
    monkeypatch.setattr(gate, "run", lambda *a, **kw: pytest.fail("command ran"))

    assert gate.main() == 2
    assert "--register-package-dir is required" in capsys.readouterr().err


def test_gate_uses_validated_external_register_package(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    _bind_test_input_lock(monkeypatch, package_dir)
    calls = []
    parity_calls = []
    monkeypatch.setattr(gate, "check_prerequisites", lambda: 0)

    def fake_parity(path):
        parity_calls.append((path, path.read_bytes()))
        return 0

    monkeypatch.setattr(gate, "verify_external_register_edc_bundle", fake_parity)

    def fake_run(command, *, cwd, timeout):
        assert timeout == gate.COMMAND_TIMEOUT_SECONDS
        calls.append((command, cwd))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gate.subprocess, "run", fake_run)

    assert gate.main(["--register-package-dir", str(package_dir)]) == 0
    assert len(parity_calls) == 1
    snapshot_path, snapshot_bytes = parity_calls[0]
    assert snapshot_path != register_csv.resolve()
    assert snapshot_path.name == "sds_dataset_register.csv"
    assert snapshot_bytes == register_csv.read_bytes()
    assert calls[-1] == (
        [
            sys.executable,
            "scripts/e6_check_governance.py",
            "--strict",
            "--register-csv",
            str(snapshot_path),
        ],
        REPO_ROOT,
    )


def test_gate_rejects_register_bytes_that_do_not_match_manifest(
    monkeypatch, tmp_path, capsys
):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    _bind_test_input_lock(monkeypatch, package_dir)
    register_csv.write_text(
        "identifier,title\nurn:sds:reg:test:water,Changed\n", encoding="utf-8"
    )
    monkeypatch.setattr(gate, "check_prerequisites", lambda: 0)
    monkeypatch.setattr(gate, "run", lambda *a, **kw: pytest.fail("command ran"))

    assert gate.main(["--register-package-dir", str(package_dir)]) == 2
    assert "sha256 does not match" in capsys.readouterr().err


def test_gate_rejects_checksum_that_does_not_match_register(
    monkeypatch, tmp_path, capsys
):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    _bind_test_input_lock(monkeypatch, package_dir)
    checksum_path = package_dir / "MANIFEST.sha256"
    checksum_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    checksum_lines[0] = f"{'0' * 64} *sds_dataset_register.csv"
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(gate, "check_prerequisites", lambda: 0)
    monkeypatch.setattr(gate, "run", lambda *a, **kw: pytest.fail("command ran"))

    assert gate.main(["--register-package-dir", str(package_dir)]) == 2
    assert "MANIFEST.sha256 hash does not match" in capsys.readouterr().err


def test_gate_rejects_manifest_row_count_that_does_not_match_csv(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["row_count"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="row_count does not match"):
        gate.resolve_register_package(package_dir)


def test_gate_rejects_non_atomizer_register_contract(monkeypatch, tmp_path, capsys):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    _bind_test_input_lock(monkeypatch, package_dir)
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_project"] = "other-project"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(gate, "check_prerequisites", lambda: 0)
    monkeypatch.setattr(gate, "run", lambda *a, **kw: pytest.fail("command ran"))

    assert gate.main(["--register-package-dir", str(package_dir)]) == 2
    assert "source_project must be atomizer" in capsys.readouterr().err


def test_gate_rejects_manifest_without_unique_register_file_entry(tmp_path):
    package_dir = tmp_path / "package"
    _write_manifest_bound_register_package(package_dir)
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (package_dir / "MANIFEST.sha256").write_text("", encoding="utf-8")

    with pytest.raises(
        ValueError, match="must contain exactly one sds_dataset_register.csv entry"
    ):
        gate.resolve_register_package(package_dir)


def test_external_register_must_regenerate_tracked_edc_bundle(
    monkeypatch, tmp_path, capsys
):
    repo_root = tmp_path / "repo"
    tracked_edc = repo_root / "configs" / "edc"
    tracked_edc.mkdir(parents=True)
    for filename in gate.EDC_BUNDLE_FILENAMES:
        (tracked_edc / filename).write_text(f"tracked-{filename}", encoding="utf-8")
    register_csv = tmp_path / "sds_dataset_register.csv"
    register_csv.write_text("identifier\nurn:test:1\n", encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", repo_root)

    def fake_run(command, *, cwd):
        assert cwd == repo_root
        outdir = Path(command[command.index("--outdir") + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        for filename in gate.EDC_BUNDLE_FILENAMES:
            content = f"tracked-{filename}"
            if filename == "assets.json":
                content = "drifted-assets"
            (outdir / filename).write_text(content, encoding="utf-8")
        return 0

    monkeypatch.setattr(gate, "run", fake_run)

    assert gate.verify_external_register_edc_bundle(register_csv) == 2
    assert "regenerated EDC artifact differs: assets.json" in capsys.readouterr().err


def test_external_edc_parity_uses_bounded_reads(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    tracked_edc = repo_root / "configs" / "edc"
    tracked_edc.mkdir(parents=True)
    for filename in gate.EDC_BUNDLE_FILENAMES:
        (tracked_edc / filename).write_text(f"tracked-{filename}", encoding="utf-8")
    register_csv = tmp_path / "sds_dataset_register.csv"
    register_csv.write_text("identifier\nurn:test:1\n", encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", repo_root)

    def fake_run(command, *, cwd):
        outdir = Path(command[command.index("--outdir") + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        for filename in gate.EDC_BUNDLE_FILENAMES:
            (outdir / filename).write_text(f"tracked-{filename}", encoding="utf-8")
        return 0

    monkeypatch.setattr(gate, "run", fake_run)
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: pytest.fail(f"unbounded read used for {self}"),
    )

    assert gate.verify_external_register_edc_bundle(register_csv) == 0


def test_external_edc_parity_rejects_oversized_artifact(monkeypatch, tmp_path, capsys):
    repo_root = tmp_path / "repo"
    tracked_edc = repo_root / "configs" / "edc"
    tracked_edc.mkdir(parents=True)
    for filename in gate.EDC_BUNDLE_FILENAMES:
        (tracked_edc / filename).write_bytes(b"x" * 9)
    register_csv = tmp_path / "sds_dataset_register.csv"
    register_csv.write_text("identifier\nurn:test:1\n", encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", repo_root)
    monkeypatch.setattr(gate, "MAX_EDC_ARTIFACT_BYTES", 8, raising=False)

    def fake_run(command, *, cwd):
        outdir = Path(command[command.index("--outdir") + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        for filename in gate.EDC_BUNDLE_FILENAMES:
            (outdir / filename).write_bytes(b"x" * 9)
        return 0

    monkeypatch.setattr(gate, "run", fake_run)

    assert gate.verify_external_register_edc_bundle(register_csv) == 2
    assert "EDC artifact exceeds size limit" in capsys.readouterr().err


def test_validated_gate_checks_snapshot_before_and_after_every_consumer(monkeypatch):
    snapshot = SimpleNamespace(register_csv=Path("snapshot.csv"))
    events = []
    monkeypatch.setattr(
        gate, "assert_locked_snapshot", lambda receipt: events.append("check")
    )
    monkeypatch.setattr(
        gate,
        "verify_external_register_edc_bundle",
        lambda path: events.append("parity") or 0,
    )
    monkeypatch.setattr(
        gate,
        "run",
        lambda command, *, cwd: events.append("run") or 0,
    )

    assert gate.run_validated_gate(snapshot) == 0
    assert events == [
        "check",
        "parity",
        "check",
        "check",
        "run",
        "check",
        "check",
        "run",
        "check",
        "check",
        "run",
        "check",
        "check",
        "run",
        "check",
        "check",
        "run",
        "check",
    ]


def test_snapshot_guard_reseals_when_consumer_raises(monkeypatch, tmp_path):
    register_csv = tmp_path / gate.REGISTER_FILENAME
    register_csv.write_bytes(b"register")
    snapshot = gate.LockedRegisterSnapshot(
        register_csv=register_csv,
        sha256=hashlib.sha256(b"register").hexdigest(),
        size_bytes=len(b"register"),
        file_identity=(register_csv.stat().st_dev, register_csv.stat().st_ino),
    )
    checks = []
    monkeypatch.setattr(
        gate, "assert_locked_snapshot", lambda value: checks.append(value)
    )

    def fail():
        raise RuntimeError("consumer failed")

    with pytest.raises(RuntimeError, match="consumer failed"):
        gate._run_snapshot_guarded(snapshot, fail)

    assert checks == [snapshot, snapshot]


def test_validated_gate_rejects_snapshot_mutated_by_consumer(monkeypatch, tmp_path):
    snapshot_path = tmp_path / "snapshot.csv"
    content = b"identifier\nurn:test:1\n"
    snapshot_path.write_bytes(content)
    path_stat = snapshot_path.lstat()
    snapshot = gate.LockedRegisterSnapshot(
        register_csv=snapshot_path,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        file_identity=(path_stat.st_dev, path_stat.st_ino),
    )

    def mutating_parity(path):
        path.chmod(0o600)
        path.write_bytes(b"mutated")
        return 0

    monkeypatch.setattr(gate, "verify_external_register_edc_bundle", mutating_parity)
    monkeypatch.setattr(gate, "run", lambda *args, **kwargs: pytest.fail("test ran"))

    with pytest.raises(ValueError, match="snapshot changed after creation"):
        gate.run_validated_gate(snapshot)


def test_gate_stops_before_tests_when_external_edc_parity_fails(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    _bind_test_input_lock(monkeypatch, package_dir)
    parity_calls = []
    monkeypatch.setattr(gate, "check_prerequisites", lambda: 0)
    monkeypatch.setattr(
        gate,
        "verify_external_register_edc_bundle",
        lambda path: parity_calls.append(path) or 9,
    )
    monkeypatch.setattr(gate, "run", lambda *a, **kw: pytest.fail("test command ran"))

    assert gate.main(["--register-package-dir", str(package_dir)]) == 9
    assert len(parity_calls) == 1
    assert parity_calls[0] != register_csv.resolve()
    assert parity_calls[0].name == "sds_dataset_register.csv"


def test_makefile_does_not_accept_the_external_package_path_across_make():
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "SDS_E6_REGISTER_PACKAGE_DIR" not in makefile
    assert "$(E6_GATE)" not in makefile
    assert "direct argument-vector invocation required" in makefile


def test_makefile_e6_gate_is_not_an_accepted_external_input_boundary(tmp_path):
    make = shutil.which("make")
    if make is None:
        pytest.skip("GNU Make is not installed")
    environment = os.environ.copy()
    environment["SDS_E6_REGISTER_PACKAGE_DIR"] = str(tmp_path)

    completed = subprocess.run(
        [make, "e6-gate", f"ROOT_PY={sys.executable}"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "direct argument-vector invocation required" in (
        completed.stdout + completed.stderr
    )


def test_powershell_documentation_uses_the_direct_python_argument_vector():
    tests_doc = (REPO_ROOT / "api" / "docs" / "tests.md").read_text(encoding="utf-8")
    powershell_blocks = [
        block.split("```", 1)[0] for block in tests_doc.split("```powershell")[1:]
    ]
    powershell_block = next(
        block for block in powershell_blocks if "run_e6_gate.py" in block
    )

    assert "--register-package-dir" in powershell_block
    assert "make e6-gate" not in powershell_block
    assert "SDS_E6_REGISTER_PACKAGE_DIR" not in powershell_block


def test_parser_does_not_read_external_register_package_from_environment(monkeypatch):
    package_dir = '/tmp/pkg"; printf SHELL_INJECTION; #'
    monkeypatch.setenv("SDS_E6_REGISTER_PACKAGE_DIR", package_dir)

    args = gate.build_parser().parse_args([])

    assert args.register_package_dir is None


@pytest.mark.parametrize("failure_index", [None, 0, 1, 2, 3, 4])
def test_gate_command_order_and_failure_propagation(
    monkeypatch, tmp_path, failure_index
):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    _bind_test_input_lock(monkeypatch, package_dir)
    calls = []
    monkeypatch.setattr(gate, "check_prerequisites", lambda: 0)
    monkeypatch.setattr(gate, "verify_external_register_edc_bundle", lambda path: 0)
    monkeypatch.setattr(
        gate,
        "snapshot_locked_register",
        lambda validation, **kwargs: gate.LockedRegisterSnapshot(
            register_csv=validation.register_csv,
            sha256=validation.register_sha256,
            size_bytes=validation.register_size_bytes,
            file_identity=validation.register_identity,
        ),
    )

    def fake_run(command, *, cwd, timeout):
        assert timeout == gate.COMMAND_TIMEOUT_SECONDS
        calls.append((command, cwd))
        return SimpleNamespace(returncode=7 if len(calls) - 1 == failure_index else 0)

    monkeypatch.setattr(gate.subprocess, "run", fake_run)

    assert gate.main(["--register-package-dir", str(package_dir)]) == (
        0 if failure_index is None else 7
    )
    expected = _expected_commands(register_csv)
    assert calls == (
        expected if failure_index is None else expected[: failure_index + 1]
    )


@pytest.fixture
def canonical_runtime(monkeypatch):
    runtime = SimpleNamespace(
        implementation=SimpleNamespace(name="cpython"),
        version_info=(3, 10, 20),
        version="3.10.20",
        executable="canonical-python",
        stderr=StringIO(),
    )
    unicode_data = SimpleNamespace(unidata_version="13.0.0")
    monkeypatch.setattr(gate, "sys", runtime)
    monkeypatch.setattr(gate, "unicodedata", unicode_data)
    return runtime, unicode_data


@pytest.mark.parametrize("mismatch", ["implementation", "version", "unicode"])
def test_gate_rejects_noncanonical_runtime_before_commands(
    monkeypatch, canonical_runtime, mismatch
):
    runtime, unicode_data = canonical_runtime
    if mismatch == "implementation":
        runtime.implementation.name = "pypy"
    elif mismatch == "version":
        runtime.version_info = (3, 11, 0)
        runtime.version = "3.11.0"
    else:
        unicode_data.unidata_version = "14.0.0"
    monkeypatch.setattr(gate, "run", lambda *a, **kw: pytest.fail("command ran"))

    assert gate.main() == 2
    assert "use CPython 3.10 with Unicode data 13.0.0" in runtime.stderr.getvalue()


@pytest.mark.parametrize("dependency", ["pytest", "jsonschema"])
@pytest.mark.parametrize("error_type", [ImportError, RuntimeError])
def test_gate_rejects_unavailable_dependencies_before_commands(
    monkeypatch, canonical_runtime, dependency, error_type
):
    def fake_import(name):
        if name == dependency:
            raise error_type("unavailable dependency")
        return SimpleNamespace()

    monkeypatch.setattr(gate, "importlib", SimpleNamespace(import_module=fake_import))
    monkeypatch.setattr(gate, "run", lambda *a, **kw: pytest.fail("command ran"))

    assert gate.main() == 2
    error = canonical_runtime[0].stderr.getvalue()
    assert f"cannot import {dependency} with canonical-python" in error
    assert "Install api/requirements-dev.txt" in error


def test_gate_accepts_canonical_runtime_and_imports_dependencies(
    monkeypatch, canonical_runtime
):
    imports = []
    monkeypatch.setattr(
        gate, "importlib", SimpleNamespace(import_module=imports.append)
    )

    assert gate.check_prerequisites() == 0
    assert imports == ["pytest", "jsonschema"]


def test_gate_reports_command_start_failure(monkeypatch, tmp_path, capsys):
    package_dir = tmp_path / "package"
    register_csv = _write_manifest_bound_register_package(package_dir)
    _bind_test_input_lock(monkeypatch, package_dir)
    monkeypatch.setattr(gate, "check_prerequisites", lambda: 0)
    monkeypatch.setattr(gate, "verify_external_register_edc_bundle", lambda path: 0)
    calls = []

    def cannot_start(command, *, cwd, timeout):
        assert timeout == gate.COMMAND_TIMEOUT_SECONDS
        calls.append((command, cwd))
        raise FileNotFoundError("interpreter unavailable")

    monkeypatch.setattr(gate.subprocess, "run", cannot_start)

    assert gate.main(["--register-package-dir", str(package_dir)]) == 2
    assert calls == _expected_commands(register_csv)[:1]
    assert "E6 gate could not start command" in capsys.readouterr().err
