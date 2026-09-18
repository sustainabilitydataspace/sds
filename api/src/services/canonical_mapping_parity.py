"""Compare legacy standard mappings with canonical materialized mappings."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from src.database.models import MaterializedPairwiseMapping, StandardMapping

DEFAULT_CONFIDENCE_TOLERANCE = Decimal("0.05")
WORKLIST_STATUS_UNCLASSIFIED = "unclassified"
ISSUE_CLASSIFICATION_HINTS = {
    "missing_in_canonical": "canonical_coverage_gap_or_materialization_bug",
    "extra_in_canonical": "legacy_gap_or_semantic_expansion",
    "code_mismatch": "legacy_code_normalization_review_required",
    "relationship_mismatch": "semantic_relationship_review_required",
    "confidence_mismatch": "confidence_policy_review_required",
    "duplicate_legacy_key": "legacy_data_quality_issue",
    "duplicate_canonical_key": "canonical_materialization_data_quality_issue",
}
MAPPING_PARITY_WORKLIST_FIELDS = [
    "issue_type",
    "status",
    "suggested_classification",
    "source_standard",
    "source_code",
    "target_standard",
    "target_code",
    "field",
    "legacy_value",
    "canonical_value",
]
ROMAN_SUFFIX_RE = re.compile(r"-(i|ii|iii|iv|v|vi|vii|viii|ix|x)$", re.IGNORECASE)
TRAILING_LETTER_HYPHEN_RE = re.compile(r"([A-Za-z])-$")
COMPOSITE_CODE_TOKENS = (";", "/", ",", " to ", "Guidance")


@dataclass(frozen=True, order=True)
class MappingParityKey:
    """Stable public mapping key used by the legacy and canonical surfaces."""

    source_standard: str
    source_code: str
    target_standard: str
    target_code: str

    def as_string(self) -> str:
        return "|".join(
            [
                self.source_standard,
                self.source_code,
                self.target_standard,
                self.target_code,
            ]
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "source_standard": self.source_standard,
            "source_code": self.source_code,
            "target_standard": self.target_standard,
            "target_code": self.target_code,
        }


@dataclass(frozen=True)
class MappingParityFieldMismatch:
    """A matched mapping key whose public behavior differs."""

    key: MappingParityKey
    field: str
    legacy_value: Any
    canonical_value: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.as_dict(),
            "field": self.field,
            "legacy_value": self.legacy_value,
            "canonical_value": self.canonical_value,
        }


@dataclass(frozen=True)
class MappingParityWorkItem:
    """Classifiable cutover work item generated from a parity difference."""

    issue_type: str
    key: MappingParityKey
    suggested_classification: str
    status: str = WORKLIST_STATUS_UNCLASSIFIED
    field: str | None = None
    legacy_value: Any = None
    canonical_value: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "issue_type": self.issue_type,
            "status": self.status,
            "suggested_classification": self.suggested_classification,
            "field": self.field,
            "legacy_value": self.legacy_value,
            "canonical_value": self.canonical_value,
            **self.key.as_dict(),
        }


@dataclass
class MappingParityReport:
    """Parity report for one legacy/canonical mapping comparison."""

    source_standard: str | None
    target_standard: str | None
    confidence_tolerance: Decimal
    legacy_count: int
    canonical_count: int
    matched_count: int
    normalized_code_match_count: int = 0
    missing_in_canonical: list[MappingParityKey] = field(default_factory=list)
    extra_in_canonical: list[MappingParityKey] = field(default_factory=list)
    code_mismatches: list[MappingParityFieldMismatch] = field(default_factory=list)
    relationship_mismatches: list[MappingParityFieldMismatch] = field(
        default_factory=list
    )
    confidence_mismatches: list[MappingParityFieldMismatch] = field(
        default_factory=list
    )
    duplicate_legacy_keys: list[MappingParityKey] = field(default_factory=list)
    duplicate_canonical_keys: list[MappingParityKey] = field(default_factory=list)
    sample_limit: int = 100

    @property
    def passed(self) -> bool:
        return not (
            self.missing_in_canonical
            or self.extra_in_canonical
            or self.code_mismatches
            or self.relationship_mismatches
            or self.confidence_mismatches
            or self.duplicate_legacy_keys
            or self.duplicate_canonical_keys
        )

    def as_dict(self) -> dict[str, Any]:
        worklist = self.worklist()
        return {
            "passed": self.passed,
            "source_standard": self.source_standard,
            "target_standard": self.target_standard,
            "confidence_tolerance": str(self.confidence_tolerance),
            "legacy_count": self.legacy_count,
            "canonical_count": self.canonical_count,
            "matched_count": self.matched_count,
            "normalized_code_match_count": self.normalized_code_match_count,
            "missing_in_canonical_count": len(self.missing_in_canonical),
            "extra_in_canonical_count": len(self.extra_in_canonical),
            "code_mismatch_count": len(self.code_mismatches),
            "relationship_mismatch_count": len(self.relationship_mismatches),
            "confidence_mismatch_count": len(self.confidence_mismatches),
            "duplicate_legacy_key_count": len(self.duplicate_legacy_keys),
            "duplicate_canonical_key_count": len(self.duplicate_canonical_keys),
            "worklist_count": len(worklist),
            "samples": {
                "missing_in_canonical": _sample_keys(
                    self.missing_in_canonical, self.sample_limit
                ),
                "extra_in_canonical": _sample_keys(
                    self.extra_in_canonical, self.sample_limit
                ),
                "code_mismatches": [
                    item.as_dict() for item in self.code_mismatches[: self.sample_limit]
                ],
                "relationship_mismatches": [
                    item.as_dict()
                    for item in self.relationship_mismatches[: self.sample_limit]
                ],
                "confidence_mismatches": [
                    item.as_dict()
                    for item in self.confidence_mismatches[: self.sample_limit]
                ],
                "duplicate_legacy_keys": _sample_keys(
                    self.duplicate_legacy_keys, self.sample_limit
                ),
                "duplicate_canonical_keys": _sample_keys(
                    self.duplicate_canonical_keys, self.sample_limit
                ),
                "worklist": [item.as_dict() for item in worklist[: self.sample_limit]],
            },
        }

    def worklist(self) -> list[MappingParityWorkItem]:
        """Return every parity difference as a deterministic classification queue."""

        items: list[MappingParityWorkItem] = []
        items.extend(
            _work_item("missing_in_canonical", key) for key in self.missing_in_canonical
        )
        items.extend(
            _work_item("extra_in_canonical", key) for key in self.extra_in_canonical
        )
        items.extend(
            _work_item(
                "code_mismatch",
                mismatch.key,
                field=mismatch.field,
                legacy_value=mismatch.legacy_value,
                canonical_value=mismatch.canonical_value,
            )
            for mismatch in self.code_mismatches
        )
        items.extend(
            _work_item(
                "relationship_mismatch",
                mismatch.key,
                field=mismatch.field,
                legacy_value=mismatch.legacy_value,
                canonical_value=mismatch.canonical_value,
            )
            for mismatch in self.relationship_mismatches
        )
        items.extend(
            _work_item(
                "confidence_mismatch",
                mismatch.key,
                field=mismatch.field,
                legacy_value=mismatch.legacy_value,
                canonical_value=mismatch.canonical_value,
            )
            for mismatch in self.confidence_mismatches
        )
        items.extend(
            _work_item("duplicate_legacy_key", key)
            for key in self.duplicate_legacy_keys
        )
        items.extend(
            _work_item("duplicate_canonical_key", key)
            for key in self.duplicate_canonical_keys
        )
        return sorted(
            items,
            key=lambda item: (
                item.issue_type,
                item.key.source_standard,
                item.key.source_code,
                item.key.target_standard,
                item.key.target_code,
                item.field or "",
            ),
        )


def write_mapping_parity_worklist_csv(
    path: Path,
    worklist: Iterable[MappingParityWorkItem],
) -> None:
    """Write a deterministic cutover-review worklist CSV."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MAPPING_PARITY_WORKLIST_FIELDS)
        writer.writeheader()
        for item in worklist:
            row = item.as_dict()
            writer.writerow(
                {key: row.get(key) for key in MAPPING_PARITY_WORKLIST_FIELDS}
            )


