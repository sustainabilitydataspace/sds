"""Operational relationship policy for canonical mapping packages."""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping

OPERATIONAL_RELATIONSHIP_TYPES = frozenset(
    {
        "equivalent",
        "partial",
        "broader",
        "narrower",
    }
)

RELATIONSHIP_ALIASES = {
    "same_as": "equivalent",
    "exact": "equivalent",
    "exact_match": "equivalent",
    "close_match": "partial",
    "overlap": "partial",
    "overlaps": "partial",
    "broad_match": "broader",
    "narrow_match": "narrower",
}


def normalize_relationship_type(value: str | None) -> str:
    """Return the canonical relationship token used by SDS operational surfaces."""

    normalized = (value or "equivalent").strip().lower().replace("-", "_")
    return RELATIONSHIP_ALIASES.get(normalized, normalized)


def is_operational_relationship_type(value: str | None) -> bool:
    """Whether SDS can safely import/materialize the relationship operationally."""

    return normalize_relationship_type(value) in OPERATIONAL_RELATIONSHIP_TYPES


def relationship_type_counts(rows: Iterable[Mapping[str, str]]) -> dict[str, int]:
    """Count normalized relationship types in package assertion-group rows."""

    counts = Counter(
        normalize_relationship_type(row.get("relationship_type")) for row in rows
    )
    return dict(sorted(counts.items()))


def non_operational_relationship_type_counts(
    rows: Iterable[Mapping[str, str]],
) -> dict[str, int]:
    """Count relationship types that must stay in validator/inspection lanes."""

    counts = Counter()
    for row in rows:
        relationship_type = normalize_relationship_type(row.get("relationship_type"))
        if relationship_type not in OPERATIONAL_RELATIONSHIP_TYPES:
            counts[relationship_type] += 1
    return dict(sorted(counts.items()))


def non_operational_relationship_blockers(
    counts: Mapping[str, int],
) -> list[str]:
    """Build human-readable blockers for DB import/workflow reports."""

    return [
        (
            f"{relationship_type}: {count} assertion group(s) are validator/"
            "inspection-only and cannot be committed to operational SDS surfaces"
        )
        for relationship_type, count in sorted(counts.items())
    ]
