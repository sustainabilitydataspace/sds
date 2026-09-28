"""Shadow/report-only validation for canonical mapping packages."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from src.ontology.curie import DEFAULT_NAMESPACES
from src.services.canonical_mapping_package import (
    CanonicalMappingPackageError,
    CanonicalMappingPackageManifest,
    canonical_mapping_manifest_file_name,
    parse_canonical_mapping_manifest,
)
from src.services.canonical_mapping_relationship_policy import (
    non_operational_relationship_blockers,
    non_operational_relationship_type_counts,
    relationship_type_counts,
)
from src.services.strict_json import StrictJsonError, loads_strict_json

RELEASES_FILE = "sds_standard_releases"
DATAPOINTS_FILE = "sds_standard_datapoints"
ASSERTION_GROUPS_FILE = "sds_mapping_assertion_groups"
ASSERTION_COMPONENTS_FILE = "sds_mapping_assertion_components"
REQUIRED_PACKAGE_FILES = frozenset(
    {
        f"{RELEASES_FILE}.csv",
        f"{DATAPOINTS_FILE}.csv",
        f"{ASSERTION_GROUPS_FILE}.csv",
        f"{ASSERTION_COMPONENTS_FILE}.csv",
    }
)
PACKAGE_CONTROL_FILES = frozenset({"manifest.json", "MANIFEST.sha256"})
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_CHECKSUM_BYTES = 64 * 1024
MAX_PACKAGE_FILE_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_TOTAL_BYTES = 256 * 1024 * 1024
MAX_CSV_ROWS = 1_000_000
MAX_CSV_COLUMNS = 128
MAX_CSV_FIELD_CHARS = 128 * 1024
_CHECKSUM_LINE_RE = re.compile(r"^(?P<digest>[0-9a-f]{64}) [ *](?P<name>[^/\\\x00]+)$")

RELEASE_HEADERS = (
    "standard_id",
    "name",
    "version",
)
DATAPOINT_HEADERS = (
    "standard_id",
    "standard_version",
    "code",
    "label",
)
ASSERTION_GROUP_HEADERS = (
    "source_standard_id",
    "source_standard_version",
    "source_code",
    "mapping_profile",
    "relationship_type",
    "coverage_status",
    "confidence",
    "valid_from",
    "approval_status",
    "publication_status",
)
ASSERTION_COMPONENT_HEADERS = (
    "source_standard_id",
    "source_standard_version",
    "source_code",
    "mapping_profile",
    "component_order",
    "sygris_canonical_uri",
    "sygris_revision",
    "component_role",
)
SYGRIS_CANONICAL_URI_PREFIXES = (
    "syg:",
    DEFAULT_NAMESPACES.prefixes["syg"],
)


@dataclass(frozen=True)
class CanonicalMappingImportIssue:
    """Validation issue found before any database write is attempted."""

    file: str
    message: str
    row_number: int | None = None
    code: str = "validation_error"


@dataclass(frozen=True)
class CanonicalMappingImportReport:
    """Report-only import plan for a canonical mapping package."""

    package_dir: str
    package_schema_version: str | None
    mode: str
    valid: bool
    file_counts: dict[str, int]
    manifest_hash: str | None
    checksum_count: int
    source_version: str | None = None
    operational_eligible: bool = False
    relationship_type_counts: dict[str, int] = field(default_factory=dict)
    non_operational_relationship_type_counts: dict[str, int] = field(
        default_factory=dict
    )
    operational_blockers: list[str] = field(default_factory=list)
    errors: list[CanonicalMappingImportIssue] = field(default_factory=list)
    warnings: list[CanonicalMappingImportIssue] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_dir": self.package_dir,
            "package_schema_version": self.package_schema_version,
            "mode": self.mode,
            "valid": self.valid,
            "file_counts": self.file_counts,
            "manifest_hash": self.manifest_hash,
            "checksum_count": self.checksum_count,
            "source_version": self.source_version,
            "operational_eligible": self.operational_eligible,
            "relationship_type_counts": self.relationship_type_counts,
            "non_operational_relationship_type_counts": (
                self.non_operational_relationship_type_counts
            ),
            "operational_blockers": self.operational_blockers,
            "errors": [issue.__dict__ for issue in self.errors],
            "warnings": [issue.__dict__ for issue in self.warnings],
        }


@dataclass(frozen=True)
class CanonicalMappingPackageRows:
    """Validated CSV row sets from a canonical mapping package."""

    releases: list[dict[str, str]]
    datapoints: list[dict[str, str]]
    assertion_groups: list[dict[str, str]]
    assertion_components: list[dict[str, str]]


class CanonicalMappingPackageSecurityError(CanonicalMappingPackageError):
    """Fail-closed rejection raised before untrusted package bytes are parsed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise CanonicalMappingPackageSecurityError(
                "unsafe_package_object", "canonical mapping package is not a directory"
            )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _member_seal(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
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
        raise CanonicalMappingPackageSecurityError(
            "unsafe_package_object", f"cannot securely open package member {name}"
        ) from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
            raise CanonicalMappingPackageSecurityError(
                "unsafe_package_object",
                f"package member must be an independent regular file: {name}",
            )
        if initial.st_size > max_bytes:
            raise CanonicalMappingPackageSecurityError(
                "package_resource_limit", f"package member exceeds byte budget: {name}"
            )
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - observed))
            if not chunk:
                break
            observed += len(chunk)
            if observed > max_bytes:
                raise CanonicalMappingPackageSecurityError(
                    "package_resource_limit",
                    f"package member exceeds byte budget: {name}",
                )
            chunks.append(chunk)
        final = os.fstat(descriptor)
        if _member_seal(initial) != _member_seal(final) or observed != initial.st_size:
            raise CanonicalMappingPackageSecurityError(
                "package_changed_during_read",
                f"package member changed while read: {name}",
            )
        return b"".join(chunks), _member_seal(initial)
    finally:
        os.close(descriptor)