def compare_legacy_to_canonical_mapping_parity(
    *,
    db: Session,
    source_standard: str | None = None,
    target_standard: str | None = None,
    confidence_tolerance: Decimal = DEFAULT_CONFIDENCE_TOLERANCE,
    sample_limit: int = 100,
) -> MappingParityReport:
    """Compare current legacy mappings against canonical materialized mappings."""

    legacy_rows = _load_legacy_rows(
        db,
        source_standard=source_standard,
        target_standard=target_standard,
    )
    canonical_rows = _load_canonical_rows(
        db,
        source_standard=source_standard,
        target_standard=target_standard,
    )
    legacy_by_key, duplicate_legacy = _index_rows(legacy_rows, _legacy_key)
    canonical_by_key, duplicate_canonical = _index_rows(canonical_rows, _canonical_key)

    legacy_keys = set(legacy_by_key)
    canonical_keys = set(canonical_by_key)
    exact_matched = sorted(legacy_keys & canonical_keys)
    missing = sorted(legacy_keys - canonical_keys)
    extra = sorted(canonical_keys - legacy_keys)
    normalized_pairs = _match_normalized_code_pairs(missing, extra)
    normalized_legacy_keys = {legacy_key for legacy_key, _ in normalized_pairs}
    normalized_canonical_keys = {canonical_key for _, canonical_key in normalized_pairs}
    missing = [key for key in missing if key not in normalized_legacy_keys]
    extra = [key for key in extra if key not in normalized_canonical_keys]

    code_mismatches: list[MappingParityFieldMismatch] = []
    relationship_mismatches: list[MappingParityFieldMismatch] = []
    confidence_mismatches: list[MappingParityFieldMismatch] = []
    for legacy_key, canonical_key in normalized_pairs:
        code_mismatches.append(
            MappingParityFieldMismatch(
                key=canonical_key,
                field="target_code",
                legacy_value=legacy_key.target_code,
                canonical_value=canonical_key.target_code,
            )
        )
    matched_pairs = [(key, key) for key in exact_matched] + normalized_pairs
    for legacy_key, canonical_key in matched_pairs:
        comparison_key = canonical_key
        legacy = legacy_by_key[legacy_key]
        canonical = canonical_by_key[canonical_key]
        legacy_relationship = _clean_relationship(legacy.relationship_type)
        canonical_relationship = _clean_relationship(canonical.relationship_type)
        if legacy_relationship != canonical_relationship:
            relationship_mismatches.append(
                MappingParityFieldMismatch(
                    key=comparison_key,
                    field="relationship_type",
                    legacy_value=legacy_relationship,
                    canonical_value=canonical_relationship,
                )
            )

        legacy_confidence = _decimal_or_none(legacy.confidence)
        canonical_confidence = _decimal_or_none(canonical.match_strength)
        if legacy_confidence is not None and canonical_confidence is not None:
            delta = abs(legacy_confidence - canonical_confidence)
            if delta > confidence_tolerance:
                confidence_mismatches.append(
                    MappingParityFieldMismatch(
                        key=comparison_key,
                        field="confidence",
                        legacy_value=str(legacy_confidence),
                        canonical_value=str(canonical_confidence),
                    )
                )

    return MappingParityReport(
        source_standard=source_standard,
        target_standard=target_standard,
        confidence_tolerance=confidence_tolerance,
        legacy_count=len(legacy_rows),
        canonical_count=len(canonical_rows),
        matched_count=len(matched_pairs),
        normalized_code_match_count=len(normalized_pairs),
        missing_in_canonical=missing,
        extra_in_canonical=extra,
        code_mismatches=code_mismatches,
        relationship_mismatches=relationship_mismatches,
        confidence_mismatches=confidence_mismatches,
        duplicate_legacy_keys=sorted(duplicate_legacy),
        duplicate_canonical_keys=sorted(duplicate_canonical),
        sample_limit=sample_limit,
    )


