"""Materialize pairwise mappings from canonical Sygris assertion footprints."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from itertools import permutations
from typing import Any

from sqlalchemy import text, tuple_
from sqlalchemy.orm import Session, aliased, joinedload

from src.database.models import (
    MappingAssertionGroup,
    MaterializedPairwiseMapping,
    StandardDatapoint,
    StandardRelease,
)
from src.services.canonical_mapping_installed_standards import StandardReleaseKey
from src.services.canonical_mapping_relationship_policy import (
    is_operational_relationship_type,
    non_operational_relationship_blockers,
    normalize_relationship_type,
)

DEFAULT_MAPPING_PROFILE = "default"
# Public materialization defaults to APPROVED-only (codex F08 M1): the
# materialized_pairwise_mappings rows are exposed via the public /api/v1/mappings
# surface, so draft assertions must not materialize by default. Inspection/preview
# callers that need draft review pass approval_statuses=("draft", ...) explicitly.
DEFAULT_APPROVAL_STATUSES = ("approved",)
DERIVATION_METHOD = "sygris_footprint_overlap"
_ONE = Decimal("1.0000")
_ZERO = Decimal("0.0000")


@dataclass(frozen=True)
class PairwiseCandidate:
    """Derived pairwise row before DB persistence."""

    source_group: MappingAssertionGroup
    target_group: MappingAssertionGroup
    source_datapoint: StandardDatapoint
    target_datapoint: StandardDatapoint
    source_standard: str
    target_standard: str
    source_footprint: dict[int, Decimal]
    target_footprint: dict[int, Decimal]
    shared_concept_ids: tuple[int, ...]
    match_strength: Decimal
    relationship_type: str
    coverage_summary: str
    difference_summary: str
    metadata_json: dict[str, Any]
    generated_from_hash: str


@dataclass
class PairwiseMaterializationReport:
    """Outcome of a shadow pairwise materialization run."""

    mode: str
    dry_run: bool
    committed: bool
    mapping_profile: str
    approval_statuses: tuple[str, ...]
    candidate_count: int
    materialization_hash: str
    blocked: bool = False
    blockers: list[str] = field(default_factory=list)
    relationship_type_counts: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    installed_standard_releases: tuple[StandardReleaseKey, ...] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "dry_run": self.dry_run,
            "committed": self.committed,
            "mapping_profile": self.mapping_profile,
            "approval_statuses": list(self.approval_statuses),
            "candidate_count": self.candidate_count,
            "materialization_hash": self.materialization_hash,
            "blocked": self.blocked,
            "blockers": self.blockers,
            "relationship_type_counts": self.relationship_type_counts,
            "counts": self.counts,
            "installed_standard_releases": (
                [
                    {"standard_id": standard_id, "version": version}
                    for standard_id, version in self.installed_standard_releases
                ]
                if self.installed_standard_releases is not None
                else None
            ),
        }


def _acquire_materialization_lock(db: Session) -> None:
    """Serialize pairwise materialization via a deterministic advisory lock.

    Uses PostgreSQL pg_advisory_xact_lock when available; otherwise no-op.
    The lock is transaction-scoped and released on commit/rollback.
    """
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    lock_key = (
        int.from_bytes(
            hashlib.sha256(b"sds:pairwise_materialization").digest()[:8],
            "big",
        )
        & 0x7FFF_FFFF_FFFF_FFFF
    )
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})


def materialize_pairwise_mappings(
    *,
    db: Session,
    dry_run: bool = False,
    mapping_profile: str = DEFAULT_MAPPING_PROFILE,
    approval_statuses: tuple[str, ...] = DEFAULT_APPROVAL_STATUSES,
    allow_operational_subset: bool = False,
    installed_standard_releases: set[StandardReleaseKey] | None = None,
) -> PairwiseMaterializationReport:
    """Generate current pairwise mappings from Sygris canonical footprints.

    This writes only to ``materialized_pairwise_mappings``. The legacy
    ``standard_mappings`` table is not touched, but the public mapping store is
    now canonical-backed, so committed materialization changes the rows visible
    through ``/api/v1/mappings``.
    """
    _acquire_materialization_lock(db)

    (
        groups,
        skipped_duplicates,
        skipped_missing_installed_standard,
        relationship_counts,
        non_operational_counts,
    ) = _load_current_groups(
        db,
        mapping_profile=mapping_profile,
        approval_statuses=approval_statuses,
        installed_standard_releases=installed_standard_releases,
    )
    skipped_non_operational = sum(non_operational_counts.values())
    if skipped_non_operational and not dry_run and not allow_operational_subset:
        report = PairwiseMaterializationReport(
            mode="shadow_pairwise_materialization_blocked",
            dry_run=dry_run,
            committed=False,
            mapping_profile=mapping_profile,
            approval_statuses=approval_statuses,
            candidate_count=0,
            materialization_hash=_materialization_hash([]),
            blocked=True,
            blockers=non_operational_relationship_blockers(non_operational_counts),
            relationship_type_counts=relationship_counts,
            installed_standard_releases=(
                tuple(sorted(installed_standard_releases))
                if installed_standard_releases is not None
                else None
            ),
        )
        if skipped_duplicates:
            report.counts["older_assertion_groups_skipped"] = skipped_duplicates
        if skipped_missing_installed_standard:
            report.counts["assertion_groups_skipped_missing_installed_standard"] = (
                skipped_missing_installed_standard
            )
        report.counts["non_operational_assertion_groups_blocked"] = (
            skipped_non_operational
        )
        for relationship_type, count in non_operational_counts.items():
            report.counts[f"non_operational_relationship_{relationship_type}"] = count
        db.rollback()
        return report

    candidates = _derive_pairwise_candidates(groups, mapping_profile=mapping_profile)
    materialization_hash = _materialization_hash(candidates)
    report = PairwiseMaterializationReport(
        mode="shadow_pairwise_materialization",
        dry_run=dry_run,
        committed=False,
        mapping_profile=mapping_profile,
        approval_statuses=approval_statuses,
        candidate_count=len(candidates),
        materialization_hash=materialization_hash,
        relationship_type_counts=relationship_counts,
        installed_standard_releases=(
            tuple(sorted(installed_standard_releases))
            if installed_standard_releases is not None
            else None
        ),
    )
    if skipped_duplicates:
        report.counts["older_assertion_groups_skipped"] = skipped_duplicates
    if skipped_missing_installed_standard:
        report.counts["assertion_groups_skipped_missing_installed_standard"] = (
            skipped_missing_installed_standard
        )
    if skipped_non_operational:
        report.counts[
            (
                "non_operational_assertion_groups_dry_run_only"
                if dry_run
                else "non_operational_assertion_groups_skipped"
            )
        ] = skipped_non_operational
        for relationship_type, count in non_operational_counts.items():
            report.counts[f"non_operational_relationship_{relationship_type}"] = count

    candidate_keys = {
        (candidate.source_datapoint.id, candidate.target_datapoint.id)
        for candidate in candidates
    }

    try:
        for candidate in candidates:
            _upsert_pairwise_candidate(db, candidate, report)
        db.flush()
        _mark_missing_current_rows_stale(
            db,
            candidate_keys,
            report,
            installed_standard_releases=installed_standard_releases,
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


def _load_current_groups(
    db: Session,
    *,
    mapping_profile: str,
    approval_statuses: tuple[str, ...],
    installed_standard_releases: set[StandardReleaseKey] | None,
) -> tuple[list[MappingAssertionGroup], int, int, dict[str, int], dict[str, int]]:
    query = (
        db.query(MappingAssertionGroup)
        .options(
            joinedload(MappingAssertionGroup.source_datapoint).joinedload(
                StandardDatapoint.standard_release
            ),
            joinedload(MappingAssertionGroup.components),
        )
        .filter(
            MappingAssertionGroup.mapping_profile == mapping_profile,
            MappingAssertionGroup.valid_to.is_(None),
            # exclude superseded groups so a replaced assertion can't materialize
            # into the public surface (codex F08 M1)
            MappingAssertionGroup.superseded_by.is_(None),
        )
        .order_by(
            MappingAssertionGroup.source_datapoint_id,
            MappingAssertionGroup.valid_from.desc(),
            MappingAssertionGroup.id.desc(),
        )
    )
    if approval_statuses:
        query = query.filter(
            MappingAssertionGroup.approval_status.in_(approval_statuses)
        )

    selected: dict[int, MappingAssertionGroup] = {}
    seen_datapoint_ids: set[int] = set()
    relationship_counts: dict[str, int] = {}
    non_operational_counts: dict[str, int] = {}
    skipped = 0
    skipped_missing_installed_standard = 0
    for group in query.all():
        if not group.components or group.source_datapoint is None:
            continue
        datapoint = group.source_datapoint
        if datapoint.standard_release is None:
            continue
        release_key = (
            datapoint.standard_release.standard_id,
            datapoint.standard_release.version,
        )
        if (
            installed_standard_releases is not None
            and release_key not in installed_standard_releases
        ):
            skipped_missing_installed_standard += 1
            continue
        if (
            datapoint.lifecycle_status != "active"
            or datapoint.standard_release.lifecycle_status != "active"
        ):
            skipped_missing_installed_standard += 1
            continue
        if group.source_datapoint_id in seen_datapoint_ids:
            skipped += 1
            continue
        seen_datapoint_ids.add(group.source_datapoint_id)
        relationship_type = normalize_relationship_type(group.relationship_type)
        relationship_counts[relationship_type] = (
            relationship_counts.get(relationship_type, 0) + 1
        )
        if not is_operational_relationship_type(relationship_type):
            non_operational_counts[relationship_type] = (
                non_operational_counts.get(relationship_type, 0) + 1
            )
            continue
        selected[group.source_datapoint_id] = group
    return (
        list(selected.values()),
        skipped,
        skipped_missing_installed_standard,
        dict(sorted(relationship_counts.items())),
        dict(sorted(non_operational_counts.items())),
    )


def _derive_pairwise_candidates(
    groups: list[MappingAssertionGroup], *, mapping_profile: str
) -> list[PairwiseCandidate]:
    candidates: list[PairwiseCandidate] = []
    footprints = {group.id: _footprint_for(group) for group in groups}
    for source_group, target_group in permutations(groups, 2):
        source_datapoint = source_group.source_datapoint
        target_datapoint = target_group.source_datapoint
        source_release = source_datapoint.standard_release
        target_release = target_datapoint.standard_release
        if source_release.standard_id == target_release.standard_id:
            continue
        source_footprint = footprints[source_group.id]
        target_footprint = footprints[target_group.id]
        shared = tuple(sorted(set(source_footprint) & set(target_footprint)))
        if not shared:
            continue

        match_strength = _match_strength(source_footprint, target_footprint, shared)
        relationship_type = _relationship_type(
            source_group=source_group,
            target_group=target_group,
            source_footprint=source_footprint,
            target_footprint=target_footprint,
            match_strength=match_strength,
        )
        coverage_summary, difference_summary, metadata = _summaries_and_metadata(
            source_group=source_group,
            target_group=target_group,
            source_footprint=source_footprint,
            target_footprint=target_footprint,
            shared=shared,
            mapping_profile=mapping_profile,
            match_strength=match_strength,
        )
        candidate = PairwiseCandidate(
            source_group=source_group,
            target_group=target_group,
            source_datapoint=source_datapoint,
            target_datapoint=target_datapoint,
            source_standard=source_release.standard_id,
            target_standard=target_release.standard_id,
            source_footprint=source_footprint,
            target_footprint=target_footprint,
            shared_concept_ids=shared,
            match_strength=match_strength,
            relationship_type=relationship_type,
            coverage_summary=coverage_summary,
            difference_summary=difference_summary,
            metadata_json=metadata,
            generated_from_hash="",
        )
        candidates.append(
            PairwiseCandidate(
                **{
                    **candidate.__dict__,
                    "generated_from_hash": _candidate_hash(candidate),
                }
            )
        )
    return sorted(
        candidates,
        key=lambda item: (
            item.source_standard,
            item.source_datapoint.code,
            item.target_standard,
            item.target_datapoint.code,
        ),
    )


def _footprint_for(group: MappingAssertionGroup) -> dict[int, Decimal]:
    footprint: dict[int, Decimal] = {}
    for component in group.components:
        value = component.coverage_fraction or _ONE
        concept_id = component.canonical_concept_id
        footprint[concept_id] = min(_ONE, footprint.get(concept_id, _ZERO) + value)
    return footprint


def _match_strength(
    source_footprint: dict[int, Decimal],
    target_footprint: dict[int, Decimal],
    shared: tuple[int, ...],
) -> Decimal:
    overlap = sum(
        min(source_footprint[concept_id], target_footprint[concept_id])
        for concept_id in shared
    )
    denominator = max(sum(source_footprint.values()), sum(target_footprint.values()))
    if not denominator:
        return _ZERO
    return (overlap / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _relationship_type(
    *,
    source_group: MappingAssertionGroup,
    target_group: MappingAssertionGroup,
    source_footprint: dict[int, Decimal],
    target_footprint: dict[int, Decimal],
    match_strength: Decimal,
) -> str:
    if source_footprint == target_footprint and match_strength == Decimal("1.00"):
        return _directional_group_relationship(source_group, target_group)
    return "partial"


def _directional_group_relationship(
    source_group: MappingAssertionGroup, target_group: MappingAssertionGroup
) -> str:
    source_relationship = _clean_group_relationship(source_group.relationship_type)
    target_relationship = _clean_group_relationship(target_group.relationship_type)
    if source_relationship == "equivalent" and target_relationship == "equivalent":
        if _is_complete_coverage(source_group) and _is_complete_coverage(target_group):
            return "equivalent"
        return "partial"
    if (
        source_relationship in {"broader", "narrower"}
        and target_relationship == "equivalent"
    ):
        return source_relationship
    if source_relationship == "equivalent" and target_relationship in {
        "broader",
        "narrower",
    }:
        return _invert_directional_relationship(target_relationship)
    return "partial"


def _clean_group_relationship(value: str | None) -> str:
    return normalize_relationship_type(value)


def _is_complete_coverage(group: MappingAssertionGroup) -> bool:
    return (group.coverage_status or "").strip().lower() in {"complete", "full"}


def _invert_directional_relationship(relationship: str) -> str:
    if relationship == "broader":
        return "narrower"
    if relationship == "narrower":
        return "broader"
    return relationship


def _summaries_and_metadata(
    *,
    source_group: MappingAssertionGroup,
    target_group: MappingAssertionGroup,
    source_footprint: dict[int, Decimal],
    target_footprint: dict[int, Decimal],
    shared: tuple[int, ...],
    mapping_profile: str,
    match_strength: Decimal,
) -> tuple[str, str, dict[str, Any]]:
    source_only = tuple(sorted(set(source_footprint) - set(shared)))
    target_only = tuple(sorted(set(target_footprint) - set(shared)))
    coverage_summary = (
        f"Derived through {len(shared)} shared Sygris canonical concept(s)."
    )
    difference_summary = (
        f"Source-only Sygris concepts: {len(source_only)}; "
        f"target-only Sygris concepts: {len(target_only)}."
    )
    metadata = {
        "mapping_profile": mapping_profile,
        "source_assertion_group_id": source_group.id,
        "target_assertion_group_id": target_group.id,
        "source_assertion_hash": source_group.assertion_hash,
        "target_assertion_hash": target_group.assertion_hash,
        "source_assertion_relationship_type": source_group.relationship_type,
        "target_assertion_relationship_type": target_group.relationship_type,
        "source_assertion_coverage_status": source_group.coverage_status,
        "target_assertion_coverage_status": target_group.coverage_status,
        "shared_sygris_concept_ids": list(shared),
        "source_only_sygris_concept_ids": list(source_only),
        "target_only_sygris_concept_ids": list(target_only),
        "match_strength": str(match_strength),
    }
    return coverage_summary, difference_summary, metadata


def _candidate_hash(candidate: PairwiseCandidate) -> str:
    payload = {
        "source_datapoint_id": candidate.source_datapoint.id,
        "target_datapoint_id": candidate.target_datapoint.id,
        "source_assertion_group_id": candidate.source_group.id,
        "target_assertion_group_id": candidate.target_group.id,
        "relationship_type": candidate.relationship_type,
        "match_strength": str(candidate.match_strength),
        "source_assertion_relationship_type": candidate.source_group.relationship_type,
        "target_assertion_relationship_type": candidate.target_group.relationship_type,
        "source_assertion_coverage_status": candidate.source_group.coverage_status,
        "target_assertion_coverage_status": candidate.target_group.coverage_status,
        "source_footprint": _json_ready_footprint(candidate.source_footprint),
        "target_footprint": _json_ready_footprint(candidate.target_footprint),
        "shared_concept_ids": list(candidate.shared_concept_ids),
    }
    return _hash_payload(payload)


def _materialization_hash(candidates: list[PairwiseCandidate]) -> str:
    payload = [candidate.generated_from_hash for candidate in candidates]
    return _hash_payload(payload)


def _json_ready_footprint(footprint: dict[int, Decimal]) -> dict[str, str]:
    return {str(key): str(value) for key, value in sorted(footprint.items())}


def _hash_payload(payload: Any) -> str:
    normalized = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _upsert_pairwise_candidate(
    db: Session,
    candidate: PairwiseCandidate,
    report: PairwiseMaterializationReport,
) -> None:
    existing = (
        db.query(MaterializedPairwiseMapping)
        .filter(
            MaterializedPairwiseMapping.source_datapoint_id
            == candidate.source_datapoint.id,
            MaterializedPairwiseMapping.target_datapoint_id
            == candidate.target_datapoint.id,
            MaterializedPairwiseMapping.is_current.is_(True),
        )
        .first()
    )
    if existing and existing.generated_from_hash == candidate.generated_from_hash:
        _count(report, "pairwise_unchanged")
        return
    if existing:
        existing.is_current = False
        existing.stale_reason = "replaced_by_new_sygris_footprint_materialization"
        db.flush()
        _count(report, "pairwise_superseded")

    db.add(_mapping_from_candidate(candidate))
    _count(report, "pairwise_created")


def _mapping_from_candidate(
    candidate: PairwiseCandidate,
) -> MaterializedPairwiseMapping:
    return MaterializedPairwiseMapping(
        source_datapoint_id=candidate.source_datapoint.id,
        target_datapoint_id=candidate.target_datapoint.id,
        source_assertion_group_id=candidate.source_group.id,
        target_assertion_group_id=candidate.target_group.id,
        source_standard=candidate.source_standard,
        source_code=candidate.source_datapoint.code,
        target_standard=candidate.target_standard,
        target_code=candidate.target_datapoint.code,
        relationship_type=candidate.relationship_type,
        match_strength=candidate.match_strength,
        derivation_method=DERIVATION_METHOD,
        coverage_summary=candidate.coverage_summary,
        difference_summary=candidate.difference_summary,
        generated_from_hash=candidate.generated_from_hash,
        is_current=True,
        metadata_json=candidate.metadata_json,
    )


def _mark_missing_current_rows_stale(
    db: Session,
    candidate_keys: set[tuple[int, int]],
    report: PairwiseMaterializationReport,
    *,
    installed_standard_releases: set[StandardReleaseKey] | None = None,
) -> None:
    query = db.query(MaterializedPairwiseMapping).filter(
        MaterializedPairwiseMapping.derivation_method == DERIVATION_METHOD,
        MaterializedPairwiseMapping.is_current.is_(True),
    )

    if installed_standard_releases is not None:
        if not installed_standard_releases:
            return
        source_datapoint = aliased(StandardDatapoint)
        target_datapoint = aliased(StandardDatapoint)
        source_release = aliased(StandardRelease)
        target_release = aliased(StandardRelease)
        release_scope = tuple(sorted(installed_standard_releases))
        query = (
            query.join(
                source_datapoint,
                MaterializedPairwiseMapping.source_datapoint_id == source_datapoint.id,
            )
            .join(
                source_release,
                source_datapoint.standard_release_id == source_release.id,
            )
            .join(
                target_datapoint,
                MaterializedPairwiseMapping.target_datapoint_id == target_datapoint.id,
            )
            .join(
                target_release,
                target_datapoint.standard_release_id == target_release.id,
            )
            .filter(
                tuple_(source_release.standard_id, source_release.version).in_(
                    release_scope
                ),
                tuple_(target_release.standard_id, target_release.version).in_(
                    release_scope
                ),
            )
        )

    current_rows = query.all()
    for row in current_rows:
        key = (row.source_datapoint_id, row.target_datapoint_id)
        if key in candidate_keys:
            continue
        row.is_current = False
        row.stale_reason = "missing_from_latest_sygris_footprint_materialization"
        _count(report, "pairwise_staled")


def _count(report: PairwiseMaterializationReport, key: str) -> None:
    report.counts[key] = report.counts.get(key, 0) + 1