def _strict_json_object(raw: bytes) -> Mapping[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CanonicalMappingPackageSecurityError(
                    "manifest_invalid", f"duplicate manifest key: {key}"
                )
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanonicalMappingPackageSecurityError(
            "manifest_invalid", "manifest.json must be strict UTF-8 JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise CanonicalMappingPackageSecurityError(
            "manifest_invalid", "manifest.json must contain a JSON object"
        )
    return payload


def _declared_package_files(manifest: Mapping[str, Any]) -> frozenset[str]:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise CanonicalMappingPackageSecurityError(
            "manifest_invalid", "manifest files must be an array"
        )
    declared: set[str] = set()
    for item in files:
        if not isinstance(item, Mapping):
            raise CanonicalMappingPackageSecurityError(
                "manifest_invalid", "manifest file entries must be objects"
            )
        supplied_names = [
            item[key]
            for key in ("path", "filename", "name")
            if key in item and item[key] not in (None, "")
        ]
        if len({str(value) for value in supplied_names}) > 1:
            raise CanonicalMappingPackageSecurityError(
                "manifest_invalid", "manifest file name aliases must match exactly"
            )
        raw_name = item.get("path") or item.get("filename") or item.get("name")
        if (
            not isinstance(raw_name, str)
            or raw_name != raw_name.strip()
            or not raw_name
        ):
            raise CanonicalMappingPackageSecurityError(
                "manifest_invalid", "manifest file name must be a non-empty string"
            )
        if (
            Path(raw_name).name != raw_name
            or "/" in raw_name
            or "\\" in raw_name
            or "\x00" in raw_name
        ):
            raise CanonicalMappingPackageSecurityError(
                "manifest_path_outside_package",
                f"manifest path escapes package directory: {raw_name}",
            )
        if raw_name in declared:
            raise CanonicalMappingPackageSecurityError(
                "manifest_invalid", f"duplicate manifest file: {raw_name}"
            )
        digest = item.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise CanonicalMappingPackageSecurityError(
                "manifest_invalid", f"invalid canonical SHA-256 for {raw_name}"
            )
        declared.add(raw_name)
    if declared != REQUIRED_PACKAGE_FILES:
        raise CanonicalMappingPackageSecurityError(
            "package_inventory_mismatch",
            "manifest must declare exactly the four canonical mapping CSV files",
        )
    return frozenset(declared)


def _write_private_snapshot_member(root_fd: int, name: str, payload: bytes) -> None:
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
def canonical_mapping_package_snapshot(package_dir: Path) -> Iterator[Path]:
    """Copy one closed, regular-file package inventory into a private snapshot."""

    try:
        package_fd = _open_directory_without_symlinks(package_dir)
    except (OSError, CanonicalMappingPackageSecurityError) as exc:
        if isinstance(exc, CanonicalMappingPackageSecurityError):
            raise
        raise CanonicalMappingPackageSecurityError(
            "unsafe_package_object", "cannot securely open canonical mapping package"
        ) from exc
    try:
        manifest_bytes, manifest_seal = _read_package_member(
            package_fd, "manifest.json", max_bytes=MAX_MANIFEST_BYTES
        )
        manifest = _strict_json_object(manifest_bytes)
        declared = _declared_package_files(manifest)
        expected = PACKAGE_CONTROL_FILES | declared
        observed = frozenset(os.listdir(package_fd))
        if observed != expected:
            raise CanonicalMappingPackageSecurityError(
                "package_inventory_mismatch",
                f"package inventory mismatch: missing={sorted(expected - observed)} "
                f"extra={sorted(observed - expected)}",
            )

        payloads: dict[str, bytes] = {"manifest.json": manifest_bytes}
        seals = {"manifest.json": manifest_seal}
        total_bytes = len(manifest_bytes)
        for name in sorted(expected - {"manifest.json"}):
            limit = (
                MAX_CHECKSUM_BYTES
                if name == "MANIFEST.sha256"
                else MAX_PACKAGE_FILE_BYTES
            )
            payload, seal = _read_package_member(package_fd, name, max_bytes=limit)
            payloads[name] = payload
            seals[name] = seal
            total_bytes += len(payload)
            if total_bytes > MAX_PACKAGE_TOTAL_BYTES:
                raise CanonicalMappingPackageSecurityError(
                    "package_resource_limit",
                    "canonical mapping package exceeds total byte budget",
                )

        scan_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        scan_flags |= getattr(os, "O_NOFOLLOW", 0)
        scan_fd = os.open(".", scan_flags, dir_fd=package_fd)
        try:
            if frozenset(os.listdir(scan_fd)) != expected:
                raise CanonicalMappingPackageSecurityError(
                    "package_changed_during_read",
                    "package inventory changed while read",
                )
            for name in sorted(expected):
                _, closing_seal = _read_package_member(
                    scan_fd,
                    name,
                    max_bytes=(
                        MAX_MANIFEST_BYTES
                        if name == "manifest.json"
                        else (
                            MAX_CHECKSUM_BYTES
                            if name == "MANIFEST.sha256"
                            else MAX_PACKAGE_FILE_BYTES
                        )
                    ),
                )
                if closing_seal != seals[name]:
                    raise CanonicalMappingPackageSecurityError(
                        "package_changed_during_read",
                        f"package member changed while read: {name}",
                    )
        finally:
            os.close(scan_fd)

        with tempfile.TemporaryDirectory(prefix="sds-canonical-mapping-") as temporary:
            snapshot = Path(temporary) / "package"
            snapshot.mkdir(mode=0o700)
            snapshot_fd = os.open(
                snapshot,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                for name in sorted(expected):
                    _write_private_snapshot_member(snapshot_fd, name, payloads[name])
            finally:
                os.close(snapshot_fd)
            yield snapshot
    finally:
        os.close(package_fd)


def validate_canonical_mapping_package(
    package_dir: Path,
) -> CanonicalMappingImportReport:
    """Validate a canonical mapping package without writing to the database."""

    reported_package_dir = str(Path(os.path.abspath(package_dir)))
    try:
        with canonical_mapping_package_snapshot(package_dir) as snapshot:
            report = _validate_canonical_mapping_package_snapshot(snapshot)
    except CanonicalMappingPackageSecurityError as exc:
        return CanonicalMappingImportReport(
            package_dir=reported_package_dir,
            package_schema_version=None,
            mode="shadow_report_only",
            valid=False,
            file_counts={},
            manifest_hash=None,
            checksum_count=0,
            errors=[
                CanonicalMappingImportIssue(
                    file="manifest.json",
                    code=exc.code,
                    message=str(exc),
                )
            ],
        )
    return replace(report, package_dir=reported_package_dir)


def _validate_canonical_mapping_package_snapshot(
    package_dir: Path,
) -> CanonicalMappingImportReport:
    """Validate a package that is already held in a private snapshot."""

    package_dir = package_dir.resolve()
    errors: list[CanonicalMappingImportIssue] = []
    warnings: list[CanonicalMappingImportIssue] = []
    file_counts: dict[str, int] = {}
    parsed_manifest: CanonicalMappingPackageManifest | None = None
    manifest_hash: str | None = None
    checksum_count = 0

    manifest_path = package_dir / "manifest.json"
    checksum_path = package_dir / "MANIFEST.sha256"

    manifest_payload: Mapping[str, Any] = {}
    try:
        manifest_payload = _load_manifest(manifest_path)
        parsed_manifest = parse_canonical_mapping_manifest(manifest_payload)
        manifest_hash = _sha256_for(manifest_path)
    except (OSError, json.JSONDecodeError, CanonicalMappingPackageError) as exc:
        errors.append(
            CanonicalMappingImportIssue(
                file="manifest.json",
                code="manifest_invalid",
                message=str(exc),
            )
        )

    file_map = _resolve_manifest_files(package_dir, manifest_payload, errors)
    if parsed_manifest is not None:
        checksum_errors, checksum_count = _validate_checksum_file(
            checksum_path,
            file_map.values(),
            manifest_digests=_manifest_file_digests(manifest_payload),
        )
        errors.extend(checksum_errors)

    releases = _load_required_csv(
        file_map.get(RELEASES_FILE),
        RELEASES_FILE,
        RELEASE_HEADERS,
        errors,
        file_counts,
    )
    datapoints = _load_required_csv(
        file_map.get(DATAPOINTS_FILE),
        DATAPOINTS_FILE,
        DATAPOINT_HEADERS,
        errors,
        file_counts,
    )
    groups = _load_required_csv(
        file_map.get(ASSERTION_GROUPS_FILE),
        ASSERTION_GROUPS_FILE,
        ASSERTION_GROUP_HEADERS,
        errors,
        file_counts,
    )
    components = _load_required_csv(
        file_map.get(ASSERTION_COMPONENTS_FILE),
        ASSERTION_COMPONENTS_FILE,
        ASSERTION_COMPONENT_HEADERS,
        errors,
        file_counts,
    )

    _validate_row_sets(
        releases=releases,
        datapoints=datapoints,
        groups=groups,
        components=components,
        errors=errors,
        warnings=warnings,
    )
    relationship_counts = relationship_type_counts(groups)
    non_operational_counts = non_operational_relationship_type_counts(groups)
    operational_blockers = non_operational_relationship_blockers(non_operational_counts)
    raw_source_version = manifest_payload.get("source_version")
    source_version = (
        raw_source_version.strip()
        if isinstance(raw_source_version, str) and raw_source_version.strip()
        else None
    )

    return CanonicalMappingImportReport(
        package_dir=str(package_dir),
        package_schema_version=(
            parsed_manifest.package_schema_version if parsed_manifest else None
        ),
        mode="shadow_report_only",
        valid=not errors,
        file_counts=file_counts,
        manifest_hash=manifest_hash,
        checksum_count=checksum_count,
        source_version=source_version,
        operational_eligible=not errors and not non_operational_counts,
        relationship_type_counts=relationship_counts,
        non_operational_relationship_type_counts=non_operational_counts,
        operational_blockers=operational_blockers,
        errors=errors,
        warnings=warnings,
    )


def _load_manifest(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise CanonicalMappingPackageError("manifest must be a JSON object")
    return payload


def _resolve_manifest_files(
    package_dir: Path,
    manifest: Mapping[str, Any],
    errors: list[CanonicalMappingImportIssue],
) -> dict[str, Path]:
    files = manifest.get("files")
    if not isinstance(files, list):
        return {}

    resolved: dict[str, Path] = {}
    for item in files:
        if not isinstance(item, Mapping):
            continue
        raw_name = canonical_mapping_manifest_file_name(item)
        raw_path = str(
            item.get("path") or item.get("filename") or item.get("name") or ""
        ).strip()
        if not raw_name:
            continue
        resolved_path = (package_dir / raw_path).resolve()
        if not resolved_path.is_relative_to(package_dir):
            errors.append(
                CanonicalMappingImportIssue(
                    file="manifest.json",
                    code="manifest_path_outside_package",
                    message=f"manifest path escapes package directory: {raw_path}",
                )
            )
            continue
        for stem in (
            RELEASES_FILE,
            DATAPOINTS_FILE,
            ASSERTION_GROUPS_FILE,
            ASSERTION_COMPONENTS_FILE,
        ):
            if raw_name.startswith(stem):
                resolved[stem] = resolved_path
    return resolved


def _manifest_file_digests(manifest: Mapping[str, Any]) -> dict[str, str]:
    files = manifest.get("files")
    if not isinstance(files, list):
        return {}
    digests: dict[str, str] = {}
    for item in files:
        if not isinstance(item, Mapping):
            continue
        name = canonical_mapping_manifest_file_name(item)
        digest = item.get("sha256")
        if name and isinstance(digest, str):
            digests[name] = digest
    return digests


def _validate_checksum_file(
    checksum_path: Path,
    files: Iterable[Path],
    *,
    manifest_digests: Mapping[str, str] | None = None,
) -> tuple[list[CanonicalMappingImportIssue], int]:
    errors: list[CanonicalMappingImportIssue] = []
    if not checksum_path.exists():
        return (
            [
                CanonicalMappingImportIssue(
                    file="MANIFEST.sha256",
                    code="checksum_missing",
                    message=f"checksum file not found: {checksum_path}",
                )
            ],
            0,
        )

    paths = list(files)
    try:
        expected = _read_checksum_file_strict(checksum_path)
    except CanonicalMappingPackageError as exc:
        return (
            [
                CanonicalMappingImportIssue(
                    file="MANIFEST.sha256",
                    code="checksum_invalid",
                    message=str(exc),
                )
            ],
            0,
        )
    required_names = {path.name for path in paths}
    if set(expected) != required_names:
        errors.append(
            CanonicalMappingImportIssue(
                file="MANIFEST.sha256",
                code="checksum_invalid",
                message=(
                    "checksum inventory mismatch: "
                    f"missing={sorted(required_names - set(expected))} "
                    f"extra={sorted(set(expected) - required_names)}"
                ),
            )
        )
    if manifest_digests is not None:
        for name in sorted(required_names & set(expected) & set(manifest_digests)):
            if manifest_digests[name] != expected[name]:
                errors.append(
                    CanonicalMappingImportIssue(
                        file=name,
                        code="manifest_checksum_mismatch",
                        message=f"manifest and checksum sidecar disagree for {name}",
                    )
                )
    checked = 0
    for path in paths:
        name = path.name
        if name not in expected:
            errors.append(
                CanonicalMappingImportIssue(
                    file="MANIFEST.sha256",
                    code="checksum_entry_missing",
                    message=f"missing checksum entry for {name}",
                )
            )
            continue
        if not path.exists():
            errors.append(
                CanonicalMappingImportIssue(
                    file=name,
                    code="file_missing",
                    message=f"manifest file not found: {path}",
                )
            )
            continue
        actual = _sha256_for(path)
        checked += 1
        if actual != expected[name]:
            errors.append(
                CanonicalMappingImportIssue(
                    file=name,
                    code="checksum_mismatch",
                    message=(
                        f"checksum mismatch for {name}: "
                        f"expected {expected[name]}, got {actual}"
                    ),
                )
            )
    return errors, checked


def _read_checksum_file_strict(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise CanonicalMappingPackageError(
            "MANIFEST.sha256 must be strict UTF-8 text"
        ) from exc
    for line_number, line in enumerate(lines, start=1):
        if not line:
            continue
        match = _CHECKSUM_LINE_RE.fullmatch(line)
        if match is None:
            raise CanonicalMappingPackageError(f"invalid checksum line {line_number}")
        name = match.group("name")
        if name in checksums:
            raise CanonicalMappingPackageError(f"duplicate checksum entry for {name}")
        checksums[name] = match.group("digest")
    return checksums


def _read_checksum_file(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, filename = parts
        checksums[filename.lstrip("*")] = digest
    return checksums


def _load_required_csv(
    path: Path | None,
    logical_name: str,
    required_headers: tuple[str, ...],
    errors: list[CanonicalMappingImportIssue],
    file_counts: dict[str, int],
) -> list[dict[str, str]]:
    filename = f"{logical_name}.csv"
    if path is None:
        errors.append(
            CanonicalMappingImportIssue(
                file=filename,
                code="file_missing",
                message=f"{filename} is not listed in manifest.json",
            )
        )
        file_counts[filename] = 0
        return []
    if not path.exists():
        errors.append(
            CanonicalMappingImportIssue(
                file=filename,
                code="file_missing",
                message=f"file not found: {path}",
            )
        )
        file_counts[filename] = 0
        return []

    previous_field_limit = csv.field_size_limit(MAX_CSV_FIELD_CHARS)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []
            if len(headers) != len(set(headers)):
                errors.append(
                    CanonicalMappingImportIssue(
                        file=path.name,
                        code="duplicate_headers",
                        message="CSV headers must be unique",
                    )
                )
                file_counts[path.name] = 0
                return []
            if len(headers) > MAX_CSV_COLUMNS:
                errors.append(
                    CanonicalMappingImportIssue(
                        file=path.name,
                        code="package_resource_limit",
                        message="CSV exceeds column budget",
                    )
                )
                file_counts[path.name] = 0
                return []
            missing = [header for header in required_headers if header not in headers]
            if missing:
                errors.append(
                    CanonicalMappingImportIssue(
                        file=path.name,
                        code="missing_headers",
                        message="missing required headers: " + ", ".join(missing),
                    )
                )
                file_counts[path.name] = 0
                return []
            rows: list[dict[str, str]] = []
            try:
                for row_number, row in enumerate(reader, start=2):
                    if row_number - 1 > MAX_CSV_ROWS:
                        errors.append(
                            CanonicalMappingImportIssue(
                                file=path.name,
                                row_number=row_number,
                                code="package_resource_limit",
                                message="CSV exceeds row budget",
                            )
                        )
                        file_counts[path.name] = len(rows)
                        return []
                    if None in row:
                        errors.append(
                            CanonicalMappingImportIssue(
                                file=path.name,
                                row_number=row_number,
                                code="invalid_row_shape",
                                message="CSV row has more fields than headers",
                            )
                        )
                        file_counts[path.name] = len(rows)
                        return []
                    rows.append(_clean_row(row))
            except csv.Error as exc:
                errors.append(
                    CanonicalMappingImportIssue(
                        file=path.name,
                        code="package_resource_limit",
                        message=f"CSV parser rejected bounded input: {exc}",
                    )
                )
                file_counts[path.name] = len(rows)
                return []
    finally:
        csv.field_size_limit(previous_field_limit)

    file_counts[path.name] = len(rows)
    if not rows:
        errors.append(
            CanonicalMappingImportIssue(
                file=path.name,
                code="empty_file",
                message="canonical mapping package CSV must contain at least one row",
            )
        )
    return rows


def _validate_row_sets(
    *,
    releases: list[dict[str, str]],
    datapoints: list[dict[str, str]],
    groups: list[dict[str, str]],
    components: list[dict[str, str]],
    errors: list[CanonicalMappingImportIssue],
    warnings: list[CanonicalMappingImportIssue],
) -> None:
    release_keys = _validate_releases(releases, errors)
    datapoint_keys = _validate_datapoints(datapoints, release_keys, errors)
    group_keys = _validate_groups(groups, datapoint_keys, errors, warnings)
    _validate_components(components, group_keys, errors)


def _validate_releases(
    rows: list[dict[str, str]], errors: list[CanonicalMappingImportIssue]
) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for idx, row in enumerate(rows, start=2):
        _require_values(
            row,
            RELEASE_HEADERS,
            "sds_standard_releases.csv",
            idx,
            errors,
        )
        if row.get("release_date"):
            _validate_date(
                row["release_date"],
                "sds_standard_releases.csv",
                idx,
                "release_date",
                errors,
            )
        _validate_json_object_field(
            row,
            "provenance",
            "sds_standard_releases.csv",
            idx,
            errors,
        )
        key = (row.get("standard_id", ""), row.get("version", ""))
        _add_unique_key(keys, key, "sds_standard_releases.csv", idx, errors)
    return keys


def _validate_datapoints(
    rows: list[dict[str, str]],
    release_keys: set[tuple[str, str]],
    errors: list[CanonicalMappingImportIssue],
) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for idx, row in enumerate(rows, start=2):
        _require_values(
            row,
            DATAPOINT_HEADERS,
            "sds_standard_datapoints.csv",
            idx,
            errors,
        )
        release_key = (row.get("standard_id", ""), row.get("standard_version", ""))
        if release_key not in release_keys:
            errors.append(
                CanonicalMappingImportIssue(
                    file="sds_standard_datapoints.csv",
                    row_number=idx,
                    code="unknown_standard_release",
                    message=(
                        "datapoint references missing standard release "
                        f"{release_key[0]} {release_key[1]}"
                    ),
                )
            )
        _validate_json_object_field(
            row,
            "metadata_json",
            "sds_standard_datapoints.csv",
            idx,
            errors,
        )
        key = (*release_key, row.get("code", ""))
        _add_unique_key(keys, key, "sds_standard_datapoints.csv", idx, errors)
    return keys


def _validate_groups(
    rows: list[dict[str, str]],
    datapoint_keys: set[tuple[str, str, str]],
    errors: list[CanonicalMappingImportIssue],
    warnings: list[CanonicalMappingImportIssue],
) -> set[tuple[str, str, str, str]]:
    keys: set[tuple[str, str, str, str]] = set()
    for idx, row in enumerate(rows, start=2):
        _require_values(
            row,
            ASSERTION_GROUP_HEADERS,
            "sds_mapping_assertion_groups.csv",
            idx,
            errors,
        )
        datapoint_key = _source_datapoint_key(row)
        if datapoint_key not in datapoint_keys:
            errors.append(
                CanonicalMappingImportIssue(
                    file="sds_mapping_assertion_groups.csv",
                    row_number=idx,
                    code="unknown_source_datapoint",
                    message=(
                        "assertion group references missing source datapoint "
                        f"{datapoint_key}"
                    ),
                )
            )
        _validate_decimal_range(
            row.get("confidence", ""),
            "sds_mapping_assertion_groups.csv",
            idx,
            "confidence",
            errors,
        )
        _validate_datetime(
            row.get("valid_from", ""),
            "sds_mapping_assertion_groups.csv",
            idx,
            "valid_from",
            errors,
        )
        if row.get("valid_to"):
            _validate_datetime(
                row["valid_to"],
                "sds_mapping_assertion_groups.csv",
                idx,
                "valid_to",
                errors,
            )
        _validate_json_object_field(
            row,
            "provenance",
            "sds_mapping_assertion_groups.csv",
            idx,
            errors,
        )
        if (
            row.get("coverage_status") in {"gap", "excluded"}
            and row.get("approval_status") == "approved"
        ):
            warnings.append(
                CanonicalMappingImportIssue(
                    file="sds_mapping_assertion_groups.csv",
                    row_number=idx,
                    code="approved_gap_or_exclusion",
                    message="approved gap/exclusion must be reviewed before cutover",
                )
            )
        key = (*datapoint_key, row.get("mapping_profile", ""))
        _add_unique_key(keys, key, "sds_mapping_assertion_groups.csv", idx, errors)
    return keys


def _validate_components(
    rows: list[dict[str, str]],
    group_keys: set[tuple[str, str, str, str]],
    errors: list[CanonicalMappingImportIssue],
) -> None:
    component_orders: set[tuple[str, str, str, str, int]] = set()
    for idx, row in enumerate(rows, start=2):
        _require_values(
            row,
            ASSERTION_COMPONENT_HEADERS,
            "sds_mapping_assertion_components.csv",
            idx,
            errors,
        )
        group_key = (*_source_datapoint_key(row), row.get("mapping_profile", ""))
        if group_key not in group_keys:
            errors.append(
                CanonicalMappingImportIssue(
                    file="sds_mapping_assertion_components.csv",
                    row_number=idx,
                    code="unknown_assertion_group",
                    message=f"component references missing assertion group {group_key}",
                )
            )
        order = _parse_int(
            row.get("component_order", ""),
            "sds_mapping_assertion_components.csv",
            idx,
            "component_order",
            errors,
        )
        if order is not None:
            _add_unique_key(
                component_orders,
                (*group_key, order),
                "sds_mapping_assertion_components.csv",
                idx,
                errors,
                code="duplicate_component_order",
            )
        if row.get("coverage_fraction"):
            _validate_decimal_range(
                row["coverage_fraction"],
                "sds_mapping_assertion_components.csv",
                idx,
                "coverage_fraction",
                errors,
            )
        _parse_int(
            row.get("sygris_revision", ""),
            "sds_mapping_assertion_components.csv",
            idx,
            "sygris_revision",
            errors,
            minimum=1,
        )
        _validate_sygris_canonical_uri(
            row.get("sygris_canonical_uri", ""),
            "sds_mapping_assertion_components.csv",
            idx,
            errors,
        )


def load_canonical_mapping_package_rows(
    package_dir: Path,
) -> CanonicalMappingPackageRows:
    """Load package CSV rows after validation has confirmed the contract."""

    try:
        with canonical_mapping_package_snapshot(package_dir) as snapshot:
            return _load_canonical_mapping_package_rows_snapshot(snapshot)
    except CanonicalMappingPackageSecurityError as exc:
        raise CanonicalMappingPackageError(
            f"cannot load canonical mapping package rows securely: {exc}"
        ) from exc


def _load_canonical_mapping_package_rows_snapshot(
    package_dir: Path,
) -> CanonicalMappingPackageRows:
    """Load rows from a private, closed package snapshot."""

    package_dir = package_dir.resolve()
    manifest = _load_manifest(package_dir / "manifest.json")
    errors: list[CanonicalMappingImportIssue] = []
    file_counts: dict[str, int] = {}
    file_map = _resolve_manifest_files(package_dir, manifest, errors)

    releases = _load_required_csv(
        file_map.get(RELEASES_FILE),
        RELEASES_FILE,
        RELEASE_HEADERS,
        errors,
        file_counts,
    )
    datapoints = _load_required_csv(
        file_map.get(DATAPOINTS_FILE),
        DATAPOINTS_FILE,
        DATAPOINT_HEADERS,
        errors,
        file_counts,
    )
    groups = _load_required_csv(
        file_map.get(ASSERTION_GROUPS_FILE),
        ASSERTION_GROUPS_FILE,
        ASSERTION_GROUP_HEADERS,
        errors,
        file_counts,
    )
    components = _load_required_csv(
        file_map.get(ASSERTION_COMPONENTS_FILE),
        ASSERTION_COMPONENTS_FILE,
        ASSERTION_COMPONENT_HEADERS,
        errors,
        file_counts,
    )

    if errors:
        first = errors[0]
        raise CanonicalMappingPackageError(
            f"cannot load canonical mapping package rows: {first.file}: {first.message}"
        )

    return CanonicalMappingPackageRows(
        releases=releases,
        datapoints=datapoints,
        assertion_groups=groups,
        assertion_components=components,
    )


def _require_values(
    row: Mapping[str, str],
    fields: Iterable[str],
    filename: str,
    row_number: int,
    errors: list[CanonicalMappingImportIssue],
) -> None:
    missing = [field for field in fields if not row.get(field)]
    if missing:
        errors.append(
            CanonicalMappingImportIssue(
                file=filename,
                row_number=row_number,
                code="missing_required_values",
                message="missing required values: " + ", ".join(missing),
            )
        )


def _add_unique_key(
    keys: set[Any],
    key: Any,
    filename: str,
    row_number: int,
    errors: list[CanonicalMappingImportIssue],
    *,
    code: str = "duplicate_key",
) -> None:
    if key in keys:
        errors.append(
            CanonicalMappingImportIssue(
                file=filename,
                row_number=row_number,
                code=code,
                message=f"duplicate key: {key}",
            )
        )
        return
    keys.add(key)


def _source_datapoint_key(row: Mapping[str, str]) -> tuple[str, str, str]:
    return (
        row.get("source_standard_id", ""),
        row.get("source_standard_version", ""),
        row.get("source_code", ""),
    )


def _validate_date(
    value: str,
    filename: str,
    row_number: int,
    field_name: str,
    errors: list[CanonicalMappingImportIssue],
) -> None:
    try:
        date.fromisoformat(value)
    except ValueError:
        errors.append(_invalid_value_issue(filename, row_number, field_name, value))


def _validate_datetime(
    value: str,
    filename: str,
    row_number: int,
    field_name: str,
    errors: list[CanonicalMappingImportIssue],
) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(_invalid_value_issue(filename, row_number, field_name, value))


def _validate_decimal_range(
    value: str,
    filename: str,
    row_number: int,
    field_name: str,
    errors: list[CanonicalMappingImportIssue],
) -> None:
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        errors.append(_invalid_value_issue(filename, row_number, field_name, value))
        return
    if not parsed.is_finite() or parsed < 0 or parsed > 1:
        errors.append(_invalid_value_issue(filename, row_number, field_name, value))


def _validate_json_object_field(
    row: Mapping[str, str],
    field_name: str,
    filename: str,
    row_number: int,
    errors: list[CanonicalMappingImportIssue],
) -> None:
    value = row.get(field_name, "")
    if not value:
        return
    try:
        parsed = loads_strict_json(value)
    except StrictJsonError:
        errors.append(_invalid_value_issue(filename, row_number, field_name, value))
        return
    if not isinstance(parsed, dict):
        errors.append(_invalid_value_issue(filename, row_number, field_name, value))


def _parse_int(
    value: str,
    filename: str,
    row_number: int,
    field_name: str,
    errors: list[CanonicalMappingImportIssue],
    *,
    minimum: int | None = None,
) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        errors.append(_invalid_value_issue(filename, row_number, field_name, value))
        return None
    if minimum is not None and parsed < minimum:
        errors.append(_invalid_value_issue(filename, row_number, field_name, value))
        return None
    return parsed


def _validate_sygris_canonical_uri(
    value: str,
    filename: str,
    row_number: int,
    errors: list[CanonicalMappingImportIssue],
) -> None:
    if not value:
        return
    if value.startswith(SYGRIS_CANONICAL_URI_PREFIXES):
        return
    errors.append(
        CanonicalMappingImportIssue(
            file=filename,
            row_number=row_number,
            code="invalid_sygris_canonical_uri",
            message=(
                "sygris_canonical_uri must use the syg: CURIE prefix or the "
                f"{DEFAULT_NAMESPACES.prefixes['syg']} IRI namespace"
            ),
        )
    )


def _invalid_value_issue(
    filename: str, row_number: int, field_name: str, value: str
) -> CanonicalMappingImportIssue:
    return CanonicalMappingImportIssue(
        file=filename,
        row_number=row_number,
        code="invalid_value",
        message=f"invalid {field_name}: {value}",
    )


def _clean_row(row: Mapping[str, Any]) -> dict[str, str]:
    return {str(key): str(value or "").strip() for key, value in row.items()}


def _sha256_for(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
