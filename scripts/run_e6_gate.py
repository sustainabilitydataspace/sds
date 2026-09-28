#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = REPO_ROOT / "api"
REGISTER_INPUT_LOCK_PATH = REPO_ROOT / "configs" / "edc" / "register-input-lock.json"
REGISTER_FILENAME = "sds_dataset_register.csv"
MANIFEST_FILENAME = "manifest.json"
CHECKSUM_FILENAME = "MANIFEST.sha256"
ANCILLARY_FILENAMES = ("summary.json",)
MAX_CONTROL_FILE_BYTES = 1024 * 1024
MAX_INPUT_LOCK_BYTES = 64 * 1024
MAX_PACKAGE_ENTRIES = 16
MAX_PACKAGE_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_TOTAL_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_EDC_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_CSV_FIELD_BYTES = 1024 * 1024
MAX_REGISTER_ROWS = 20_000
COMMAND_TIMEOUT_SECONDS = 300
REGISTER_HEADERS = [
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
EDC_BUNDLE_FILENAMES = (
    "assets.json",
    "policies.json",
    "contract-definitions.json",
)


class PackageMemberReceipt(NamedTuple):
    filename: str
    sha256: str
    size_bytes: int
    file_identity: tuple[int, int]
    max_bytes: int


class LockedRegisterValidation(NamedTuple):
    package_dir: Path
    package_identity: tuple[int, int]
    package_receipts: tuple[PackageMemberReceipt, ...]
    register_csv: Path
    register_sha256: str
    register_size_bytes: int
    register_identity: tuple[int, int]
    lock_path: Path
    lock_sha256: str
    lock_size_bytes: int
    lock_identity: tuple[int, int]


class LockedRegisterSnapshot(NamedTuple):
    register_csv: Path
    sha256: str
    size_bytes: int
    file_identity: tuple[int, int]


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _read_regular_file_bounded_with_identity(
    path: Path,
    *,
    max_bytes: int,
    label: str,
    expected_identity: tuple[int, int] | None = None,
    dir_fd: int | None = None,
) -> tuple[bytes, tuple[int, int]]:
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise ValueError(f"{label} must be a regular file: {path}") from exc
    try:
        opened_stat = os.fstat(descriptor)
        opened_identity = (opened_stat.st_dev, opened_stat.st_ino)
        if not stat.S_ISREG(opened_stat.st_mode) or _is_reparse_point(opened_stat):
            raise ValueError(f"{label} must be a regular file: {path}")
        if expected_identity is not None and opened_identity != expected_identity:
            raise ValueError(f"{label} identity changed after validation")
        if opened_stat.st_nlink != 1:
            raise ValueError(f"{label} must have exactly one hard link")
        if opened_stat.st_size > max_bytes:
            raise ValueError(f"{label} exceeds size limit")

        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > max_bytes:
            raise ValueError(f"{label} exceeds size limit")

        path_stat = (
            path.lstat()
            if dir_fd is None
            else os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
        )
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or _is_reparse_point(path_stat)
            or (path_stat.st_dev, path_stat.st_ino) != opened_identity
            or path_stat.st_nlink != 1
        ):
            raise ValueError(f"{label} changed while being read")
        return content, opened_identity
    except OSError as exc:
        raise ValueError(f"{label} cannot be read: {exc}") from exc
    finally:
        os.close(descriptor)


def _read_regular_file_bounded(
    path: Path,
    *,
    max_bytes: int,
    label: str,
    expected_identity: tuple[int, int] | None = None,
    dir_fd: int | None = None,
) -> bytes:
    content, _ = _read_regular_file_bounded_with_identity(
        path,
        max_bytes=max_bytes,
        label=label,
        expected_identity=expected_identity,
        dir_fd=dir_fd,
    )
    return content


