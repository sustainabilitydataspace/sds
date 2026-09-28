#!/usr/bin/env python3
"""Import a complete SDS package into SDS.

The package always contains the semantic indicator register
(`sds_dataset_register.csv`). It may also contain operational values in
`sds_values.csv` or `values.csv`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "api"


def _load_api_env_file() -> None:
    env_path = API_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


_load_api_env_file()


def _load_package_import_helpers():
    module_path = API_ROOT / "src" / "services" / "atomizer_package_import.py"
    spec = importlib.util.spec_from_file_location(
        "atomizer_package_import_helpers", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load package import helpers from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_value_csv_import_helpers():
    api_root_text = str(API_ROOT)
    if api_root_text not in sys.path:
        sys.path.insert(0, api_root_text)
    module_path = API_ROOT / "src" / "services" / "value_csv_import.py"
    spec = importlib.util.spec_from_file_location(
        "value_csv_import_helpers", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load values CSV helpers from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_calculation_contract_import_helpers():
    api_root_text = str(API_ROOT)
    if api_root_text not in sys.path:
        sys.path.insert(0, api_root_text)
    module_path = API_ROOT / "src" / "services" / "calculation_contract_import.py"
    spec = importlib.util.spec_from_file_location(
        "calculation_contract_import_helpers", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load calculation contract helpers from {module_path}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_PACKAGE_IMPORT_HELPERS = _load_package_import_helpers()
_VALUE_CSV_IMPORT_HELPERS = _load_value_csv_import_helpers()
_CALCULATION_CONTRACT_IMPORT_HELPERS = _load_calculation_contract_import_helpers()
AtomizerPackageImportError = _PACKAGE_IMPORT_HELPERS.AtomizerPackageImportError
CALCULATION_CONTRACT_FILENAME = _PACKAGE_IMPORT_HELPERS.CALCULATION_CONTRACT_FILENAME
SEMANTICS_MODES = _PACKAGE_IMPORT_HELPERS.SEMANTICS_MODES
resolve_calculation_contract_selection = (
    _PACKAGE_IMPORT_HELPERS.resolve_calculation_contract_selection
)
validate_standard_versioning_manifest = (
    _PACKAGE_IMPORT_HELPERS.validate_standard_versioning_manifest
)
ValueCsvContractError = _VALUE_CSV_IMPORT_HELPERS.ValueCsvContractError
load_values_from_csv = _VALUE_CSV_IMPORT_HELPERS.load_values_from_csv
CalculationContractImportError = (
    _CALCULATION_CONTRACT_IMPORT_HELPERS.CalculationContractImportError
)
validate_calculation_contract_payload = (
    _CALCULATION_CONTRACT_IMPORT_HELPERS.validate_calculation_contract_payload
)

PACKAGE_DIR_ENV = "SDS_PACKAGE_DIR"
LEGACY_PACKAGE_DIR_ENV = "ATOMIZER_SDS_PACKAGE_DIR"
REGISTER_FILENAME = "sds_dataset_register.csv"
VALUE_FILENAMES = ("sds_values.csv", "values.csv")
INDICATOR_IMPORT_SCRIPT = REPO_ROOT / "api" / "scripts" / "import_indicators.py"
CALCULATION_CONTRACT_IMPORT_SCRIPT = (
    REPO_ROOT / "api" / "scripts" / "import_calculation_contracts.py"
)
VALUE_IMPORT_SCRIPT = REPO_ROOT / "api" / "scripts" / "import_values_csv.py"
STANDARD_VERSIONING_IMPORT_SCRIPT = (
    REPO_ROOT / "api" / "scripts" / "import_standard_versioning.py"
)
SERVICE_VENV_WINDOWS = REPO_ROOT / "api" / ".venv" / "Scripts" / "python.exe"
SERVICE_VENV_POSIX = REPO_ROOT / "api" / ".venv" / "bin" / "python"
MANIFEST_FILENAME = "manifest.json"
CHECKSUM_FILENAME = "MANIFEST.sha256"
PLACEHOLDER_TENANT_IDS = {"default", "placeholder"}
MAX_PACKAGE_FILES = 64
MAX_PACKAGE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_PACKAGE_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_CHECKSUM_BYTES = 256 * 1024
IMPORT_SUBPROCESS_TIMEOUT_SECONDS = 900
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHECKSUM_RE = re.compile(r"^(?P<digest>[0-9a-f]{64}) [ *](?P<name>[^/\\\x00]+)$")


class PackageSnapshotError(ValueError):
    """Raised when an external Atomizer package cannot be snapshotted safely."""


def _open_directory_without_symlinks(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(os.sep, flags)
    try:
        for part in absolute.parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _file_seal(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_package_member(
    package_fd: int, name: str, *, max_bytes: int
) -> tuple[bytes, tuple[int, int, int, int, int, int, int]]:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=package_fd)
    except OSError as exc:
        raise PackageSnapshotError(f"cannot securely open package member {name}") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
            raise PackageSnapshotError(
                f"package member must be an independent regular file: {name}"
            )
        if initial.st_size > max_bytes:
            raise PackageSnapshotError(f"package member exceeds byte budget: {name}")
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - observed))
            if not chunk:
                break
            observed += len(chunk)
            if observed > max_bytes:
                raise PackageSnapshotError(f"package member exceeds byte budget: {name}")
            chunks.append(chunk)
        final = os.fstat(descriptor)
        if _file_seal(initial) != _file_seal(final) or observed != initial.st_size:
            raise PackageSnapshotError(f"package member changed while read: {name}")
        return b"".join(chunks), _file_seal(initial)
    finally:
        os.close(descriptor)


def _strict_json_object(raw: bytes) -> Mapping[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PackageSnapshotError(f"duplicate manifest key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageSnapshotError("manifest.json must be strict UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise PackageSnapshotError("manifest.json must contain a JSON object")
    return payload


def _manifest_declared_names(manifest: Mapping[str, Any]) -> frozenset[str]:
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise PackageSnapshotError("manifest.json files must be a non-empty array")
    names: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, Mapping):
            raise PackageSnapshotError(f"manifest files[{index}] must be an object")
        filename = item.get("path") or item.get("filename") or item.get("name")
        if not isinstance(filename, str) or filename != filename.strip() or not filename:
            raise PackageSnapshotError(
                f"manifest files[{index}] has an invalid filename"
            )
        if (
            Path(filename).name != filename
            or "/" in filename
            or "\\" in filename
            or "\x00" in filename
        ):
            raise PackageSnapshotError(
                f"manifest files[{index}] escapes the package directory"
            )
        if filename in names:
            raise PackageSnapshotError(f"duplicate manifest file: {filename}")
        digest = item.get("sha256")
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise PackageSnapshotError(
                f"manifest files[{index}] has an invalid SHA-256 digest"
            )
        names.add(filename)
    return frozenset(names)


def _write_snapshot_member(root_fd: int, name: str, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=root_fd)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def snapshot_atomizer_package(
    package_dir: Path, *, require_integrity: bool
) -> Iterator[Path]:
    """Yield a private, closed snapshot of one external package directory."""

    try:
        package_fd = _open_directory_without_symlinks(package_dir)
    except OSError as exc:
        raise PackageSnapshotError("cannot securely open package directory") from exc
    try:
        observed = frozenset(os.listdir(package_fd))
        if not observed or len(observed) > MAX_PACKAGE_FILES:
            raise PackageSnapshotError("package inventory is empty or exceeds file budget")
        has_manifest = MANIFEST_FILENAME in observed
        has_checksums = CHECKSUM_FILENAME in observed
        if has_manifest != has_checksums:
            raise PackageSnapshotError(
                "manifest.json and MANIFEST.sha256 must be supplied together"
            )
        if require_integrity and not has_manifest:
            raise PackageSnapshotError(
                "real package imports require manifest.json and MANIFEST.sha256"
            )

        manifest_bytes: bytes | None = None
        if has_manifest:
            manifest_bytes, _ = _read_package_member(
                package_fd, MANIFEST_FILENAME, max_bytes=MAX_MANIFEST_BYTES
            )
            declared = _manifest_declared_names(_strict_json_object(manifest_bytes))
            expected = declared | {MANIFEST_FILENAME, CHECKSUM_FILENAME}
            if observed != expected:
                raise PackageSnapshotError(
                    f"package inventory mismatch: missing={sorted(expected - observed)} "
                    f"extra={sorted(observed - expected)}"
                )
        else:
            expected = observed

        payloads: dict[str, bytes] = {}
        seals: dict[str, tuple[int, int, int, int, int, int, int]] = {}
        total_bytes = 0
        for name in sorted(expected):
            if Path(name).name != name or "/" in name or "\\" in name or "\x00" in name:
                raise PackageSnapshotError(f"package member has unsafe name: {name}")
            limit = (
                MAX_MANIFEST_BYTES
                if name == MANIFEST_FILENAME
                else MAX_CHECKSUM_BYTES
                if name == CHECKSUM_FILENAME
                else MAX_PACKAGE_MEMBER_BYTES
            )
            if name == MANIFEST_FILENAME and manifest_bytes is not None:
                payload, seal = _read_package_member(package_fd, name, max_bytes=limit)
                if payload != manifest_bytes:
                    raise PackageSnapshotError("manifest changed while package was read")
            else:
                payload, seal = _read_package_member(package_fd, name, max_bytes=limit)
            payloads[name] = payload
            seals[name] = seal
            total_bytes += len(payload)
            if total_bytes > MAX_PACKAGE_TOTAL_BYTES:
                raise PackageSnapshotError("package exceeds total byte budget")

        scan_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        scan_flags |= getattr(os, "O_NOFOLLOW", 0)
        scan_fd = os.open(".", scan_flags, dir_fd=package_fd)
        try:
            if frozenset(os.listdir(scan_fd)) != expected:
                raise PackageSnapshotError("package inventory changed while read")
            for name in sorted(expected):
                _, closing_seal = _read_package_member(
                    scan_fd,
                    name,
                    max_bytes=(
                        MAX_MANIFEST_BYTES
                        if name == MANIFEST_FILENAME
                        else MAX_CHECKSUM_BYTES
                        if name == CHECKSUM_FILENAME
                        else MAX_PACKAGE_MEMBER_BYTES
                    ),
                )
                if closing_seal != seals[name]:
                    raise PackageSnapshotError(f"package member changed while read: {name}")
        finally:
            os.close(scan_fd)

        with tempfile.TemporaryDirectory(prefix="sds-atomizer-package-") as temporary:
            snapshot = Path(temporary) / "package"
            snapshot.mkdir(mode=0o700)
            snapshot_fd = os.open(
                snapshot,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                for name in sorted(expected):
                    _write_snapshot_member(snapshot_fd, name, payloads[name])
            finally:
                os.close(snapshot_fd)
            yield snapshot
    finally:
        os.close(package_fd)


def _explicit_package_member_name(package_dir: Path, explicit: Path | None) -> Path | None:
    if explicit is None:
        return None
    candidate = explicit if explicit.is_absolute() else package_dir / explicit
    absolute = Path(os.path.abspath(candidate))
    root = Path(os.path.abspath(package_dir))
    try:
        relative = absolute.relative_to(root)
    except ValueError as exc:
        raise PackageSnapshotError("explicit package member must stay inside package-dir") from exc
    if len(relative.parts) != 1 or relative.name in {MANIFEST_FILENAME, CHECKSUM_FILENAME}:
        raise PackageSnapshotError("explicit package member must name one package payload")
    return Path(relative.name)


def resolve_python_executable() -> str:
    if SERVICE_VENV_WINDOWS.exists():
        return str(SERVICE_VENV_WINDOWS)
    if SERVICE_VENV_POSIX.exists():
        return str(SERVICE_VENV_POSIX)
    return sys.executable


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest_json(package_dir: Path) -> dict | None:
    manifest_path = package_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return None
    try:
        payload = _strict_json_object(manifest_path.read_bytes())
    except PackageSnapshotError as exc:
        raise ValueError(f"{MANIFEST_FILENAME} is invalid: {exc}") from exc
    return dict(payload)


def _manifest_file_hashes(manifest: dict | None) -> dict[str, str]:
    if manifest is None:
        return {}
    files = manifest.get("files")
    if files is None:
        return {}
    if not isinstance(files, list):
        raise ValueError(f"{MANIFEST_FILENAME} files must be a list")
    hashes: dict[str, str] = {}
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise ValueError(f"{MANIFEST_FILENAME} files[{index}] must be an object")
        filename_value = item.get("filename") or item.get("path") or ""
        filename = str(filename_value).strip()
        path_value = item.get("path")
        if path_value is not None and str(path_value) != filename:
            raise ValueError(
                f"{MANIFEST_FILENAME} files[{index}] filename/path mismatch"
            )
        sha256 = str(item.get("sha256") or "").strip()
        if not filename or not sha256:
            raise ValueError(
                f"{MANIFEST_FILENAME} files[{index}] requires filename and sha256"
            )
        if Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise ValueError(f"{MANIFEST_FILENAME} files[{index}] has unsafe filename")
        if _SHA256_RE.fullmatch(sha256) is None:
            raise ValueError(f"{MANIFEST_FILENAME} files[{index}] has invalid sha256")
        if filename in hashes:
            raise ValueError(f"{MANIFEST_FILENAME} has duplicate file {filename}")
        hashes[filename] = sha256
    return hashes


def _load_checksum_file(package_dir: Path) -> dict[str, str]:
    checksum_path = package_dir / CHECKSUM_FILENAME
    if not checksum_path.exists():
        return {}
    checksums: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        checksum_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line:
            continue
        match = _CHECKSUM_RE.fullmatch(raw_line)
        if match is None:
            raise ValueError(
                f"{CHECKSUM_FILENAME}:{line_number} must contain sha256 and filename"
            )
        filename = match.group("name")
        if filename in checksums:
            raise ValueError(f"{CHECKSUM_FILENAME} has duplicate file {filename}")
        checksums[filename] = match.group("digest")
    return checksums


def validate_package_integrity(
    package_dir: Path,
    package_files: list[Path | None],
) -> None:
    """Validate package manifest/checksum hashes when the package ships them."""

    expected_files = [path.resolve() for path in package_files if path is not None]
    expected_names = {path.name for path in expected_files if path.exists()}

    manifest = _load_manifest_json(package_dir)
    manifest_hashes = _manifest_file_hashes(manifest)
    checksum_hashes = _load_checksum_file(package_dir)

    if not manifest_hashes and not checksum_hashes:
        return
    if not manifest_hashes or not checksum_hashes:
        raise ValueError(
            f"{MANIFEST_FILENAME} files and {CHECKSUM_FILENAME} are both required"
        )
    if set(manifest_hashes) != set(checksum_hashes):
        raise ValueError("manifest and checksum inventories do not match")
    undeclared_consumed = expected_names - set(manifest_hashes)
    if undeclared_consumed:
        raise ValueError(
            "consumed files are missing from manifest: "
            + ", ".join(sorted(undeclared_consumed))
        )

    actual_hashes = {
        name: _sha256_file(package_dir / name) for name in sorted(manifest_hashes)
    }
    errors: list[str] = []

    if manifest is not None and REGISTER_FILENAME in actual_hashes:
        top_level_sha = str(manifest.get("sha256") or "").strip().lower()
        if top_level_sha and top_level_sha != actual_hashes[REGISTER_FILENAME]:
            errors.append(
                f"{MANIFEST_FILENAME} sha256 does not match {REGISTER_FILENAME}"
            )

    for name in sorted(actual_hashes):
        actual = actual_hashes[name]
        manifest_hash = manifest_hashes.get(name)
        if manifest_hashes and manifest_hash is None:
            errors.append(f"{MANIFEST_FILENAME} missing hash for {name}")
        elif manifest_hash is not None and manifest_hash != actual:
            errors.append(f"{MANIFEST_FILENAME} hash mismatch for {name}")

        checksum_hash = checksum_hashes.get(name)
        if checksum_hashes and checksum_hash is None:
            errors.append(f"{CHECKSUM_FILENAME} missing hash for {name}")
        elif checksum_hash is not None and checksum_hash != actual:
            errors.append(f"{CHECKSUM_FILENAME} hash mismatch for {name}")

    if errors:
        raise ValueError("package integrity validation failed: " + "; ".join(errors))


def resolve_package_file(
    package_dir: Path, explicit_path: Path | None, default_names: tuple[str, ...]
) -> Path:
    if explicit_path is not None:
        for candidate in (
            explicit_path,
            package_dir / explicit_path,
            REPO_ROOT / explicit_path,
        ):
            if candidate.exists():
                return candidate.resolve()
        return explicit_path.resolve()

    for filename in default_names:
        candidate = (package_dir / filename).resolve()
        if candidate.exists():
            return candidate
    return (package_dir / default_names[0]).resolve()


def build_indicator_command(args: argparse.Namespace, register_csv: Path) -> list[str]:
    command = [
        resolve_python_executable(),
        str(INDICATOR_IMPORT_SCRIPT),
        "--csv",
        str(register_csv),
    ]
    if args.db_url:
        command.extend(["--db-url", args.db_url])
    if args.dry_run:
        command.append("--dry-run")
    if args.resume:
        command.append("--resume")
    if args.batch_size is not None:
        command.extend(["--batch-size", str(args.batch_size)])
    for prefix in args.retire_indicator_prefix:
        command.extend(["--retire-prefix", prefix])
    for approval in args.approve_retirement_impact:
        command.extend(["--approve-retirement-impact", approval])
    return command


def parse_retirement_impact_approvals(values: list[str] | None) -> dict[str, int]:
    approvals: dict[str, int] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(
                "retirement approval must use PREFIX=COUNT, " f"got {value!r}"
            )
        prefix, count_text = value.rsplit("=", 1)
        prefix = prefix.strip()
        count_text = count_text.strip()
        if not prefix:
            raise ValueError("retirement approval prefix cannot be blank")
        if prefix in approvals:
            raise ValueError(f"duplicate retirement approval for prefix {prefix}")
        try:
            count = int(count_text)
        except ValueError as exc:
            raise ValueError(
                f"retirement approval count for {prefix} must be an integer"
            ) from exc
        if count < 0:
            raise ValueError(
                f"retirement approval count for {prefix} must be non-negative"
            )
        approvals[prefix] = count
    return approvals


def validate_retirement_impact_approvals(
    retire_prefixes: list[str],
    approvals: dict[str, int],
    *,
    dry_run: bool,
) -> None:
    prefixes = [prefix for prefix in retire_prefixes if prefix]
    if dry_run and not approvals:
        return
    missing = [prefix for prefix in prefixes if prefix not in approvals]
    if missing:
        raise ValueError(
            "real closed-scope retirement requires explicit impact approval via "
            "--approve-retirement-impact PREFIX=COUNT for: " + ", ".join(missing)
        )
    extra = sorted(set(approvals) - set(prefixes))
    if extra:
        raise ValueError(
            "retirement approval provided for unrequested prefix: " + ", ".join(extra)
        )


def build_standard_versioning_command(
    args: argparse.Namespace,
    package_dir: Path,
    register_csv: Path,
) -> list[str]:
    command = [
        resolve_python_executable(),
        str(STANDARD_VERSIONING_IMPORT_SCRIPT),
        "--package-dir",
        str(package_dir),
        "--register-csv",
        str(register_csv),
        "--created-by",
        args.created_by,
    ]
    if args.db_url:
        command.extend(["--db-url", args.db_url])
    if args.dry_run:
        command.append("--dry-run")
    return command


def build_values_command(
    args: argparse.Namespace,
    values_csv: Path,
    register_csv: Path | None = None,
    calculation_contract_json: Path | None = None,
) -> list[str]:
    command = [
        resolve_python_executable(),
        str(VALUE_IMPORT_SCRIPT),
        "--csv",
        str(values_csv),
        "--created-by",
        args.created_by,
    ]
    if args.db_url:
        command.extend(["--db-url", args.db_url])
    if args.default_entity:
        command.extend(["--default-entity", args.default_entity])
    if args.values_batch_size is not None:
        command.extend(["--batch-size", str(args.values_batch_size)])
    if args.dry_run:
        command.append("--dry-run")
        if register_csv is not None:
            command.extend(["--known-register-csv", str(register_csv)])
        if calculation_contract_json is not None:
            command.extend(
                ["--known-calculation-contract-json", str(calculation_contract_json)]
            )
    elif args.tenant_id:
        command.extend(["--tenant-id", args.tenant_id])
    return command


def validate_real_values_tenant(tenant_id: str | None) -> str:
    tenant = (tenant_id or "").strip()
    if not tenant:
        raise ValueError(
            "real package values import requires explicit --tenant-id before imports run"
        )
    if tenant.lower() in PLACEHOLDER_TENANT_IDS:
        raise ValueError(
            "real package values import requires non-placeholder --tenant-id"
        )
    return tenant


def build_calculation_contract_command(
    args: argparse.Namespace,
    contract_json: Path,
    register_csv: Path | None = None,
) -> list[str]:
    command = [
        resolve_python_executable(),
        str(CALCULATION_CONTRACT_IMPORT_SCRIPT),
        "--json",
        str(contract_json),
    ]
    if args.db_url:
        command.extend(["--db-url", args.db_url])
    if args.dry_run:
        command.append("--dry-run")
        if register_csv is not None:
            command.extend(["--known-register-csv", str(register_csv)])
    command.extend(["--retirement-scope", args.calculation_contract_retirement_scope])
    return command


def load_register_identifiers(register_csv: Path | None) -> set[str]:
    if register_csv is None or not register_csv.exists():
        return set()
    with register_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "identifier" not in reader.fieldnames:
            return set()
        return {
            (row.get("identifier") or "").strip()
            for row in reader
            if (row.get("identifier") or "").strip()
        }


def prevalidate_calculation_contract_json(
    contract_json: Path,
    *,
    register_identifiers: set[str],
) -> None:
    try:
        payload = json.loads(contract_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid calculation contract JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("calculation contract JSON must be an object")
    try:
        validate_calculation_contract_payload(
            payload,
            known_indicator_identifiers=register_identifiers,
        )
    except CalculationContractImportError as exc:
        raise ValueError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    default_package_dir = None
    if os.environ.get(PACKAGE_DIR_ENV):
        default_package_dir = Path(os.environ[PACKAGE_DIR_ENV])
    elif os.environ.get(LEGACY_PACKAGE_DIR_ENV):
        default_package_dir = Path(os.environ[LEGACY_PACKAGE_DIR_ENV])

    parser = argparse.ArgumentParser(
        description=(
            "Import an SDS package: indicators from sds_dataset_register.csv "
            "and optional operational values from sds_values.csv."
        )
    )
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=default_package_dir,
        help=(
            f"Path to an SDS package. Also accepted via {PACKAGE_DIR_ENV}; "
            "a legacy package-dir environment variable is still accepted for compatibility."
        ),
    )
    parser.add_argument(
        "--register-csv",
        type=Path,
        default=None,
        help="Explicit indicator register CSV path",
    )
    parser.add_argument(
        "--values-csv",
        type=Path,
        default=None,
        help="Explicit operational values CSV path",
    )
    parser.add_argument(
        "--calculation-contract-json",
        type=Path,
        default=None,
        help=f"Explicit {CALCULATION_CONTRACT_FILENAME} path",
    )
    parser.add_argument("--db-url", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume indicator import snapshots where supported",
    )
    parser.add_argument(
        "--batch-size", type=int, default=None, help="Indicator import batch size"
    )
    parser.add_argument(
        "--values-batch-size",
        type=int,
        default=None,
        help=(
            "Operational values import batch size. Omit to keep the values "
            "importer default."
        ),
    )
    parser.add_argument(
        "--retire-indicator-prefix",
        action="append",
        default=[],
        help=(
            "After indicator import, deactivate active indicators whose identifier "
            "starts with this prefix and is not present in the imported register. "
            "Repeat for multiple closed scopes."
        ),
    )
    parser.add_argument(
        "--approve-retirement-impact",
        action="append",
        default=[],
        help=(
            "Required for real imports that pass --retire-indicator-prefix. "
            "Use PREFIX=COUNT after reviewing target DB impact, for example "
            "urn:sds:reg:ghg:=247."
        ),
    )
    parser.add_argument(
        "--default-entity",
        type=str,
        default=None,
        help="Default entity for value rows with blank entity",
    )
    parser.add_argument(
        "--tenant-id",
        type=str,
        default=None,
        help="Explicit tenant for real operational values imports",
    )
    parser.add_argument(
        "--created-by",
        type=str,
        default="atomizer_sds_package",
        help="Audit value for imported values",
    )
    parser.add_argument("--skip-indicators", action="store_true")
    parser.add_argument("--skip-standard-versioning", action="store_true")
    parser.add_argument("--skip-calculation-contract", action="store_true")
    parser.add_argument("--skip-values", action="store_true")
    parser.add_argument(
        "--require-calculation-contract",
        action="store_true",
        help=f"Fail if the package has no {CALCULATION_CONTRACT_FILENAME}",
    )
    parser.add_argument(
        "--semantics-mode",
        choices=SEMANTICS_MODES,
        default="optional",
        help="Calculation semantics import mode: required, optional, or off",
    )
    parser.add_argument(
        "--require-values",
        action="store_true",
        help="Fail if the package has no values CSV",
    )
    parser.add_argument(
        "--require-standard-versioning",
        action="store_true",
        help=(
            "Fail unless manifest.json contains a valid standard_versioning block "
            "with release identity, delta, lineage, and dry-run-first policy."
        ),
    )
    parser.add_argument(
        "--calculation-contract-retirement-scope",
        choices=("incoming_keys", "incoming_models"),
        default="incoming_keys",
        help=(
            "Retirement boundary passed to calculation contract import. "
            "incoming_keys is the safe default; incoming_models is for approved "
            "full-model replacement imports."
        ),
    )
    return parser


def _run_import_command(command: list[str]) -> int:
    try:
        return subprocess.call(
            command,
            cwd=str(REPO_ROOT),
            timeout=IMPORT_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print("FAIL: package import subprocess exceeded its time budget", file=sys.stderr)
        return 1
    except OSError:
        print("FAIL: package import subprocess could not be started", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.package_dir is None:
        print(
            "FAIL: --package-dir is required unless "
            f"{PACKAGE_DIR_ENV} or {LEGACY_PACKAGE_DIR_ENV} is set.",
            file=sys.stderr,
        )
        return 1
    source_package_dir = Path(os.path.abspath(args.package_dir))
    try:
        register_member = _explicit_package_member_name(
            source_package_dir, args.register_csv
        )
        values_member = _explicit_package_member_name(
            source_package_dir, args.values_csv
        )
        calculation_member = _explicit_package_member_name(
            source_package_dir, args.calculation_contract_json
        )
        with snapshot_atomizer_package(
            source_package_dir, require_integrity=not args.dry_run
        ) as package_dir:
            args.package_dir = package_dir
            args.register_csv = register_member
            args.values_csv = values_member
            args.calculation_contract_json = calculation_member
            return _run_import(args, package_dir)
    except PackageSnapshotError as exc:
        print(f"FAIL: unsafe package input: {exc}", file=sys.stderr)
        return 1


def _run_import(args: argparse.Namespace, package_dir: Path) -> int:
    """Run all preflight and import steps against one private package snapshot."""

    if args.batch_size is not None and args.batch_size < 1:
        print("FAIL: --batch-size must be >= 1", file=sys.stderr)
        return 1
    if args.values_batch_size is not None and args.values_batch_size < 1:
        print("FAIL: --values-batch-size must be >= 1", file=sys.stderr)
        return 1
    try:
        retirement_impact_approvals = parse_retirement_impact_approvals(
            args.approve_retirement_impact
        )
        validate_retirement_impact_approvals(
            args.retire_indicator_prefix,
            retirement_impact_approvals,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    standard_versioning = validate_standard_versioning_manifest(
        package_dir,
        require=args.require_standard_versioning,
    )
    if not standard_versioning.valid:
        print(
            "FAIL: invalid standard versioning metadata: "
            + "; ".join(standard_versioning.errors),
            file=sys.stderr,
        )
        return 1
    if args.dry_run and standard_versioning.present:
        print(
            "INFO: standard versioning metadata validated: "
            f"{standard_versioning.standard_id} {standard_versioning.release_id}"
        )

    register_csv: Path | None = None
    if not args.skip_indicators:
        register_csv = resolve_package_file(
            package_dir, args.register_csv, (REGISTER_FILENAME,)
        )
        if not register_csv.exists():
            print(f"FAIL: register CSV not found: {register_csv}", file=sys.stderr)
            return 1

    values_csv: Path | None = None
    if not args.skip_values:
        values_csv = resolve_package_file(package_dir, args.values_csv, VALUE_FILENAMES)
        if not values_csv.exists():
            if args.require_values or args.values_csv is not None:
                print(f"FAIL: values CSV not found: {values_csv}", file=sys.stderr)
                return 1
            values_csv = None
        elif not args.dry_run:
            try:
                args.tenant_id = validate_real_values_tenant(args.tenant_id)
            except ValueError as exc:
                print(f"FAIL: {exc}", file=sys.stderr)
                return 1

    try:
        contract_selection = resolve_calculation_contract_selection(
            package_dir=package_dir,
            explicit_path=args.calculation_contract_json,
            skip=args.skip_calculation_contract,
            require=args.require_calculation_contract,
            semantics_mode=args.semantics_mode,
            repo_root=REPO_ROOT,
        )
    except AtomizerPackageImportError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    calculation_contract_json = (
        contract_selection.path if contract_selection.enabled else None
    )

    try:
        validate_package_integrity(
            package_dir,
            [register_csv, calculation_contract_json, values_csv],
        )
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if calculation_contract_json is not None:
        try:
            prevalidate_calculation_contract_json(
                calculation_contract_json,
                register_identifiers=load_register_identifiers(register_csv),
            )
        except ValueError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1

    if values_csv is not None:
        try:
            load_values_from_csv(
                values_csv,
                default_entity=args.default_entity,
                default_metadata={"source": "csv_import"},
            )
        except (FileNotFoundError, ValueCsvContractError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1

    # Import the indicator catalog FIRST so a failed/invalid register does not leave
    # committed standard-versioning metadata behind (codex F04 M2: cross-step
    # partial-state reduction; each subprocess step is idempotent/re-runnable, and
    # the write-free preflight above rejects a bad package before any write).
    if register_csv is not None:
        result = _run_import_command(build_indicator_command(args, register_csv))
        if result != 0:
            return result

    if (
        register_csv is not None
        and standard_versioning.present
        and not args.skip_standard_versioning
    ):
        result = _run_import_command(
            build_standard_versioning_command(args, package_dir, register_csv)
        )
        if result != 0:
            return result

    if calculation_contract_json is not None:
        result = _run_import_command(
            build_calculation_contract_command(
                args, calculation_contract_json, register_csv
            )
        )
        if result != 0:
            return result
    elif args.semantics_mode != "off" and not args.skip_calculation_contract:
        print(
            f"INFO: no {CALCULATION_CONTRACT_FILENAME} found; "
            "skipped calculation contract import."
        )

    if values_csv is not None:
        result = _run_import_command(
            build_values_command(
                args,
                values_csv,
                register_csv,
                calculation_contract_json,
            )
        )
        if result != 0:
            return result
    elif not args.skip_values:
        print(
            "INFO: no sds_values.csv or values.csv found; skipped operational values import."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
