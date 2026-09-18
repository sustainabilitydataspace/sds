"""Shadow/report-only validation for canonical mapping packages."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

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

RELEASES_FILE = "sds_standard_releases"
DATAPOINTS_FILE = "sds_standard_datapoints"
ASSERTION_GROUPS_FILE = "sds_mapping_assertion_groups"
ASSERTION_COMPONENTS_FILE = "sds_mapping_assertion_components"

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


def validate_canonical_mapping_package(
    package_dir: Path,
) -> CanonicalMappingImportReport:
    """Validate a canonical mapping package without writing to the database."""

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
            checksum_path, file_map.values()
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


def _validate_checksum_file(
    checksum_path: Path, files: Iterable[Path]
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

    expected = _read_checksum_file(checksum_path)
    checked = 0
    for path in files:
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

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
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
        rows = [_clean_row(row) for row in reader]

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
        parsed = json.loads(value)
    except json.JSONDecodeError:
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