def _hash_regular_file_bounded_with_identity(
    path: Path,
    *,
    max_bytes: int,
    label: str,
    expected_identity: tuple[int, int] | None = None,
    dir_fd: int | None = None,
) -> tuple[str, int, tuple[int, int]]:
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise ValueError(f"{label} must be a regular file: {path}") from exc
    try:
        opened_stat = os.fstat(descriptor)
        opened_identity = (opened_stat.st_dev, opened_stat.st_ino)
        if not stat.S_ISREG(opened_stat.st_mode) or _is_reparse_point(opened_stat):
            raise ValueError(f"{label} must be a regular file: {path}")
        if expected_identity is not None and opened_identity != expected_identity:
            raise ValueError(f"{label} identity changed after validation")
        if opened_stat.st_nlink != 1:
            raise ValueError(f"{label} must have exactly one hard link")
        if opened_stat.st_size > max_bytes:
            raise ValueError(f"{label} exceeds size limit")

        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise ValueError(f"{label} exceeds size limit")
            digest.update(chunk)

        path_stat = (
            path.lstat()
            if dir_fd is None
            else os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
        )
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or _is_reparse_point(path_stat)
            or (path_stat.st_dev, path_stat.st_ino) != opened_identity
            or path_stat.st_nlink != 1
        ):
            raise ValueError(f"{label} changed while being read")
        return digest.hexdigest(), size, opened_identity
    except OSError as exc:
        raise ValueError(f"{label} cannot be read: {exc}") from exc
    finally:
        os.close(descriptor)


def _hash_regular_file_bounded(
    path: Path,
    *,
    max_bytes: int,
    label: str,
    expected_identity: tuple[int, int] | None = None,
    dir_fd: int | None = None,
) -> tuple[str, int]:
    digest, size_bytes, _ = _hash_regular_file_bounded_with_identity(
        path,
        max_bytes=max_bytes,
        label=label,
        expected_identity=expected_identity,
        dir_fd=dir_fd,
    )
    return digest, size_bytes


@contextmanager
def _open_package_directory(
    package_dir: Path,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> Iterator[tuple[Path, int, tuple[int, int]]]:
    if os.name != "posix" or not hasattr(os, "O_DIRECTORY"):
        raise ValueError(
            "secure descriptor-relative package access is unavailable on this platform"
        )
    absolute_dir = Path(os.path.abspath(package_dir))
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute_dir, flags)
    except OSError as exc:
        raise ValueError(
            f"register package directory must not be a symlink or reparse point: {package_dir}"
        ) from exc
    try:
        opened_stat = os.fstat(descriptor)
        opened_identity = (opened_stat.st_dev, opened_stat.st_ino)
        if _is_reparse_point(opened_stat):
            raise ValueError("register package directory must not be a reparse point")
        if not stat.S_ISDIR(opened_stat.st_mode):
            raise ValueError(
                f"register package directory must be a regular directory: {package_dir}"
            )
        if expected_identity is not None and opened_identity != expected_identity:
            raise ValueError("register package directory identity changed after validation")
        yield absolute_dir, descriptor, opened_identity
        path_stat = absolute_dir.lstat()
        if (
            not stat.S_ISDIR(path_stat.st_mode)
            or _is_reparse_point(path_stat)
            or (path_stat.st_dev, path_stat.st_ino) != opened_identity
        ):
            raise ValueError("register package directory changed while being read")
    except OSError as exc:
        raise ValueError(f"register package directory cannot be read: {exc}") from exc
    finally:
        os.close(descriptor)


def _scan_package_inventory(
    package_fd: int,
) -> dict[str, tuple[int, int, int]]:
    inventory: dict[str, tuple[int, int, int]] = {}
    total_bytes = 0
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        scan_fd = os.open(".", flags, dir_fd=package_fd)
    except OSError as exc:
        raise ValueError("register package inventory cannot be read") from exc
    try:
        with os.scandir(scan_fd) as entries:
            for entry in entries:
                if len(inventory) >= MAX_PACKAGE_ENTRIES:
                    raise ValueError("register package contains too many entries")
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise ValueError(
                        f"register package entry cannot be inspected: {entry.name}"
                    ) from exc
                if not stat.S_ISREG(entry_stat.st_mode) or _is_reparse_point(entry_stat):
                    raise ValueError(
                        f"register package entry must be a regular file: {entry.name}"
                    )
                if entry_stat.st_nlink != 1:
                    raise ValueError(
                        f"register package entry must have exactly one hard link: {entry.name}"
                    )
                total_bytes += entry_stat.st_size
                if total_bytes > MAX_TOTAL_PACKAGE_BYTES:
                    raise ValueError("register package exceeds total size limit")
                inventory[entry.name] = (
                    entry_stat.st_dev,
                    entry_stat.st_ino,
                    entry_stat.st_size,
                )
    finally:
        os.close(scan_fd)
    return inventory


