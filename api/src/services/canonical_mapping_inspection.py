"""Report-only inspection for canonical mapping packages."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.services.canonical_mapping_import import (
    CanonicalMappingImportReport,
    load_canonical_mapping_package_rows,
    validate_canonical_mapping_package,
)
from src.services.canonical_mapping_relationship_policy import (
    is_operational_relationship_type,
    normalize_relationship_type,
)


@dataclass(frozen=True)
class CanonicalMappingInspectionComponent:
    """One Sygris component attached to an inspected assertion group."""

    component_order: int
    sygris_canonical_uri: str
    sygris_revision: int
    component_role: str
    coverage_fraction: str | None
    match_scope: str | None
    mismatch_scope: str | None
    transformation_rule: str | None
    rationale: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_order": self.component_order,
            "sygris_canonical_uri": self.sygris_canonical_uri,
            "sygris_revision": self.sygris_revision,
            "component_role": self.component_role,
            "coverage_fraction": self.coverage_fraction,
            "match_scope": self.match_scope,
            "mismatch_scope": self.mismatch_scope,
            "transformation_rule": self.transformation_rule,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class CanonicalMappingInspectionCandidate:
    """Report-only assertion candidate from a canonical mapping package."""

    source_standard_id: str
    source_standard_version: str
    source_code: str
    mapping_profile: str
    target_identity_status: str
    target_profile: str | None
    target_standard_id: str | None
    target_standard_version: str | None
    target_code: str | None
    target_datapoint_id: str | None
    mapping_direction: str | None
    relationship_type: str
    operational_eligible: bool
    coverage_status: str
    confidence: str
    rationale: str | None
    coverage_summary: str | None
    difference_summary: str | None
    valid_from: str
    valid_to: str | None
    approval_status: str
    publication_status: str
    assertion_hash: str | None
    provenance: dict[str, Any] | None
    components: tuple[CanonicalMappingInspectionComponent, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_standard_id": self.source_standard_id,
            "source_standard_version": self.source_standard_version,
            "source_code": self.source_code,
            "mapping_profile": self.mapping_profile,
            "target_identity_status": self.target_identity_status,
            "target_profile": self.target_profile,
            "target_standard_id": self.target_standard_id,
            "target_standard_version": self.target_standard_version,
            "target_code": self.target_code,
            "target_datapoint_id": self.target_datapoint_id,
            "mapping_direction": self.mapping_direction,
            "relationship_type": self.relationship_type,
            "operational_eligible": self.operational_eligible,
            "coverage_status": self.coverage_status,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "coverage_summary": self.coverage_summary,
            "difference_summary": self.difference_summary,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "approval_status": self.approval_status,
            "publication_status": self.publication_status,
            "assertion_hash": self.assertion_hash,
            "provenance": self.provenance,
            "components": [component.as_dict() for component in self.components],
        }


@dataclass(frozen=True)
class CanonicalMappingInspectionReport:
    """Report-only inspection output for canonical mapping packages."""

    package_dir: str
    valid: bool
    operational_eligible: bool
    validation: CanonicalMappingImportReport
    candidate_count: int
    non_operational_candidate_count: int
    relationship_type_counts: dict[str, int]
    target_identity_status_counts: dict[str, int]
    candidates: tuple[CanonicalMappingInspectionCandidate, ...] = field(
        default_factory=tuple
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_dir": self.package_dir,
            "mode": "shadow_mapping_assertion_inspection",
            "valid": self.valid,
            "operational_eligible": self.operational_eligible,
            "validation": self.validation.as_dict(),
            "candidate_count": self.candidate_count,
            "non_operational_candidate_count": self.non_operational_candidate_count,
            "relationship_type_counts": self.relationship_type_counts,
            "target_identity_status_counts": self.target_identity_status_counts,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }


def inspect_canonical_mapping_package(
    package_dir: Path,
    *,
    include_operational: bool = False,
    limit: int | None = None,
) -> CanonicalMappingInspectionReport:
    """Inspect package assertion groups without performing database writes."""

    package_dir = package_dir.resolve()
    validation = validate_canonical_mapping_package(package_dir)
    if not validation.valid:
        return CanonicalMappingInspectionReport(
            package_dir=str(package_dir),
            valid=False,
            operational_eligible=False,
            validation=validation,
            candidate_count=0,
            non_operational_candidate_count=0,
            relationship_type_counts=validation.relationship_type_counts,
            target_identity_status_counts={},
        )

    rows = load_canonical_mapping_package_rows(package_dir)
    components_by_group = _components_by_group(rows.assertion_components)
    declared_datapoints = _declared_datapoint_keys(rows.datapoints)
    candidates: list[CanonicalMappingInspectionCandidate] = []
    target_status_counts: dict[str, int] = {}
    non_operational_count = 0
    for group in rows.assertion_groups:
        relationship_type = normalize_relationship_type(group.get("relationship_type"))
        group_operational = is_operational_relationship_type(relationship_type)
        if not group_operational:
            non_operational_count += 1
        if group_operational and not include_operational:
            continue

        candidate = _candidate_from_group(
            group,
            relationship_type=relationship_type,
            operational_eligible=group_operational,
            components=components_by_group.get(_group_key(group), ()),
            declared_datapoints=declared_datapoints,
        )
        candidates.append(candidate)
        target_status_counts[candidate.target_identity_status] = (
            target_status_counts.get(candidate.target_identity_status, 0) + 1
        )
        if limit is not None and len(candidates) >= limit:
            break

    return CanonicalMappingInspectionReport(
        package_dir=str(package_dir),
        valid=True,
        operational_eligible=validation.operational_eligible,
        validation=validation,
        candidate_count=len(candidates),
        non_operational_candidate_count=non_operational_count,
        relationship_type_counts=validation.relationship_type_counts,
        target_identity_status_counts=dict(sorted(target_status_counts.items())),
        candidates=tuple(candidates),
    )


def _candidate_from_group(
    group: dict[str, str],
    *,
    relationship_type: str,
    operational_eligible: bool,
    components: tuple[CanonicalMappingInspectionComponent, ...],
    declared_datapoints: set[tuple[str, str, str]],
) -> CanonicalMappingInspectionCandidate:
    target_identity_status, target_profile = _target_identity(
        group, declared_datapoints=declared_datapoints
    )
    return CanonicalMappingInspectionCandidate(
        source_standard_id=group["source_standard_id"],
        source_standard_version=group["source_standard_version"],
        source_code=group["source_code"],
        mapping_profile=group["mapping_profile"],
        target_identity_status=target_identity_status,
        target_profile=target_profile,
        target_standard_id=_empty_to_none(group.get("target_standard_id")),
        target_standard_version=_empty_to_none(group.get("target_standard_version")),
        target_code=_empty_to_none(group.get("target_code")),
        target_datapoint_id=_empty_to_none(group.get("target_datapoint_id")),
        mapping_direction=_empty_to_none(group.get("mapping_direction")),
        relationship_type=relationship_type,
        operational_eligible=operational_eligible,
        coverage_status=group["coverage_status"],
        confidence=group["confidence"],
        rationale=_empty_to_none(group.get("rationale")),
        coverage_summary=_empty_to_none(group.get("coverage_summary")),
        difference_summary=_empty_to_none(group.get("difference_summary")),
        valid_from=group["valid_from"],
        valid_to=_empty_to_none(group.get("valid_to")),
        approval_status=group["approval_status"],
        publication_status=group["publication_status"],
        assertion_hash=_empty_to_none(group.get("assertion_hash")),
        provenance=_parse_json_object(group.get("provenance")),
        components=components,
    )


def _components_by_group(
    rows: list[dict[str, str]],
) -> dict[tuple[str, str, str, str], tuple[CanonicalMappingInspectionComponent, ...]]:
    grouped: dict[
        tuple[str, str, str, str], list[CanonicalMappingInspectionComponent]
    ] = {}
    for row in rows:
        grouped.setdefault(_group_key(row), []).append(_component_from_row(row))
    return {
        key: tuple(sorted(items, key=lambda item: item.component_order))
        for key, items in grouped.items()
    }


def _component_from_row(row: dict[str, str]) -> CanonicalMappingInspectionComponent:
    return CanonicalMappingInspectionComponent(
        component_order=int(row["component_order"]),
        sygris_canonical_uri=row["sygris_canonical_uri"],
        sygris_revision=int(row["sygris_revision"]),
        component_role=row.get("component_role") or "primary",
        coverage_fraction=_empty_to_none(row.get("coverage_fraction")),
        match_scope=_empty_to_none(row.get("match_scope")),
        mismatch_scope=_empty_to_none(row.get("mismatch_scope")),
        transformation_rule=_empty_to_none(row.get("transformation_rule")),
        rationale=_empty_to_none(row.get("rationale")),
    )


def _group_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row["source_standard_id"],
        row["source_standard_version"],
        row["source_code"],
        row["mapping_profile"],
    )


def _declared_datapoint_keys(
    rows: list[dict[str, str]],
) -> set[tuple[str, str, str]]:
    return {
        (
            row.get("standard_id", "").strip(),
            row.get("standard_version", "").strip(),
            row.get("code", "").strip(),
        )
        for row in rows
        if row.get("standard_id", "").strip()
        and row.get("standard_version", "").strip()
        and row.get("code", "").strip()
    }


def _target_identity(
    group: dict[str, str],
    *,
    declared_datapoints: set[tuple[str, str, str]],
) -> tuple[str, str | None]:
    mapping_profile = group.get("mapping_profile", "")
    target_profile = (
        mapping_profile if mapping_profile and mapping_profile != "default" else None
    )
    target_standard_id = _empty_to_none(group.get("target_standard_id"))
    target_standard_version = _empty_to_none(group.get("target_standard_version"))
    target_code = _empty_to_none(group.get("target_code"))
    target_datapoint_id = _empty_to_none(group.get("target_datapoint_id"))
    if target_standard_id or target_code or target_datapoint_id:
        target_key = (
            target_standard_id or "",
            target_standard_version or "",
            target_code or "",
        )
        if target_standard_id and target_standard_version and target_code:
            if target_key in declared_datapoints:
                return "declared_in_package", target_profile
            return "declared_missing_target", target_profile
        return "declared_missing_target", target_profile
    if not mapping_profile or mapping_profile == "default":
        return "not_declared_in_package", None
    return "profile_only", target_profile


def _parse_json_object(value: str | None) -> dict[str, Any] | None:
    value = _empty_to_none(value)
    if not value:
        return None
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else None


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None
