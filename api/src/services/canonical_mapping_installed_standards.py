"""Installed-standard compatibility checks for canonical mapping packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from src.database.models import StandardDatapoint, StandardRelease
from src.services.canonical_mapping_import import (
    CanonicalMappingImportReport,
    CanonicalMappingPackageRows,
    load_canonical_mapping_package_rows,
    validate_canonical_mapping_package,
)

StandardReleaseKey = tuple[str, str]
DatapointKey = tuple[str, str, str]
AssertionGroupKey = tuple[str, str, str, str]


@dataclass(frozen=True)
class CanonicalMappingInstalledStandardIssue:
    """Compatibility issue between a mapping package and installed standards."""

    code: str
    message: str
    standard_id: str | None = None
    version: str | None = None
    source_code: str | None = None
    mapping_profile: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "standard_id": self.standard_id,
            "version": self.version,
            "source_code": self.source_code,
            "mapping_profile": self.mapping_profile,
        }


@dataclass(frozen=True)
class CanonicalMappingInstalledStandardReport:
    """Package compatibility report for a concrete SDS standard install set."""

    package_dir: str
    compatibility_mode: str
    validation: CanonicalMappingImportReport
    installed_standard_releases: tuple[StandardReleaseKey, ...]
    status_counts: dict[str, int] = field(default_factory=dict)
    importable_assertion_group_keys: tuple[AssertionGroupKey, ...] = ()
    pending_assertion_group_keys: tuple[AssertionGroupKey, ...] = ()
    blocked_assertion_group_keys: tuple[AssertionGroupKey, ...] = ()
    issues: tuple[CanonicalMappingInstalledStandardIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return self.validation.valid

    @property
    def strict(self) -> bool:
        return self.compatibility_mode == "strict"

    @property
    def importable_assertion_group_count(self) -> int:
        return len(self.importable_assertion_group_keys)

    @property
    def pending_assertion_group_count(self) -> int:
        return len(self.pending_assertion_group_keys)

    @property
    def blocked_assertion_group_count(self) -> int:
        return len(self.blocked_assertion_group_keys)

    @property
    def operational_eligible(self) -> bool:
        if not self.validation.operational_eligible:
            return False
        if self.strict:
            return not self.issues and self.importable_assertion_group_count > 0
        return (
            self.importable_assertion_group_count > 0
            and self.blocked_assertion_group_count == 0
        )

    @property
    def blockers(self) -> list[str]:
        if not self.validation.valid:
            return ["canonical mapping package validation failed"]
        if self.strict and self.issues:
            return [issue.message for issue in self.issues]
        if self.blocked_assertion_group_count:
            return [
                issue.message
                for issue in self.issues
                if issue.code.startswith("blocked_")
            ]
        if not self.importable_assertion_group_count:
            return ["no assertion groups are compatible with installed standards"]
        return []

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_dir": self.package_dir,
            "compatibility_mode": self.compatibility_mode,
            "valid": self.valid,
            "operational_eligible": self.operational_eligible,
            "installed_standard_releases": [
                {"standard_id": standard_id, "version": version}
                for standard_id, version in self.installed_standard_releases
            ],
            "status_counts": self.status_counts,
            "importable_assertion_group_count": self.importable_assertion_group_count,
            "pending_assertion_group_count": self.pending_assertion_group_count,
            "blocked_assertion_group_count": self.blocked_assertion_group_count,
            "importable_assertion_group_keys": [
                _assertion_key_as_dict(key)
                for key in self.importable_assertion_group_keys
            ],
            "pending_assertion_group_keys": [
                _assertion_key_as_dict(key) for key in self.pending_assertion_group_keys
            ],
            "blocked_assertion_group_keys": [
                _assertion_key_as_dict(key) for key in self.blocked_assertion_group_keys
            ],
            "issues": [issue.as_dict() for issue in self.issues],
            "blockers": self.blockers,
        }


def active_standard_release_keys_from_db(db: Session) -> set[StandardReleaseKey]:
    """Return active standard releases installed in this SDS database."""

    return {
        (release.standard_id, release.version)
        for release in db.query(StandardRelease).filter(
            StandardRelease.lifecycle_status == "active"
        )
    }


def active_standard_datapoint_keys_from_db(db: Session) -> set[DatapointKey]:
    """Return active datapoints installed in this SDS database."""

    rows = (
        db.query(StandardDatapoint, StandardRelease)
        .join(
            StandardRelease, StandardDatapoint.standard_release_id == StandardRelease.id
        )
        .filter(
            StandardRelease.lifecycle_status == "active",
            StandardDatapoint.lifecycle_status == "active",
        )
        .all()
    )
    return {
        (release.standard_id, release.version, datapoint.code)
        for datapoint, release in rows
    }


def validate_canonical_mapping_installed_standard_compatibility(
    *,
    package_dir: Path,
    db: Session | None = None,
    installed_standard_releases: Iterable[StandardReleaseKey] | None = None,
    compatibility_mode: str = "strict",
    require_installed_datapoints: bool = True,
) -> CanonicalMappingInstalledStandardReport:
    """Validate whether a canonical mapping package can be live in this SDS.

    Package validation answers "is the package internally coherent?". This
    compatibility layer answers "which rows can be activated here, given the
    standards installed in this SDS instance?".
    """

    package_dir = package_dir.resolve()
    mode = _normalize_mode(compatibility_mode)
    validation = validate_canonical_mapping_package(package_dir)
    installed_releases = _normalize_standard_release_keys(
        installed_standard_releases
        if installed_standard_releases is not None
        else active_standard_release_keys_from_db(db) if db is not None else ()
    )
    if not validation.valid:
        return CanonicalMappingInstalledStandardReport(
            package_dir=str(package_dir),
            compatibility_mode=mode,
            validation=validation,
            installed_standard_releases=tuple(sorted(installed_releases)),
        )

    rows = load_canonical_mapping_package_rows(package_dir)
    active_datapoints = (
        active_standard_datapoint_keys_from_db(db)
        if db is not None and require_installed_datapoints
        else None
    )

    status_counts: dict[str, int] = {}
    importable: list[AssertionGroupKey] = []
    pending: list[AssertionGroupKey] = []
    blocked: list[AssertionGroupKey] = []
    issues: list[CanonicalMappingInstalledStandardIssue] = []
    issued_missing_releases: set[StandardReleaseKey] = set()

    package_release_keys = {
        (row["standard_id"], row["version"]) for row in rows.releases
    }
    for standard_id, version in sorted(package_release_keys - installed_releases):
        issued_missing_releases.add((standard_id, version))
        issues.append(
            CanonicalMappingInstalledStandardIssue(
                code="missing_standard_release",
                standard_id=standard_id,
                version=version,
                message=(
                    f"{standard_id} {version} is referenced by the mapping package "
                    "but is not installed in this SDS instance."
                ),
            )
        )

    for row in rows.assertion_groups:
        key = _assertion_key(row)
        release_key = (row["source_standard_id"], row["source_standard_version"])
        datapoint_key = (*release_key, row["source_code"])
        if release_key not in installed_releases:
            _increment(status_counts, "pending_missing_standard")
            pending.append(key)
            if release_key not in issued_missing_releases:
                issues.append(
                    CanonicalMappingInstalledStandardIssue(
                        code="missing_standard_release",
                        standard_id=release_key[0],
                        version=release_key[1],
                        source_code=row["source_code"],
                        mapping_profile=row["mapping_profile"],
                        message=(
                            f"{release_key[0]} {release_key[1]} is not installed; "
                            f"{row['source_code']} remains pending."
                        ),
                    )
                )
            continue
        if active_datapoints is not None and datapoint_key not in active_datapoints:
            _increment(status_counts, "blocked_missing_datapoint")
            blocked.append(key)
            issues.append(
                CanonicalMappingInstalledStandardIssue(
                    code="blocked_missing_datapoint",
                    standard_id=release_key[0],
                    version=release_key[1],
                    source_code=row["source_code"],
                    mapping_profile=row["mapping_profile"],
                    message=(
                        f"{release_key[0]} {release_key[1]} / {row['source_code']} "
                        "is not an active installed datapoint."
                    ),
                )
            )
            continue
        _increment(status_counts, "installable_now")
        importable.append(key)

    return CanonicalMappingInstalledStandardReport(
        package_dir=str(package_dir),
        compatibility_mode=mode,
        validation=validation,
        installed_standard_releases=tuple(sorted(installed_releases)),
        status_counts=dict(sorted(status_counts.items())),
        importable_assertion_group_keys=tuple(importable),
        pending_assertion_group_keys=tuple(pending),
        blocked_assertion_group_keys=tuple(blocked),
        issues=tuple(issues),
    )


def filter_rows_for_installed_standard_compatibility(
    rows: CanonicalMappingPackageRows,
    compatibility: CanonicalMappingInstalledStandardReport,
) -> CanonicalMappingPackageRows:
    """Return only package rows eligible for activation in this SDS instance."""

    importable_keys = set(compatibility.importable_assertion_group_keys)
    release_keys = {
        (key[0], key[1]) for key in compatibility.importable_assertion_group_keys
    }
    datapoint_keys = {
        (key[0], key[1], key[2])
        for key in compatibility.importable_assertion_group_keys
    }
    return CanonicalMappingPackageRows(
        releases=[
            row
            for row in rows.releases
            if (row["standard_id"], row["version"]) in release_keys
        ],
        datapoints=[
            row
            for row in rows.datapoints
            if (row["standard_id"], row["standard_version"], row["code"])
            in datapoint_keys
        ],
        assertion_groups=[
            row
            for row in rows.assertion_groups
            if _assertion_key(row) in importable_keys
        ],
        assertion_components=[
            row
            for row in rows.assertion_components
            if _assertion_key(row) in importable_keys
        ],
    )


def _normalize_standard_release_keys(
    values: Iterable[StandardReleaseKey],
) -> set[StandardReleaseKey]:
    normalized: set[StandardReleaseKey] = set()
    for standard_id, version in values:
        normalized.add((str(standard_id).strip(), str(version).strip()))
    return {key for key in normalized if key[0] and key[1]}


def _normalize_mode(value: str) -> str:
    mode = (value or "strict").strip().lower()
    if mode not in {"strict", "partial"}:
        raise ValueError("compatibility_mode must be 'strict' or 'partial'")
    return mode


def _assertion_key(row: dict[str, str]) -> AssertionGroupKey:
    return (
        row["source_standard_id"],
        row["source_standard_version"],
        row["source_code"],
        row["mapping_profile"],
    )


def _assertion_key_as_dict(key: AssertionGroupKey) -> dict[str, str]:
    return {
        "standard_id": key[0],
        "version": key[1],
        "code": key[2],
        "mapping_profile": key[3],
    }


def _increment(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1