def _load_legacy_rows(
    db: Session,
    *,
    source_standard: str | None,
    target_standard: str | None,
) -> list[StandardMapping]:
    query = db.query(StandardMapping).filter(StandardMapping.is_active.is_(True))
    if source_standard:
        query = query.filter(StandardMapping.source_standard == source_standard)
    if target_standard:
        query = query.filter(StandardMapping.target_standard == target_standard)
    return query.order_by(
        StandardMapping.source_standard,
        StandardMapping.source_code,
        StandardMapping.target_standard,
        StandardMapping.target_code,
        StandardMapping.id,
    ).all()


def _load_canonical_rows(
    db: Session,
    *,
    source_standard: str | None,
    target_standard: str | None,
) -> list[MaterializedPairwiseMapping]:
    query = db.query(MaterializedPairwiseMapping).filter(
        MaterializedPairwiseMapping.is_current.is_(True)
    )
    if source_standard:
        query = query.filter(
            MaterializedPairwiseMapping.source_standard == source_standard
        )
    if target_standard:
        query = query.filter(
            MaterializedPairwiseMapping.target_standard == target_standard
        )
    return query.order_by(
        MaterializedPairwiseMapping.source_standard,
        MaterializedPairwiseMapping.source_code,
        MaterializedPairwiseMapping.target_standard,
        MaterializedPairwiseMapping.target_code,
        MaterializedPairwiseMapping.id,
    ).all()