def _assert_package_seal(
    package_fd: int,
    receipts: tuple[PackageMemberReceipt, ...],
) -> None:
    inventory = _scan_package_inventory(package_fd)
    expected_names = {receipt.filename for receipt in receipts}
    if set(inventory) != expected_names:
        raise ValueError("register package inventory changed during validation")
    for receipt in receipts:
        digest, size_bytes = _hash_regular_file_bounded(
            Path(receipt.filename),
            max_bytes=receipt.max_bytes,
            label="package member",
            expected_identity=receipt.file_identity,
            dir_fd=package_fd,
        )
        if digest != receipt.sha256 or size_bytes != receipt.size_bytes:
            raise ValueError(
                f"package member changed after validation: {receipt.filename}"
            )


def check_prerequisites() -> int:
    if (
        sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 10)
        or unicodedata.unidata_version != "13.0.0"
    ):
        print(
            "E6 gate prerequisite failed: use CPython 3.10 with Unicode data "
            "13.0.0 and api/requirements-dev.txt installed. "
            f"Selected interpreter: {sys.executable} "
            f"({sys.implementation.name} {sys.version.split()[0]}, "
            f"Unicode {unicodedata.unidata_version}).",
            file=sys.stderr,
        )
        return 2

    for dependency in ("pytest", "jsonschema"):
        try:
            importlib.import_module(dependency)
        except Exception as exc:
            print(
                f"E6 gate prerequisite failed: cannot import {dependency} "
                f"with {sys.executable}: {exc}. "
                "Install api/requirements-dev.txt in the selected environment; "
                "schema validation must not be skipped.",
                file=sys.stderr,
            )
            return 2
    return 0


def run(command: list[str], *, cwd: Path) -> int:
    print(f"$ {' '.join(command)}", flush=True)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(
            f"E6 gate command timed out after {COMMAND_TIMEOUT_SECONDS} seconds",
            file=sys.stderr,
        )
        return 124
    except OSError as exc:
        print(f"E6 gate could not start command: {exc}", file=sys.stderr)
        return 2
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the strict E6 governance gate")
    parser.add_argument(
        "--register-package-dir",
        type=Path,
        help="Path to an external manifest-bound Atomizer register package",
    )
    return parser


def resolve_register_package(
    package_dir: Path,
    *,
    lock_path: Path | None = None,
    return_validation: bool = False,
) -> Path | LockedRegisterValidation:
    if return_validation and lock_path is None:
        raise ValueError("locked validation requires an SDS register input lock")
    with _open_package_directory(package_dir) as (
        absolute_dir,
        package_fd,
        package_identity,
    ):
        return _resolve_open_register_package(
            absolute_dir,
            package_fd=package_fd,
            package_identity=package_identity,
            lock_path=lock_path,
            return_validation=return_validation,
        )


