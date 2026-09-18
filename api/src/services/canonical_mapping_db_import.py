"""Database shadow import for canonical Sygris mapping packages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.database.models import (
    CanonicalConcept,
    ConceptState,
    MappingAssertionComponent,
    MappingAssertionGroup,
    StandardDatapoint,
    StandardRelease,
)
from src.services.canonical_mapping_import import (
    CanonicalMappingImportReport,
    load_canonical_mapping_package_rows,
    validate_canonical_mapping_package,
)
from src.services.canonical_mapping_import_lock import CANONICAL_MAPPING_IMPORT_LOCK_KEY
from src.services.canonical_mapping_installed_standards import (
    StandardReleaseKey,
    filter_rows_for_installed_standard_compatibility,
    validate_canonical_mapping_installed_standard_compatibility,
)
from src.services.canonical_mapping_relationship_policy import (
    non_operational_relationship_blockers,
    non_operational_relationship_type_counts,
    relationship_type_counts,
)

DEFAULT_CREATED_BY = "canonical_mapping_shadow_import"
_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")


@dataclass
class CanonicalMappingDbImportReport:
    """Outcome of a DB-backed canonical mapping package import."""

    package_dir: str
    mode: str
    dry_run: bool
    valid: bool
    committed: bool
    validation: CanonicalMappingImportReport
    blocked: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    relationship_type_counts: dict[str, int] = field(default_factory=dict)
    compatibility: dict[str, Any] = field(default_factory=dict)
    materialized_pairwise_mappings_written: int = 0
    legacy_standard_mappings_written: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_dir": self.package_dir,
            "mode": self.mode,
            "dry_run": self.dry_run,
            "valid": self.valid,
            "blocked": self.blocked,
            "committed": self.committed,
            "validation": self.validation.as_dict(),
            "counts": self.counts,
            "blockers": self.blockers,
            "relationship_type_counts": self.relationship_type_counts,
            "compatibility": self.compatibility,
            "materialized_pairwise_mappings_written": (
                self.materialized_pairwise_mappings_written
            ),
            "legacy_standard_mappings_written": self.legacy_standard_mappings_written,
        }


def import_canonical_mapping_package_to_db(
    *,
    package_dir: Path,
    db: Session,
    dry_run: bool = False,
    created_by: str = DEFAULT_CREATED_BY,
    allow_non_operational_relationships: bool = False,
    installed_standard_releases: set[StandardReleaseKey] | None = None,
    allow_partial_installed_standards: bool = False,
) -> CanonicalMappingDbImportReport:
    """Validate and import a package into canonical shadow tables only.

    This function intentionally does not write legacy ``standard_mappings`` rows
    and does not generate ``materialized_pairwise_mappings``. Run committed
    pairwise materialization explicitly when the public canonical
    ``/api/v1/mappings`` read-model should change.
    """

    package_dir = package_dir.resolve()
    _acquire_import_lock(db)
    validation = validate_canonical_mapping_package(package_dir)
    report = CanonicalMappingDbImportReport(
        package_dir=str(package_dir),
        mode="shadow_db_import",
        dry_run=dry_run,
        valid=validation.valid,
        committed=False,
        validation=validation,
    )
    if not validation.valid:
        return report

    rows = load_canonical_mapping_package_rows(package_dir)
    if installed_standard_releases is not None:
        compatibility = validate_canonical_mapping_installed_standard_compatibility(
            package_dir=package_dir,
            db=db,
            installed_standard_releases=installed_standard_releases,
            compatibility_mode=(
                "partial" if allow_partial_installed_standards else "strict"
            ),
        )
        report.compatibility = compatibility.as_dict()
        if compatibility.pending_assertion_group_count:
            report.counts["mapping_assertion_groups_pending_missing_standard"] = (
                compatibility.pending_assertion_group_count
            )
        if compatibility.blocked_assertion_group_count:
            report.counts["mapping_assertion_groups_blocked_missing_datapoint"] = (
                compatibility.blocked_assertion_group_count
            )
        if not compatibility.operational_eligible:
            report.mode = "shadow_db_import_blocked"
            report.blocked = True
            report.blockers = compatibility.blockers
            db.rollback()
            return report
        rows = filter_rows_for_installed_standard_compatibility(rows, compatibility)

    report.relationship_type_counts = relationship_type_counts(rows.assertion_groups)
    non_operational_counts = non_operational_relationship_type_counts(
        rows.assertion_groups
    )
    if non_operational_counts:
        blocked_count = sum(non_operational_counts.values())
        report.counts["non_operational_assertion_groups_present"] = blocked_count
        for relationship_type, count in non_operational_counts.items():
            report.counts[f"non_operational_relationship_{relationship_type}"] = count
        if not dry_run and not allow_non_operational_relationships:
            report.mode = "shadow_db_import_blocked"
            report.blocked = True
            report.blockers = non_operational_relationship_blockers(
                non_operational_counts
            )
            report.counts["non_operational_assertion_groups_blocked"] = blocked_count
            db.rollback()
            return report

    release_by_key: dict[tuple[str, str], StandardRelease] = {}
    datapoint_by_key: dict[tuple[str, str, str], StandardDatapoint] = {}
    concept_by_key: dict[tuple[str, int], CanonicalConcept] = {}
    group_by_key: dict[tuple[str, str, str, str], MappingAssertionGroup] = {}

    try:
        for row in rows.releases:
            release = _upsert_standard_release(
                db,
                row,
                report,
                created_by=created_by,
                package_manifest_hash=validation.manifest_hash,
            )
            release_by_key[(release.standard_id, release.version)] = release
        db.flush()

        for row in rows.datapoints:
            key = (row["standard_id"], row["standard_version"])
            datapoint = _upsert_standard_datapoint(
                db,
                row,
                release_by_key[key],
                report,
                created_by=created_by,
            )
            datapoint_by_key[(*key, datapoint.code)] = datapoint
        db.flush()

        for row in rows.assertion_components:
            key = (row["sygris_canonical_uri"], int(row["sygris_revision"]))
            if key not in concept_by_key:
                concept_by_key[key] = _get_or_create_sygris_concept(
                    db,
                    canonical_uri=key[0],
                    revision=key[1],
                    report=report,
                    created_by=created_by,
                )
        db.flush()

        for row in rows.assertion_groups:
            source_key = (
                row["source_standard_id"],
                row["source_standard_version"],
                row["source_code"],
            )
            group = _upsert_assertion_group(
                db,
                row,
                datapoint_by_key[source_key],
                report,
                created_by=created_by,
                package_manifest_hash=validation.manifest_hash,
            )
            group_by_key[(*source_key, group.mapping_profile)] = group
        db.flush()

        for row in rows.assertion_components:
            group_key = (
                row["source_standard_id"],
                row["source_standard_version"],
                row["source_code"],
                row["mapping_profile"],
            )
            concept_key = (row["sygris_canonical_uri"], int(row["sygris_revision"]))
            _upsert_assertion_component(
                db,
                row,
                assertion_group=group_by_key[group_key],
                canonical_concept=concept_by_key[concept_key],
                report=report,
            )

        _retire_absent_assertion_groups(
            db,
            rows.assertion_groups,
            release_by_key=release_by_key,
            report=report,
            created_by=created_by,
        )

        db.flush()
        if dry_run:
            db.rollback()
        else:
            db.commit()
            report.committed = True
        return report
    except Exception:
        db.rollback()
        raise


def _upsert_standard_release(
    db: Session,
    row: dict[str, str],
    report: CanonicalMappingDbImportReport,
    *,
    created_by: str,
    package_manifest_hash: str | None,
) -> StandardRelease:
    release = (
        db.query(StandardRelease)
        .filter(
            StandardRelease.standard_id == row["standard_id"],
            StandardRelease.version == row["version"],
        )
        .first()
    )
    values = {
        "standard_id": row["standard_id"],
        "name": row["name"],
        "version": row["version"],
        "release_date": _parse_date(row.get("release_date")),
        "source_url": _empty_to_none(row.get("source_url")),
        "lifecycle_status": row.get("lifecycle_status") or "active",
        "provenance": _merge_package_provenance(
            _parse_json_object(row.get("provenance")),
            package_manifest_hash=package_manifest_hash,
            source_file="sds_standard_releases.csv",
        ),
    }
    if release is None:
        release = StandardRelease(**values, created_by=created_by)
        db.add(release)
        _count(report, "standard_releases", "created")
        return release

    if _apply_values(release, values):
        _count(report, "standard_releases", "updated")
    else:
        _count(report, "standard_releases", "unchanged")
    return release


def _upsert_standard_datapoint(
    db: Session,
    row: dict[str, str],
    release: StandardRelease,
    report: CanonicalMappingDbImportReport,
    *,
    created_by: str,
) -> StandardDatapoint:
    datapoint = (
        db.query(StandardDatapoint)
        .filter(
            StandardDatapoint.standard_release_id == release.id,
            StandardDatapoint.code == row["code"],
        )
        .first()
    )
    values = {
        "standard_release": release,
        "code": row["code"],
        "label": row["label"],
        "disclosure_text": _empty_to_none(row.get("disclosure_text")),
        "datapoint_type": _empty_to_none(row.get("datapoint_type")),
        "unit": _empty_to_none(row.get("unit")),
        "lifecycle_status": row.get("lifecycle_status") or "active",
        "metadata_json": _parse_json_object(row.get("metadata_json")),
    }
    if datapoint is None:
        datapoint = StandardDatapoint(**values, created_by=created_by)
        db.add(datapoint)
        _count(report, "standard_datapoints", "created")
        return datapoint

    if _apply_values(datapoint, values):
        _count(report, "standard_datapoints", "updated")
    else:
        _count(report, "standard_datapoints", "unchanged")
    return datapoint


def _get_or_create_sygris_concept(
    db: Session,
    *,
    canonical_uri: str,
    revision: int,
    report: CanonicalMappingDbImportReport,
    created_by: str,
) -> CanonicalConcept:
    concept = (
        db.query(CanonicalConcept)
        .filter(
            CanonicalConcept.canonical_uri == canonical_uri,
            CanonicalConcept.revision == revision,
        )
        .first()
    )
    if concept is not None:
        _count(report, "sygris_canonical_concepts", "unchanged")
        return concept

    concept = CanonicalConcept(
        canonical_uri=canonical_uri,
        revision=revision,
        label=_label_from_canonical_uri(canonical_uri),
        description=(
            "Sygris canonical pivot seeded from a canonical mapping package "
            "shadow import."
        ),
        taxonomy="Sygris",
        concept_type="mapping_pivot",
        concept_state=ConceptState.CATALOGUED,
        created_by=created_by,
    )
    db.add(concept)
    _count(report, "sygris_canonical_concepts", "created")
    return concept


def _upsert_assertion_group(
    db: Session,
    row: dict[str, str],
    source_datapoint: StandardDatapoint,
    report: CanonicalMappingDbImportReport,
    *,
    created_by: str,
    package_manifest_hash: str | None,
) -> MappingAssertionGroup:
    group = None
    assertion_hash = _empty_to_none(row.get("assertion_hash"))
    if assertion_hash:
        group = (
            db.query(MappingAssertionGroup)
            .filter(MappingAssertionGroup.assertion_hash == assertion_hash)
            .first()
        )
    if group is None and not assertion_hash:
        group = (
            db.query(MappingAssertionGroup)
            .filter(
                MappingAssertionGroup.source_datapoint_id == source_datapoint.id,
                MappingAssertionGroup.mapping_profile == row["mapping_profile"],
            )
            .first()
        )

    values = {
        "source_datapoint": source_datapoint,
        "mapping_profile": row["mapping_profile"],
        "relationship_type": row["relationship_type"],
        "coverage_status": row["coverage_status"],
        "confidence": Decimal(row["confidence"]),
        "rationale": _empty_to_none(row.get("rationale")),
        "coverage_summary": _empty_to_none(row.get("coverage_summary")),
        "difference_summary": _empty_to_none(row.get("difference_summary")),
        "valid_from": _parse_datetime(row["valid_from"]),
        "valid_to": _parse_datetime(row.get("valid_to")),
        "approval_status": row["approval_status"],
        "publication_status": row["publication_status"],
        "assertion_hash": assertion_hash,
        "provenance": _merge_package_provenance(
            _parse_json_object(row.get("provenance")),
            package_manifest_hash=package_manifest_hash,
            source_file="sds_mapping_assertion_groups.csv",
        ),
    }
    if group is None:
        group = MappingAssertionGroup(**values, created_by=created_by)
        db.add(group)
        _count(report, "mapping_assertion_groups", "created")
        return group

    if _apply_values(group, values):
        _count(report, "mapping_assertion_groups", "updated")
    else:
        _count(report, "mapping_assertion_groups", "unchanged")
    return group


def _upsert_assertion_component(
    db: Session,
    row: dict[str, str],
    *,
    assertion_group: MappingAssertionGroup,
    canonical_concept: CanonicalConcept,
    report: CanonicalMappingDbImportReport,
) -> MappingAssertionComponent:
    component_order = int(row["component_order"])
    component = (
        db.query(MappingAssertionComponent)
        .filter(
            MappingAssertionComponent.assertion_group_id == assertion_group.id,
            MappingAssertionComponent.component_order == component_order,
        )
        .first()
    )
    values = {
        "assertion_group": assertion_group,
        "canonical_concept": canonical_concept,
        "component_order": component_order,
        "component_role": row.get("component_role") or "primary",
        "coverage_fraction": _parse_decimal(row.get("coverage_fraction")),
        "match_scope": _empty_to_none(row.get("match_scope")),
        "mismatch_scope": _empty_to_none(row.get("mismatch_scope")),
        "transformation_rule": _empty_to_none(row.get("transformation_rule")),
        "rationale": _empty_to_none(row.get("rationale")),
    }
    if component is None:
        component = MappingAssertionComponent(**values)
        db.add(component)
        _count(report, "mapping_assertion_components", "created")
        return component

    if _apply_values(component, values):
        _count(report, "mapping_assertion_components", "updated")
    else:
        _count(report, "mapping_assertion_components", "unchanged")
    return component


def _retire_absent_assertion_groups(
    db: Session,
    assertion_rows: list[dict[str, str]],
    *,
    release_by_key: dict[tuple[str, str], StandardRelease],
    report: CanonicalMappingDbImportReport,
    created_by: str,
) -> None:
    """Mark current shadow assertions absent from the latest cumulative package stale."""

    package_hashes = {
        row["assertion_hash"]
        for row in assertion_rows
        if _empty_to_none(row.get("assertion_hash"))
    }
    if not package_hashes:
        return
    release_ids = {release.id for release in release_by_key.values() if release.id}
    mapping_profiles = {row["mapping_profile"] for row in assertion_rows}
    if not release_ids or not mapping_profiles:
        return

    stale_groups = (
        db.query(MappingAssertionGroup)
        .join(StandardDatapoint)
        .filter(
            StandardDatapoint.standard_release_id.in_(release_ids),
            MappingAssertionGroup.mapping_profile.in_(mapping_profiles),
            MappingAssertionGroup.valid_to.is_(None),
            MappingAssertionGroup.created_by == created_by,
            MappingAssertionGroup.assertion_hash.notin_(package_hashes),
        )
        .all()
    )
    if not stale_groups:
        return
    retired_at = datetime.now(timezone.utc).replace(tzinfo=None)
    for group in stale_groups:
        group.valid_to = retired_at
        _count(report, "mapping_assertion_groups", "retired_absent")


def _apply_values(obj: Any, values: dict[str, Any]) -> bool:
    changed = False
    for key, value in values.items():
        if getattr(obj, key) != value:
            setattr(obj, key, value)
            changed = True
    return changed


def _count(report: CanonicalMappingDbImportReport, entity: str, action: str) -> None:
    key = f"{entity}_{action}"
    report.counts[key] = report.counts.get(key, 0) + 1


def _parse_date(value: str | None) -> date | None:
    value = _empty_to_none(value)
    return date.fromisoformat(value) if value else None


def _parse_datetime(value: str | None) -> datetime | None:
    value = _empty_to_none(value)
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_decimal(value: str | None) -> Decimal | None:
    value = _empty_to_none(value)
    return Decimal(value) if value else None


def _parse_json_object(value: str | None) -> dict[str, Any] | None:
    value = _empty_to_none(value)
    if not value:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("canonical mapping JSON metadata fields must be objects")
    return parsed


def _merge_package_provenance(
    provenance: dict[str, Any] | None,
    *,
    package_manifest_hash: str | None,
    source_file: str,
) -> dict[str, Any]:
    merged = dict(provenance or {})
    merged["package_manifest_hash"] = package_manifest_hash
    merged["source_file"] = source_file
    return merged


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _label_from_canonical_uri(canonical_uri: str) -> str:
    token = canonical_uri.rstrip("/#").split("#")[-1].split("/")[-1].split(":")[-1]
    return _CAMEL_BOUNDARY_RE.sub(" ", token.replace("_", " ")).strip() or canonical_uri


def _acquire_import_lock(db: Session) -> None:
    """Serialize canonical mapping package imports on PostgreSQL callers."""

    bind = db.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect_name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": CANONICAL_MAPPING_IMPORT_LOCK_KEY},
    )