def _index_rows(
    rows: list[Any], key_builder
) -> tuple[dict[MappingParityKey, Any], set[MappingParityKey]]:
    by_key: dict[MappingParityKey, Any] = {}
    duplicates: set[MappingParityKey] = set()
    for row in rows:
        key = key_builder(row)
        if key in by_key:
            duplicates.add(key)
            continue
        by_key[key] = row
    return by_key, duplicates


def _legacy_key(row: StandardMapping) -> MappingParityKey:
    return MappingParityKey(
        source_standard=row.source_standard,
        source_code=row.source_code,
        target_standard=row.target_standard,
        target_code=row.target_code or "",
    )


def _canonical_key(row: MaterializedPairwiseMapping) -> MappingParityKey:
    return MappingParityKey(
        source_standard=row.source_standard,
        source_code=row.source_code,
        target_standard=row.target_standard,
        target_code=row.target_code,
    )


def _match_normalized_code_pairs(
    missing: list[MappingParityKey],
    extra: list[MappingParityKey],
) -> list[tuple[MappingParityKey, MappingParityKey]]:
    legacy_by_normalized = _unique_normalized_keys(missing)
    canonical_by_normalized = _unique_normalized_keys(extra)
    pairs: list[tuple[MappingParityKey, MappingParityKey]] = []
    common_normalized_keys = set(legacy_by_normalized) & set(canonical_by_normalized)
    for normalized_key in sorted(common_normalized_keys):
        legacy_key = legacy_by_normalized[normalized_key]
        canonical_key = canonical_by_normalized[normalized_key]
        if legacy_key != canonical_key:
            pairs.append((legacy_key, canonical_key))
    return pairs


def _unique_normalized_keys(
    keys: list[MappingParityKey],
) -> dict[MappingParityKey, MappingParityKey]:
    by_normalized: dict[MappingParityKey, MappingParityKey] = {}
    duplicates: set[MappingParityKey] = set()
    for key in keys:
        normalized_key = _normalized_key(key)
        if normalized_key in by_normalized:
            duplicates.add(normalized_key)
            continue
        by_normalized[normalized_key] = key
    for duplicate in duplicates:
        by_normalized.pop(duplicate, None)
    return by_normalized


def _normalized_key(key: MappingParityKey) -> MappingParityKey:
    return MappingParityKey(
        source_standard=key.source_standard,
        source_code=_normalize_public_code(key.source_standard, key.source_code),
        target_standard=key.target_standard,
        target_code=_normalize_public_code(key.target_standard, key.target_code),
    )


def _normalize_public_code(standard: str, code: str) -> str:
    normalized = re.sub(r"\s+", " ", (code or "").strip())
    if standard.upper() != "GRI":
        return normalized
    if any(token in normalized for token in COMPOSITE_CODE_TOKENS):
        return normalized
    normalized = re.sub(r"\s+\.", ".", normalized)
    normalized = re.sub(r"\.\s+", ".", normalized)
    normalized = ROMAN_SUFFIX_RE.sub(r".\1", normalized)
    normalized = TRAILING_LETTER_HYPHEN_RE.sub(r"\1", normalized)
    return normalized


def _clean_relationship(value: str | None) -> str | None:
    return (value or "").strip().lower() or None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _sample_keys(
    keys: list[MappingParityKey], sample_limit: int
) -> list[dict[str, str]]:
    return [key.as_dict() for key in keys[:sample_limit]]


def _work_item(
    issue_type: str,
    key: MappingParityKey,
    *,
    field: str | None = None,
    legacy_value: Any = None,
    canonical_value: Any = None,
) -> MappingParityWorkItem:
    return MappingParityWorkItem(
        issue_type=issue_type,
        key=key,
        field=field,
        legacy_value=legacy_value,
        canonical_value=canonical_value,
        suggested_classification=ISSUE_CLASSIFICATION_HINTS[issue_type],
    )