def _resolve_open_register_package(
    resolved_dir: Path,
    *,
    package_fd: int,
    package_identity: tuple[int, int],
    lock_path: Path | None,
    return_validation: bool,
) -> Path | LockedRegisterValidation:
    initial_inventory = _scan_package_inventory(package_fd)
    package_entry_names = set(initial_inventory)
    package_receipts: dict[str, PackageMemberReceipt] = {}
    for filename in (REGISTER_FILENAME, MANIFEST_FILENAME, CHECKSUM_FILENAME):
        try:
            control_stat = os.stat(
                filename,
                dir_fd=package_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise ValueError(f"control file is not a regular file: {filename}") from exc
        if not stat.S_ISREG(control_stat.st_mode) or _is_reparse_point(control_stat):
            raise ValueError(f"control file is not a regular file: {filename}")
        if control_stat.st_nlink != 1:
            raise ValueError(f"control file must have exactly one hard link: {filename}")
        if (
            filename in {MANIFEST_FILENAME, CHECKSUM_FILENAME}
            and control_stat.st_size > MAX_CONTROL_FILE_BYTES
        ):
            raise ValueError(f"control file exceeds size limit: {filename}")
    register_csv = resolved_dir / REGISTER_FILENAME
    manifest_bytes, manifest_identity = _read_regular_file_bounded_with_identity(
        Path(MANIFEST_FILENAME),
        max_bytes=MAX_CONTROL_FILE_BYTES,
        label=f"{MANIFEST_FILENAME} control file",
        dir_fd=package_fd,
    )
    package_receipts[MANIFEST_FILENAME] = PackageMemberReceipt(
        MANIFEST_FILENAME,
        hashlib.sha256(manifest_bytes).hexdigest(),
        len(manifest_bytes),
        manifest_identity,
        MAX_CONTROL_FILE_BYTES,
    )
    try:
        manifest = json.loads(
            manifest_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{MANIFEST_FILENAME} is invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"{MANIFEST_FILENAME} must be a JSON object")
    if manifest.get("contract_version") != "atomizer-sds-register-package-v1":
        raise ValueError(
            f"{MANIFEST_FILENAME} contract_version must be "
            "atomizer-sds-register-package-v1"
        )
    if manifest.get("contract_mode") != "register":
        raise ValueError(f"{MANIFEST_FILENAME} contract_mode must be register")
    if manifest.get("source_project") != "atomizer":
        raise ValueError(f"{MANIFEST_FILENAME} source_project must be atomizer")
    if not isinstance(manifest.get("source_version"), str) or not manifest[
        "source_version"
    ].strip():
        raise ValueError(f"{MANIFEST_FILENAME} source_version must be non-empty")
    source_version = manifest["source_version"]
    sync = manifest.get("sync")
    if (
        manifest.get("source_ref") != source_version
        or not isinstance(sync, dict)
        or sync.get("atomizer_project_version") != source_version
    ):
        raise ValueError(
            f"{MANIFEST_FILENAME} source version metadata is inconsistent"
        )
    if manifest.get("framework_filter") != "":
        raise ValueError(f"{MANIFEST_FILENAME} framework_filter must be empty")
    policy = manifest.get("policy")
    if not isinstance(policy, dict) or policy.get("canonical_inputs") != [
        REGISTER_FILENAME
    ] or policy.get("preferred_boundary") != REGISTER_FILENAME:
        raise ValueError(
            f"{MANIFEST_FILENAME} policy must bind the canonical CSV"
        )
    expected_sha256 = manifest.get("sha256")
    files = manifest.get("files")
    if not isinstance(files, list) or any(not isinstance(item, dict) for item in files):
        raise ValueError(f"{MANIFEST_FILENAME} files must be a list of objects")
    if len(files) > MAX_PACKAGE_ENTRIES:
        raise ValueError("register manifest declares too many payloads")

    declared_payloads: dict[str, dict] = {}
    for item in files:
        filename = item.get("filename")
        if (
            not isinstance(filename, str)
            or not filename
            or Path(filename).name != filename
            or Path(filename).is_absolute()
        ):
            raise ValueError(
                f"{MANIFEST_FILENAME} files entries must use safe basenames"
            )
        if filename in declared_payloads:
            raise ValueError(f"{MANIFEST_FILENAME} contains duplicate filename: {filename}")
        declared_payloads[filename] = item

    register_entries = [
        item for item in files if item.get("filename") == REGISTER_FILENAME
    ]
    if len(register_entries) != 1:
        raise ValueError(
            f"{MANIFEST_FILENAME} files must contain exactly one "
            f"{REGISTER_FILENAME} entry"
        )
    register_entry = register_entries[0]

    checksum_bytes, checksum_identity = _read_regular_file_bounded_with_identity(
        Path(CHECKSUM_FILENAME),
        max_bytes=MAX_CONTROL_FILE_BYTES,
        label=f"{CHECKSUM_FILENAME} control file",
        dir_fd=package_fd,
    )
    package_receipts[CHECKSUM_FILENAME] = PackageMemberReceipt(
        CHECKSUM_FILENAME,
        hashlib.sha256(checksum_bytes).hexdigest(),
        len(checksum_bytes),
        checksum_identity,
        MAX_CONTROL_FILE_BYTES,
    )
    try:
        checksum_text = checksum_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{CHECKSUM_FILENAME} is not UTF-8: {exc}") from exc
    checksum_payloads: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        checksum_text.splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError(
                f"{CHECKSUM_FILENAME}:{line_number} must contain sha256 and filename"
            )
        digest, filename = parts
        filename = filename.lstrip("*")
        if re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
            raise ValueError(
                f"{CHECKSUM_FILENAME}:{line_number} has invalid SHA-256 digest"
            )
        if (
            not filename
            or Path(filename).name != filename
            or Path(filename).is_absolute()
        ):
            raise ValueError(
                f"{CHECKSUM_FILENAME}:{line_number} filename must be a safe basename"
            )
        if filename in checksum_payloads:
            raise ValueError(f"{CHECKSUM_FILENAME} contains duplicate entry: {filename}")
        checksum_payloads[filename] = digest.lower()
    if set(checksum_payloads) != set(declared_payloads):
        raise ValueError(
            f"{CHECKSUM_FILENAME} inventory does not match {MANIFEST_FILENAME} files"
        )

    payload_metadata: dict[str, dict[str, int | str]] = {}
    register_bytes: bytes | None = None
    register_identity: tuple[int, int] | None = None
    actual_package_bytes = len(manifest_bytes) + len(checksum_bytes)
    for filename, item in declared_payloads.items():
        payload_path = Path(filename)
        if filename == REGISTER_FILENAME:
            register_bytes, register_identity = _read_regular_file_bounded_with_identity(
                payload_path,
                max_bytes=MAX_PACKAGE_PAYLOAD_BYTES,
                label="package payload",
                dir_fd=package_fd,
            )
            digest = hashlib.sha256(register_bytes).hexdigest()
            size_bytes = len(register_bytes)
            payload_identity = register_identity
        else:
            digest, size_bytes, payload_identity = (
                _hash_regular_file_bounded_with_identity(
                    payload_path,
                    max_bytes=MAX_PACKAGE_PAYLOAD_BYTES,
                    label="package payload",
                    dir_fd=package_fd,
                )
            )
        package_receipts[filename] = PackageMemberReceipt(
            filename,
            digest,
            size_bytes,
            payload_identity,
            MAX_PACKAGE_PAYLOAD_BYTES,
        )
        actual_package_bytes += size_bytes
        if actual_package_bytes > MAX_TOTAL_PACKAGE_BYTES:
            raise ValueError("register package exceeds total size limit")
        payload_metadata[filename] = {
            "sha256": digest,
            "size_bytes": size_bytes,
        }
        if item.get("sha256") != digest:
            raise ValueError(f"sha256 does not match declared payload: {filename}")
        if item.get("size_bytes") != size_bytes:
            raise ValueError(f"size_bytes does not match declared payload: {filename}")
        if checksum_payloads[filename] != digest:
            raise ValueError(f"{CHECKSUM_FILENAME} hash does not match {filename}")

    if register_bytes is None or register_identity is None:
        raise ValueError(f"{REGISTER_FILENAME} payload was not read")
    register_digest = hashlib.sha256(register_bytes).hexdigest()
    if expected_sha256 != register_digest:
        raise ValueError(f"{MANIFEST_FILENAME} sha256 does not match {REGISTER_FILENAME}")

    ancillary_payload_metadata: dict[str, dict[str, int | str]] = {}
    for filename in ANCILLARY_FILENAMES:
        ancillary_path = Path(filename)
        try:
            os.stat(filename, dir_fd=package_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError(
                f"ancillary payload cannot be inspected: {filename}"
            ) from exc
        digest, size_bytes, ancillary_identity = (
            _hash_regular_file_bounded_with_identity(
                ancillary_path,
                max_bytes=MAX_CONTROL_FILE_BYTES,
                label="ancillary package payload",
                dir_fd=package_fd,
            )
        )
        package_receipts[filename] = PackageMemberReceipt(
            filename,
            digest,
            size_bytes,
            ancillary_identity,
            MAX_CONTROL_FILE_BYTES,
        )
        actual_package_bytes += size_bytes
        if actual_package_bytes > MAX_TOTAL_PACKAGE_BYTES:
            raise ValueError("register package exceeds total size limit")
        ancillary_payload_metadata[filename] = {
            "sha256": digest,
            "size_bytes": size_bytes,
        }

    allowed_package_entries = {
        MANIFEST_FILENAME,
        CHECKSUM_FILENAME,
        *declared_payloads,
        *ancillary_payload_metadata,
    }
    unexpected_entries = sorted(package_entry_names - allowed_package_entries)
    if unexpected_entries:
        raise ValueError(
            "unexpected package entries: " + ", ".join(unexpected_entries)
        )
    if _scan_package_inventory(package_fd) != initial_inventory:
        raise ValueError("register package inventory changed during validation")

    lock_validation: tuple[Path, str, int, tuple[int, int]] | None = None
    if lock_path is not None:
        absolute_lock_path = Path(os.path.abspath(lock_path))
        try:
            (
                input_lock_content,
                lock_identity,
            ) = _read_regular_file_bounded_with_identity(
                absolute_lock_path,
                max_bytes=MAX_INPUT_LOCK_BYTES,
                label="SDS register input lock",
            )
            input_lock = json.loads(
                input_lock_content.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"SDS register input lock is invalid JSON: {exc}") from exc
        expected_lock = {
            "schema_version": "sds-e6-register-input-lock-v1",
            "contract_version": "atomizer-sds-register-package-v1",
            "source_project": "atomizer",
            "source_version": source_version,
            "row_count": manifest.get("row_count"),
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "manifest_size_bytes": len(manifest_bytes),
            "checksum_sha256": hashlib.sha256(checksum_bytes).hexdigest(),
            "checksum_size_bytes": len(checksum_bytes),
            "payloads": payload_metadata,
            "ancillary_payloads": ancillary_payload_metadata,
        }
        if input_lock != expected_lock:
            raise ValueError("package does not match SDS input lock")
        lock_validation = (
            absolute_lock_path,
            hashlib.sha256(input_lock_content).hexdigest(),
            len(input_lock_content),
            lock_identity,
        )

    previous_field_limit = csv.field_size_limit(MAX_CSV_FIELD_BYTES)
    try:
        register_content = register_bytes.decode("utf-8-sig")
        with io.StringIO(register_content, newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            actual_headers = list(reader.fieldnames or [])
            actual_row_count = 0
            identifiers: set[str] = set()
            for row_number, row in enumerate(reader, start=2):
                actual_row_count += 1
                if actual_row_count > MAX_REGISTER_ROWS:
                    raise ValueError(f"{REGISTER_FILENAME} row count exceeds limit")
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(
                        f"{REGISTER_FILENAME}:{row_number} column count does not match header"
                    )
                if any(
                    len(value.encode("utf-8")) > MAX_CSV_FIELD_BYTES
                    for value in row.values()
                ):
                    raise ValueError(
                        f"{REGISTER_FILENAME}:{row_number} CSV field exceeds byte limit"
                    )
                identifier = (row.get("identifier") or "").strip()
                if not identifier:
                    raise ValueError(
                        f"{REGISTER_FILENAME}:{row_number} identifier must be non-empty"
                    )
                if identifier in identifiers:
                    raise ValueError(
                        f"{REGISTER_FILENAME}:{row_number} duplicate identifier: {identifier}"
                    )
                identifiers.add(identifier)
    except (UnicodeDecodeError, csv.Error) as exc:
        if "field larger than field limit" in str(exc):
            raise ValueError(f"{REGISTER_FILENAME} CSV field exceeds limit") from exc
        raise ValueError(f"{REGISTER_FILENAME} is invalid CSV: {exc}") from exc
    finally:
        csv.field_size_limit(previous_field_limit)
    expected_row_count = manifest.get("row_count")
    if (
        not isinstance(expected_row_count, int)
        or isinstance(expected_row_count, bool)
        or expected_row_count < 1
        or expected_row_count != actual_row_count
    ):
        raise ValueError(
            f"{MANIFEST_FILENAME} row_count does not match {REGISTER_FILENAME}"
        )
    if register_entry.get("row_count") != actual_row_count:
        raise ValueError(
            f"{MANIFEST_FILENAME} files entry row_count does not match "
            f"{REGISTER_FILENAME}"
        )
    if register_entry.get("headers") != actual_headers:
        raise ValueError(
            f"{MANIFEST_FILENAME} files entry headers do not match "
            f"{REGISTER_FILENAME}"
        )
    required_headers = register_entry.get("required_headers")
    if required_headers != REGISTER_HEADERS:
        raise ValueError(
            f"{MANIFEST_FILENAME} files entry required_headers do not match SDS contract"
        )
    if not isinstance(required_headers, list) or any(
        not isinstance(header, str) or not header for header in required_headers
    ):
        raise ValueError(
            f"{MANIFEST_FILENAME} files entry required_headers must be "
            "a non-empty string list"
        )
    missing_headers = [
        header for header in required_headers if header not in actual_headers
    ]
    if missing_headers:
        raise ValueError(
            f"{REGISTER_FILENAME} is missing required headers: "
            + ", ".join(missing_headers)
        )
    if actual_headers != REGISTER_HEADERS:
        raise ValueError(f"{REGISTER_FILENAME} headers do not match SDS contract")
    sealed_receipts = tuple(
        package_receipts[name] for name in sorted(package_receipts)
    )
    _assert_package_seal(package_fd, sealed_receipts)
    if return_validation:
        if lock_validation is None:
            raise ValueError("locked validation receipt was not created")
        (
            validated_lock_path,
            lock_sha256,
            lock_size_bytes,
            lock_identity,
        ) = lock_validation
        return LockedRegisterValidation(
            package_dir=resolved_dir,
            package_identity=package_identity,
            package_receipts=sealed_receipts,
            register_csv=register_csv,
            register_sha256=register_digest,
            register_size_bytes=len(register_bytes),
            register_identity=register_identity,
            lock_path=validated_lock_path,
            lock_sha256=lock_sha256,
            lock_size_bytes=lock_size_bytes,
            lock_identity=lock_identity,
        )
    return register_csv


def _assert_input_lock(validation: LockedRegisterValidation) -> None:
    input_lock_content = _read_regular_file_bounded(
        validation.lock_path,
        max_bytes=MAX_INPUT_LOCK_BYTES,
        label="SDS register input lock",
        expected_identity=validation.lock_identity,
    )
    if (
        hashlib.sha256(input_lock_content).hexdigest() != validation.lock_sha256
        or len(input_lock_content) != validation.lock_size_bytes
    ):
        raise ValueError("SDS register input lock changed after validation")


def snapshot_locked_register(
    validation: LockedRegisterValidation, *, snapshot_dir: Path
) -> LockedRegisterSnapshot:
    _assert_input_lock(validation)
    with _open_package_directory(
        validation.package_dir,
        expected_identity=validation.package_identity,
    ) as (_, package_fd, _):
        _assert_package_seal(package_fd, validation.package_receipts)
        content = _read_regular_file_bounded(
            Path(REGISTER_FILENAME),
            max_bytes=MAX_PACKAGE_PAYLOAD_BYTES,
            label="validated register",
            expected_identity=validation.register_identity,
            dir_fd=package_fd,
        )
        _assert_package_seal(package_fd, validation.package_receipts)
    if (
        hashlib.sha256(content).hexdigest() != validation.register_sha256
        or len(content) != validation.register_size_bytes
    ):
        raise ValueError(f"{REGISTER_FILENAME} changed after validation")
    if not snapshot_dir.is_dir() or snapshot_dir.is_symlink():
        raise ValueError(f"snapshot directory is not a regular directory: {snapshot_dir}")
    snapshot_path = snapshot_dir / REGISTER_FILENAME
    try:
        with snapshot_path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fchmod(handle.fileno(), 0o400)
            snapshot_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(snapshot_stat.st_mode) or snapshot_stat.st_nlink != 1:
                raise ValueError("register snapshot must be an independent regular file")
            snapshot_identity = (snapshot_stat.st_dev, snapshot_stat.st_ino)
    except FileExistsError as exc:
        raise ValueError("register snapshot destination already exists") from exc
    snapshot_digest, snapshot_size_bytes, _ = _hash_regular_file_bounded_with_identity(
        snapshot_path,
        max_bytes=MAX_PACKAGE_PAYLOAD_BYTES,
        label="register snapshot",
        expected_identity=snapshot_identity,
    )
    if (
        snapshot_digest != validation.register_sha256
        or snapshot_size_bytes != validation.register_size_bytes
    ):
        raise ValueError("register snapshot changed during creation")
    with _open_package_directory(
        validation.package_dir,
        expected_identity=validation.package_identity,
    ) as (_, package_fd, _):
        _assert_package_seal(package_fd, validation.package_receipts)
    _assert_input_lock(validation)
    return LockedRegisterSnapshot(
        register_csv=snapshot_path,
        sha256=snapshot_digest,
        size_bytes=snapshot_size_bytes,
        file_identity=snapshot_identity,
    )


def assert_locked_snapshot(snapshot: LockedRegisterSnapshot) -> None:
    digest, size_bytes = _hash_regular_file_bounded(
        snapshot.register_csv,
        max_bytes=MAX_PACKAGE_PAYLOAD_BYTES,
        label="register snapshot",
        expected_identity=snapshot.file_identity,
    )
    if digest != snapshot.sha256 or size_bytes != snapshot.size_bytes:
        raise ValueError("register snapshot changed after creation")


def verify_external_register_edc_bundle(register_csv: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="sds-e6-edc-") as temporary_dir:
        generated_dir = Path(temporary_dir)
        generator_exit = run(
            [
                sys.executable,
                "scripts/edc_bundle_from_register.py",
                "--input",
                str(register_csv),
                "--outdir",
                str(generated_dir),
                "--policy-registry",
                "docs/policies/policy_registry.json",
                "--strict",
            ],
            cwd=REPO_ROOT,
        )
        if generator_exit != 0:
            return generator_exit

        tracked_dir = REPO_ROOT / "configs" / "edc"
        for filename in EDC_BUNDLE_FILENAMES:
            tracked_path = tracked_dir / filename
            generated_path = generated_dir / filename
            if not tracked_path.is_file() or not generated_path.is_file():
                print(
                    f"E6 gate register package validation failed: missing EDC artifact: {filename}",
                    file=sys.stderr,
                )
                return 2
            try:
                tracked_content = _read_regular_file_bounded(
                    tracked_path,
                    max_bytes=MAX_EDC_ARTIFACT_BYTES,
                    label="EDC artifact",
                )
                generated_content = _read_regular_file_bounded(
                    generated_path,
                    max_bytes=MAX_EDC_ARTIFACT_BYTES,
                    label="EDC artifact",
                )
            except ValueError as exc:
                print(
                    f"E6 gate register package validation failed: {exc}",
                    file=sys.stderr,
                )
                return 2
            if tracked_content != generated_content:
                print(
                    "E6 gate register package validation failed: "
                    f"regenerated EDC artifact differs: {filename}",
                    file=sys.stderr,
                )
                return 2
    return 0


def _run_snapshot_guarded(
    snapshot: LockedRegisterSnapshot,
    consumer: Callable[[], int],
) -> int:
    assert_locked_snapshot(snapshot)
    try:
        return consumer()
    finally:
        assert_locked_snapshot(snapshot)


def run_validated_gate(snapshot: LockedRegisterSnapshot) -> int:
    register_csv = snapshot.register_csv
    parity_exit = _run_snapshot_guarded(
        snapshot,
        lambda: verify_external_register_edc_bundle(register_csv),
    )
    if parity_exit != 0:
        return parity_exit

    deliverable_test_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "api/tests/test_e6_governance_pack.py",
        "-q",
    ]
    deliverable_exit = _run_snapshot_guarded(
        snapshot,
        lambda: run(deliverable_test_cmd, cwd=REPO_ROOT),
    )
    if deliverable_exit != 0:
        return deliverable_exit

    policy_test_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_policy_enforcement.py",
        "-q",
    ]
    policy_exit = _run_snapshot_guarded(
        snapshot,
        lambda: run(policy_test_cmd, cwd=SERVICE_DIR),
    )
    if policy_exit != 0:
        return policy_exit

    generator_test_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "api/tests/test_edc_bundle_input_security.py",
        "-q",
    ]
    generator_test_exit = _run_snapshot_guarded(
        snapshot,
        lambda: run(generator_test_cmd, cwd=REPO_ROOT),
    )
    if generator_test_exit != 0:
        return generator_test_exit

    checker_test_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "api/tests/test_e6_check_governance.py",
        "-q",
    ]
    checker_test_exit = _run_snapshot_guarded(
        snapshot,
        lambda: run(checker_test_cmd, cwd=REPO_ROOT),
    )
    if checker_test_exit != 0:
        return checker_test_exit

    check_cmd = [
        sys.executable,
        "scripts/e6_check_governance.py",
        "--strict",
    ]
    check_cmd.extend(["--register-csv", str(register_csv)])
    checker_exit = _run_snapshot_guarded(
        snapshot,
        lambda: run(check_cmd, cwd=REPO_ROOT),
    )
    return checker_exit


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args([] if argv is None else argv)
    prerequisite_exit = check_prerequisites()
    if prerequisite_exit != 0:
        return prerequisite_exit

    if args.register_package_dir is None:
        print(
            "E6 gate register package validation failed: "
            "--register-package-dir is required",
            file=sys.stderr,
        )
        return 2
    try:
        validation = resolve_register_package(
            args.register_package_dir,
            lock_path=REGISTER_INPUT_LOCK_PATH,
            return_validation=True,
        )
        if not isinstance(validation, LockedRegisterValidation):
            raise ValueError("locked validation receipt was not returned")
        with tempfile.TemporaryDirectory(prefix="sds-e6-register-") as temporary_dir:
            register_csv = snapshot_locked_register(
                validation,
                snapshot_dir=Path(temporary_dir),
            )
            return run_validated_gate(register_csv)
    except ValueError as exc:
        print(f"E6 gate register package validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
