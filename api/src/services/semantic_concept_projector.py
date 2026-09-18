"""Deterministic projector from the indicator catalog into semantic concepts."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.database.models import (
    CanonicalConcept,
    CanonicalConceptIndicatorLink,
    Concept,
    ConceptIndicatorLink,
    ConceptState,
    Indicator,
    StandardDatapoint,
    StandardRelease,
)

PROJECTION_SOURCE = "indicator_catalog"
PROJECTION_VERSION = "indicator-catalog-v1"
DISCLOSURE_PROJECTION_SOURCE = "disclosure_catalog"
DISCLOSURE_PROJECTION_VERSION = "disclosure-catalog-v1"
STANDARD_DATAPOINT_PROJECTION_SOURCE = "standard_datapoint_catalog"
STANDARD_DATAPOINT_PROJECTION_VERSION = "standard-datapoint-catalog-v1"
LINK_TYPE = "catalog_indicator"
CURRENT_SOURCE_STANDARDS = {"CSRD", "ESRS", "GRI", "GHG", "GHG_PROTOCOL"}
FUTURE_SOURCE_STANDARDS = {"CDP", "ISSB", "SASB", "TCFD", "UN_SDG"}


@dataclass
class SemanticProjectionResult:
    """Summary of one deterministic semantic catalog projection run."""

    active_indicators: int = 0
    canonical_created: int = 0
    canonical_revised: int = 0
    canonical_unchanged: int = 0
    concepts_created: int = 0
    concepts_updated: int = 0
    concepts_unchanged: int = 0
    disclosure_catalog_created: int = 0
    disclosure_catalog_updated: int = 0
    disclosure_catalog_unchanged: int = 0
    disclosure_catalog_deleted: int = 0
    source_datapoints_considered: int = 0
    source_datapoints_projected: int = 0
    source_datapoint_catalog_created: int = 0
    source_datapoint_catalog_updated: int = 0
    source_datapoint_catalog_unchanged: int = 0
    source_datapoint_catalog_deleted: int = 0
    links_created: int = 0
    links_unchanged: int = 0
    missing_active_indicator_identifiers: list[str] | None = None
    stale_active_indicator_identifiers: list[str] | None = None
    missing_disclosure_uris: list[str] | None = None
    stale_disclosure_uris: list[str] | None = None
    extra_disclosure_uris: list[str] | None = None
    missing_source_datapoint_uris: list[str] | None = None
    stale_source_datapoint_uris: list[str] | None = None
    extra_source_datapoint_uris: list[str] | None = None
    unsupported_legacy_disclosure_uris: list[str] | None = None
    future_source_standards: list[str] | None = None
    unsupported_active_standard_ids: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["missing_active_indicator_identifiers"] = (
            self.missing_active_indicator_identifiers or []
        )
        payload["stale_active_indicator_identifiers"] = (
            self.stale_active_indicator_identifiers or []
        )
        payload["missing_disclosure_uris"] = self.missing_disclosure_uris or []
        payload["stale_disclosure_uris"] = self.stale_disclosure_uris or []
        payload["extra_disclosure_uris"] = self.extra_disclosure_uris or []
        payload["missing_source_datapoint_uris"] = (
            self.missing_source_datapoint_uris or []
        )
        payload["stale_source_datapoint_uris"] = self.stale_source_datapoint_uris or []
        payload["extra_source_datapoint_uris"] = self.extra_source_datapoint_uris or []
        payload["unsupported_legacy_disclosure_uris"] = (
            self.unsupported_legacy_disclosure_uris or []
        )
        payload["future_source_standards"] = self.future_source_standards or []
        payload["unsupported_active_standard_ids"] = (
            self.unsupported_active_standard_ids or []
        )
        payload["projected_active_indicators"] = max(
            self.active_indicators
            - len(payload["missing_active_indicator_identifiers"])
            - len(payload["stale_active_indicator_identifiers"]),
            0,
        )
        payload["ready"] = (
            self.active_indicators > 0
            and not payload["missing_active_indicator_identifiers"]
            and not payload["stale_active_indicator_identifiers"]
            and not payload["missing_disclosure_uris"]
            and not payload["stale_disclosure_uris"]
            and not payload["extra_disclosure_uris"]
            and not payload["missing_source_datapoint_uris"]
            and not payload["stale_source_datapoint_uris"]
            and not payload["extra_source_datapoint_uris"]
            and not payload["unsupported_legacy_disclosure_uris"]
            and not payload["unsupported_active_standard_ids"]
        )
        return payload


class SemanticConceptProjector:
    """Project active SDS indicators into canonical and read-model concepts."""

    def __init__(
        self,
        db: Session,
        *,
        now_factory: Callable[[], datetime] | None = None,
    ):
        self.db = db
        self._now_factory = now_factory or datetime.utcnow

    def project(self, *, commit: bool = True) -> SemanticProjectionResult:
        """Materialize the current active indicator catalog as semantic concepts."""
        result = SemanticProjectionResult()
        try:
            indicators = self._active_indicators()
            result.active_indicators = len(indicators)
            indicator_payloads: list[dict[str, Any]] = []

            for indicator in indicators:
                payload = _payload_for_indicator(indicator)
                indicator_payloads.append(payload)
                payload_hash = _projection_hash(payload)
                canonical, canonical_status = self._upsert_canonical_concept(
                    indicator=indicator,
                    payload=payload,
                    payload_hash=payload_hash,
                )
                if canonical_status == "created":
                    result.canonical_created += 1
                elif canonical_status == "revised":
                    result.canonical_revised += 1
                else:
                    result.canonical_unchanged += 1

                concept_status = self._upsert_read_model_concept(
                    canonical=canonical,
                    payload=payload,
                    payload_hash=payload_hash,
                )
                if concept_status == "created":
                    result.concepts_created += 1
                elif concept_status == "updated":
                    result.concepts_updated += 1
                else:
                    result.concepts_unchanged += 1

                result.links_created += self._ensure_links(
                    canonical=canonical, indicator=indicator
                )

            disclosure_statuses = self._upsert_disclosure_catalog(indicator_payloads)
            result.disclosure_catalog_created = disclosure_statuses["created"]
            result.disclosure_catalog_updated = disclosure_statuses["updated"]
            result.disclosure_catalog_unchanged = disclosure_statuses["unchanged"]
            result.disclosure_catalog_deleted = disclosure_statuses["deleted"]

            source_datapoints = self._active_source_datapoints()
            result.source_datapoints_considered = len(source_datapoints)
            source_datapoint_payloads = [
                payload
                for release, datapoint in source_datapoints
                if (payload := _source_datapoint_payload(release, datapoint))
                is not None
            ]
            result.source_datapoints_projected = len(source_datapoint_payloads)
            source_datapoint_statuses = self._upsert_source_datapoint_catalog(
                source_datapoint_payloads
            )
            result.source_datapoint_catalog_created = source_datapoint_statuses[
                "created"
            ]
            result.source_datapoint_catalog_updated = source_datapoint_statuses[
                "updated"
            ]
            result.source_datapoint_catalog_unchanged = source_datapoint_statuses[
                "unchanged"
            ]
            result.source_datapoint_catalog_deleted = source_datapoint_statuses[
                "deleted"
            ]

            self.db.flush()
            coverage = self.coverage_summary()
            result.missing_active_indicator_identifiers = coverage[
                "missing_active_indicator_identifiers"
            ]
            result.stale_active_indicator_identifiers = coverage[
                "stale_active_indicator_identifiers"
            ]
            result.missing_disclosure_uris = coverage["missing_disclosure_uris"]
            result.stale_disclosure_uris = coverage["stale_disclosure_uris"]
            result.extra_disclosure_uris = coverage["extra_disclosure_uris"]
            result.missing_source_datapoint_uris = coverage[
                "missing_source_datapoint_uris"
            ]
            result.stale_source_datapoint_uris = coverage["stale_source_datapoint_uris"]
            result.extra_source_datapoint_uris = coverage["extra_source_datapoint_uris"]
            result.unsupported_legacy_disclosure_uris = coverage[
                "unsupported_legacy_disclosure_uris"
            ]
            result.future_source_standards = coverage["future_source_standards"]
            result.unsupported_active_standard_ids = coverage[
                "unsupported_active_standard_ids"
            ]
            if commit:
                self.db.commit()
        except Exception:
            if commit:
                self.db.rollback()
            raise
        return result

    def _active_source_datapoints(
        self,
    ) -> list[tuple[StandardRelease, StandardDatapoint]]:
        rows = (
            self.db.query(StandardRelease, StandardDatapoint)
            .join(
                StandardDatapoint,
                StandardDatapoint.standard_release_id == StandardRelease.id,
            )
            .filter(
                StandardRelease.lifecycle_status == "active",
                StandardDatapoint.lifecycle_status == "active",
            )
            .order_by(
                StandardRelease.standard_id.asc(),
                StandardRelease.version.asc(),
                StandardDatapoint.code.asc(),
            )
            .all()
        )
        return [(release, datapoint) for release, datapoint in rows]

    def _upsert_source_datapoint_catalog(
        self, source_datapoint_payloads: list[dict[str, Any]]
    ) -> dict[str, int]:
        statuses = {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0}
        active_uris: set[str] = set()
        for payload in source_datapoint_payloads:
            active_uris.add(payload["uri"])
            payload_hash = _projection_hash(payload)
            status = self._upsert_unlinked_concept(
                payload,
                payload_hash,
                projection_source=STANDARD_DATAPOINT_PROJECTION_SOURCE,
                projection_version=STANDARD_DATAPOINT_PROJECTION_VERSION,
            )
            statuses[status] += 1

        stale_rows = (
            self.db.query(Concept)
            .filter(Concept.projection_source == STANDARD_DATAPOINT_PROJECTION_SOURCE)
            .all()
        )
        for concept in stale_rows:
            if concept.uri not in active_uris:
                self.db.delete(concept)
                statuses["deleted"] += 1
        return statuses

    def _upsert_disclosure_catalog(
        self, indicator_payloads: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Project standard-level disclosure parent nodes from active datapoints."""
        statuses = {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0}
        active_parent_uris: set[str] = set()
        for parent_payload in _expected_disclosure_payloads(indicator_payloads):
            active_parent_uris.add(parent_payload["uri"])
            payload_hash = _projection_hash(parent_payload)
            status = self._upsert_unlinked_concept(
                parent_payload,
                payload_hash,
                projection_source=DISCLOSURE_PROJECTION_SOURCE,
                projection_version=DISCLOSURE_PROJECTION_VERSION,
            )
            statuses[status] += 1

        stale_parents = (
            self.db.query(Concept)
            .filter(Concept.projection_source == DISCLOSURE_PROJECTION_SOURCE)
            .all()
        )
        for concept in stale_parents:
            if concept.uri not in active_parent_uris:
                self.db.delete(concept)
                statuses["deleted"] += 1

        return statuses

    def _upsert_unlinked_concept(
        self,
        payload: dict[str, Any],
        payload_hash: str,
        *,
        projection_source: str,
        projection_version: str,
    ) -> str:
        concept = self.db.query(Concept).filter(Concept.uri == payload["uri"]).first()
        now = self._now_factory()
        if concept is None:
            self.db.add(
                Concept(
                    uri=payload["uri"],
                    indicator_id=None,
                    label=payload["label"],
                    description=payload["description"],
                    taxonomy=payload["taxonomy"],
                    concept_type=payload["concept_type"],
                    unit=payload["unit"],
                    temporal_granularity=payload["temporal_granularity"],
                    hierarchy_level=payload["hierarchy_level"],
                    concept_state=payload["concept_state"],
                    projection_source=projection_source,
                    projection_hash=payload_hash,
                    projection_version=projection_version,
                    projection_metadata=payload["metadata"],
                    projected_at=now,
                )
            )
            self.db.flush()
            return "created"

        fields = {
            "indicator_id": None,
            "label": payload["label"],
            "description": payload["description"],
            "taxonomy": payload["taxonomy"],
            "concept_type": payload["concept_type"],
            "unit": payload["unit"],
            "temporal_granularity": payload["temporal_granularity"],
            "hierarchy_level": payload["hierarchy_level"],
            "concept_state": payload["concept_state"],
            "projection_source": projection_source,
            "projection_hash": payload_hash,
            "projection_version": projection_version,
            "projection_metadata": payload["metadata"],
        }
        changed = any(getattr(concept, key) != value for key, value in fields.items())
        if not changed:
            return "unchanged"

        for key, value in fields.items():
            setattr(concept, key, value)
        concept.projected_at = now
        return "updated"

    def coverage_summary(self) -> dict[str, Any]:
        """Return the active-indicator projection coverage invariant.

        Counts a row as *fresh* only when the stored projection hash and version
        match the current deterministic payload for the indicator. Stale rows
        (indicator mutated since last projection) are reported separately so
        readiness checks and gates cannot pass with stale concepts.
        """
        indicators = (
            self.db.query(Indicator)
            .filter(Indicator.is_active.is_(True))
            .order_by(Indicator.identifier.asc())
            .all()
        )
        active_identifiers = [indicator.identifier for indicator in indicators]
        indicator_payloads = [
            _payload_for_indicator(indicator) for indicator in indicators
        ]

        projected_identifiers: set[str] = set()
        present_identifiers: set[str] = set()
        stale_identifiers: list[str] = []
        for indicator in indicators:
            payload = _payload_for_indicator(indicator)
            payload_hash = _projection_hash(payload)
            concept = (
                self.db.query(Concept)
                .filter(
                    Concept.uri == indicator.identifier,
                    Concept.indicator_id == indicator.id,
                    Concept.projection_source == PROJECTION_SOURCE,
                )
                .first()
            )
            if concept is None:
                continue
            present_identifiers.add(indicator.identifier)
            if (
                concept.projection_hash == payload_hash
                and concept.projection_version == PROJECTION_VERSION
            ):
                projected_identifiers.add(indicator.identifier)
            else:
                stale_identifiers.append(indicator.identifier)

        missing = [
            identifier
            for identifier in active_identifiers
            if identifier not in present_identifiers
        ]
        (
            active_disclosure_uris,
            projected_disclosure_uris,
            missing_disclosure_uris,
            stale_disclosure_uris,
            extra_disclosure_uris,
        ) = self._disclosure_catalog_coverage(indicator_payloads)
        (
            active_source_datapoint_uris,
            projected_source_datapoint_uris,
            missing_source_datapoint_uris,
            stale_source_datapoint_uris,
            extra_source_datapoint_uris,
        ) = self._source_datapoint_coverage()
        unsupported_legacy_disclosure_uris = self._unsupported_legacy_disclosure_uris()
        future_source_standards, unsupported_active_standard_ids = (
            self._active_standard_classification()
        )
        ready = (
            bool(active_identifiers)
            and not missing
            and not stale_identifiers
            and not missing_disclosure_uris
            and not stale_disclosure_uris
            and not extra_disclosure_uris
            and not missing_source_datapoint_uris
            and not stale_source_datapoint_uris
            and not extra_source_datapoint_uris
            and not unsupported_legacy_disclosure_uris
            and not unsupported_active_standard_ids
        )
        return {
            "active_indicators": len(active_identifiers),
            "projected_active_indicators": len(projected_identifiers),
            "missing_active_indicator_identifiers": missing,
            "stale_active_indicator_identifiers": stale_identifiers,
            "active_disclosures": len(active_disclosure_uris),
            "projected_disclosures": len(projected_disclosure_uris),
            "missing_disclosure_uris": missing_disclosure_uris,
            "stale_disclosure_uris": stale_disclosure_uris,
            "extra_disclosure_uris": extra_disclosure_uris,
            "active_source_datapoints": len(active_source_datapoint_uris),
            "projected_source_datapoints": len(projected_source_datapoint_uris),
            "missing_source_datapoint_uris": missing_source_datapoint_uris,
            "stale_source_datapoint_uris": stale_source_datapoint_uris,
            "extra_source_datapoint_uris": extra_source_datapoint_uris,
            "unsupported_legacy_disclosure_uris": unsupported_legacy_disclosure_uris,
            "future_source_standards": future_source_standards,
            "unsupported_active_standard_ids": unsupported_active_standard_ids,
            "ready": ready,
        }

    def _disclosure_catalog_coverage(
        self, indicator_payloads: list[dict[str, Any]]
    ) -> tuple[list[str], set[str], list[str], list[str], list[str]]:
        payloads = _expected_disclosure_payloads(indicator_payloads)
        active_uris = [payload["uri"] for payload in payloads]
        expected_by_uri = {payload["uri"]: payload for payload in payloads}
        projected_uris: set[str] = set()
        stale_uris: list[str] = []

        rows = (
            self.db.query(Concept)
            .filter(Concept.projection_source == DISCLOSURE_PROJECTION_SOURCE)
            .all()
        )
        rows_by_uri = {row.uri: row for row in rows}
        for uri, payload in expected_by_uri.items():
            concept = rows_by_uri.get(uri)
            if concept is None:
                continue
            payload_hash = _projection_hash(payload)
            if (
                concept.projection_hash == payload_hash
                and concept.projection_version == DISCLOSURE_PROJECTION_VERSION
            ):
                projected_uris.add(uri)
            else:
                stale_uris.append(uri)

        missing_uris = [uri for uri in active_uris if uri not in rows_by_uri]
        extra_uris = sorted(uri for uri in rows_by_uri if uri not in expected_by_uri)
        return active_uris, projected_uris, missing_uris, stale_uris, extra_uris

    def _source_datapoint_coverage(
        self,
    ) -> tuple[list[str], set[str], list[str], list[str], list[str]]:
        payloads = [
            payload
            for release, datapoint in self._active_source_datapoints()
            if (payload := _source_datapoint_payload(release, datapoint)) is not None
        ]
        active_uris = [payload["uri"] for payload in payloads]
        expected_by_uri = {payload["uri"]: payload for payload in payloads}
        projected_uris: set[str] = set()
        stale_uris: list[str] = []
        rows = (
            self.db.query(Concept)
            .filter(Concept.projection_source == STANDARD_DATAPOINT_PROJECTION_SOURCE)
            .all()
        )
        rows_by_uri = {row.uri: row for row in rows}
        for uri, payload in expected_by_uri.items():
            concept = rows_by_uri.get(uri)
            if concept is None:
                continue
            payload_hash = _projection_hash(payload)
            if (
                concept.projection_hash == payload_hash
                and concept.projection_version == STANDARD_DATAPOINT_PROJECTION_VERSION
            ):
                projected_uris.add(uri)
            else:
                stale_uris.append(uri)

        missing_uris = [uri for uri in active_uris if uri not in rows_by_uri]
        extra_uris = sorted(uri for uri in rows_by_uri if uri not in expected_by_uri)
        return active_uris, projected_uris, missing_uris, stale_uris, extra_uris

    def _unsupported_legacy_disclosure_uris(self) -> list[str]:
        rows = (
            self.db.query(Concept.uri)
            .filter(
                func.upper(Concept.taxonomy).in_(("CSRD", "GRI")),
                func.upper(Concept.concept_type) == "DISCLOSURE",
                Concept.projection_source.is_(None),
            )
            .order_by(Concept.uri.asc())
            .all()
        )
        return [uri for (uri,) in rows]

    def _active_standard_classification(self) -> tuple[list[str], list[str]]:
        rows = (
            self.db.query(StandardRelease.standard_id)
            .filter(StandardRelease.lifecycle_status == "active")
            .distinct()
            .all()
        )
        future: set[str] = set()
        unsupported: set[str] = set()
        for (standard_id,) in rows:
            normalized = _normalize_standard_id(standard_id)
            if normalized in CURRENT_SOURCE_STANDARDS:
                continue
            if normalized in FUTURE_SOURCE_STANDARDS:
                future.add(normalized)
            else:
                unsupported.add(normalized or str(standard_id or ""))
        return sorted(future), sorted(unsupported)

    def _active_indicators(self) -> list[Indicator]:
        return (
            self.db.query(Indicator)
            .filter(Indicator.is_active.is_(True))
            .order_by(Indicator.identifier.asc())
            .all()
        )

    def _upsert_canonical_concept(
        self,
        *,
        indicator: Indicator,
        payload: dict[str, Any],
        payload_hash: str,
    ) -> tuple[CanonicalConcept, str]:
        current = (
            self.db.query(CanonicalConcept)
            .filter(
                CanonicalConcept.canonical_uri == payload["uri"],
                CanonicalConcept.effective_to.is_(None),
                CanonicalConcept.projection_source == PROJECTION_SOURCE,
            )
            .order_by(CanonicalConcept.revision.desc(), CanonicalConcept.id.desc())
            .first()
        )
        now = self._now_factory()
        if current is None:
            canonical = self._new_canonical_concept(
                indicator=indicator,
                payload=payload,
                payload_hash=payload_hash,
                revision=self._next_canonical_revision(payload["uri"]),
                now=now,
            )
            self.db.add(canonical)
            self.db.flush()
            return canonical, "created"

        if (
            current.projection_hash == payload_hash
            and current.projection_version == PROJECTION_VERSION
        ):
            return current, "unchanged"

        current.effective_to = now
        canonical = self._new_canonical_concept(
            indicator=indicator,
            payload=payload,
            payload_hash=payload_hash,
            revision=self._next_canonical_revision(payload["uri"]),
            now=now,
        )
        self.db.add(canonical)
        self.db.flush()
        current.superseded_by = canonical.id
        return canonical, "revised"

    def _next_canonical_revision(self, canonical_uri: str) -> int:
        max_revision = (
            self.db.query(func.max(CanonicalConcept.revision))
            .filter(CanonicalConcept.canonical_uri == canonical_uri)
            .scalar()
        )
        return int(max_revision or 0) + 1

    def _new_canonical_concept(
        self,
        *,
        indicator: Indicator,
        payload: dict[str, Any],
        payload_hash: str,
        revision: int,
        now: datetime,
    ) -> CanonicalConcept:
        return CanonicalConcept(
            canonical_uri=payload["uri"],
            revision=revision,
            effective_from=now,
            indicator_id=indicator.id,
            label=payload["label"],
            description=payload["description"],
            taxonomy=payload["taxonomy"],
            concept_type=payload["concept_type"],
            unit=payload["unit"],
            temporal_granularity=payload["temporal_granularity"],
            hierarchy_level=payload["hierarchy_level"],
            concept_state=payload["concept_state"],
            projection_source=PROJECTION_SOURCE,
            projection_hash=payload_hash,
            projection_version=PROJECTION_VERSION,
            projection_metadata=payload["metadata"],
            projected_at=now,
            created_by="semantic_concept_projector",
        )

    def _upsert_read_model_concept(
        self,
        *,
        canonical: CanonicalConcept,
        payload: dict[str, Any],
        payload_hash: str,
    ) -> str:
        concept = self.db.query(Concept).filter(Concept.uri == payload["uri"]).first()
        if concept is None:
            self.db.add(
                Concept(
                    uri=payload["uri"],
                    indicator_id=payload["indicator_id"],
                    label=payload["label"],
                    description=payload["description"],
                    taxonomy=payload["taxonomy"],
                    concept_type=payload["concept_type"],
                    unit=payload["unit"],
                    temporal_granularity=payload["temporal_granularity"],
                    hierarchy_level=payload["hierarchy_level"],
                    concept_state=payload["concept_state"],
                    projection_source=PROJECTION_SOURCE,
                    projection_hash=payload_hash,
                    projection_version=PROJECTION_VERSION,
                    projection_metadata={
                        **payload["metadata"],
                        "canonical_concept_id": canonical.id,
                        "canonical_revision": canonical.revision,
                    },
                    projected_at=self._now_factory(),
                )
            )
            self.db.flush()
            return "created"

        next_metadata = {
            **payload["metadata"],
            "canonical_concept_id": canonical.id,
            "canonical_revision": canonical.revision,
        }
        fields = {
            "indicator_id": payload["indicator_id"],
            "label": payload["label"],
            "description": payload["description"],
            "taxonomy": payload["taxonomy"],
            "concept_type": payload["concept_type"],
            "unit": payload["unit"],
            "temporal_granularity": payload["temporal_granularity"],
            "hierarchy_level": payload["hierarchy_level"],
            "concept_state": payload["concept_state"],
            "projection_source": PROJECTION_SOURCE,
            "projection_hash": payload_hash,
            "projection_version": PROJECTION_VERSION,
            "projection_metadata": next_metadata,
        }
        changed = any(getattr(concept, key) != value for key, value in fields.items())
        if not changed:
            return "unchanged"

        for key, value in fields.items():
            setattr(concept, key, value)
        concept.projected_at = self._now_factory()
        return "updated"

    def _ensure_links(
        self, *, canonical: CanonicalConcept, indicator: Indicator
    ) -> int:
        created = 0
        canonical_link = (
            self.db.query(CanonicalConceptIndicatorLink)
            .filter(
                CanonicalConceptIndicatorLink.canonical_concept_id == canonical.id,
                CanonicalConceptIndicatorLink.indicator_id == indicator.id,
                CanonicalConceptIndicatorLink.link_type == LINK_TYPE,
            )
            .first()
        )
        if canonical_link is None:
            self.db.add(
                CanonicalConceptIndicatorLink(
                    canonical_concept_id=canonical.id,
                    indicator_id=indicator.id,
                    link_type=LINK_TYPE,
                    confidence=1.0,
                    rationale="Deterministic projection from active indicator catalog.",
                )
            )
            created += 1

        concept = (
            self.db.query(Concept).filter(Concept.uri == canonical.canonical_uri).one()
        )
        concept_link = (
            self.db.query(ConceptIndicatorLink)
            .filter(
                ConceptIndicatorLink.concept_id == concept.id,
                ConceptIndicatorLink.indicator_id == indicator.id,
                ConceptIndicatorLink.link_type == LINK_TYPE,
            )
            .first()
        )
        if concept_link is None:
            self.db.add(
                ConceptIndicatorLink(
                    concept_id=concept.id,
                    indicator_id=indicator.id,
                    link_type=LINK_TYPE,
                    confidence=1.0,
                    rationale="Deterministic projection from active indicator catalog.",
                )
            )
            created += 1
        return created


def _payload_for_indicator(indicator: Indicator) -> dict[str, Any]:
    taxonomy = _taxonomy_for_indicator(indicator)
    metadata = {
        "source": PROJECTION_SOURCE,
        "projector_version": PROJECTION_VERSION,
        "source_family": taxonomy,
        "indicator_identifier": indicator.identifier,
        "indicator_id": indicator.id,
        "dimension": indicator.dimension,
        "source_ref": indicator.source_ref,
        "code_esrs": indicator.code_esrs,
        "code_gri": indicator.code_gri,
        "code_gri_expanded": indicator.code_gri_expanded,
        "unit_name": indicator.unit_name,
        "unit_type": indicator.unit_type,
        "periodicity": indicator.periodicity,
        "period_type": indicator.period_type,
        "value_type": indicator.value_type,
        "source_row": indicator.source_row,
        "owner": indicator.owner,
        "access_rights": indicator.access_rights,
        "validation_method": indicator.validation_method,
        "double_materiality": indicator.double_materiality,
        "is_active": indicator.is_active,
    }
    return {
        "uri": indicator.identifier,
        "indicator_id": indicator.id,
        "label": _bounded(
            indicator.title or indicator.indicator_name or indicator.identifier, 500
        ),
        "description": indicator.description,
        "taxonomy": taxonomy,
        "concept_type": "Indicator",
        "unit": _bounded(indicator.unit_name or indicator.unit_type, 50),
        "temporal_granularity": _bounded(
            indicator.periodicity or indicator.period_type, 30
        ),
        "hierarchy_level": 0,
        "concept_state": _concept_state_value(indicator.concept_state),
        "metadata": metadata,
    }


def _taxonomy_for_indicator(indicator: Indicator) -> str:
    identifier = (indicator.identifier or "").lower()
    source_ref = (indicator.source_ref or "").lower()
    if _is_ghg_protocol_indicator(identifier=identifier, source_ref=source_ref):
        return "GHG"
    if (
        indicator.code_esrs
        or _has_standard_token(identifier, "esrs")
        or _has_standard_token(source_ref, "esrs")
    ):
        return "CSRD"
    if (
        indicator.code_gri
        or indicator.code_gri_expanded
        or _has_standard_token(identifier, "gri")
        or _has_standard_token(source_ref, "gri")
    ):
        return "GRI"
    return "Sygris"


def _is_ghg_protocol_indicator(*, identifier: str, source_ref: str) -> bool:
    if re.search(r"(^|[:/_-])ghg([:/_-]|$)", identifier):
        return True
    return (
        "ghg protocol" in source_ref
        or "ghg_scope" in source_ref
        or "wri/wbcsd" in source_ref
    )


def _source_datapoint_payload(
    release: StandardRelease, datapoint: StandardDatapoint
) -> dict[str, Any] | None:
    metadata_json = datapoint.metadata_json or {}
    if not isinstance(metadata_json, dict):
        metadata_json = {}
    if str(metadata_json.get("sds_identifier") or "").strip():
        return None

    taxonomy = _taxonomy_for_standard_id(release.standard_id)
    if taxonomy is None:
        return None

    concept_type = "Indicator" if taxonomy == "GHG" else "Disclosure"
    metadata = {
        "source": STANDARD_DATAPOINT_PROJECTION_SOURCE,
        "projector_version": STANDARD_DATAPOINT_PROJECTION_VERSION,
        "source_family": taxonomy,
        "standard_id": release.standard_id,
        "standard_release_id": release.id,
        "standard_release_name": release.name,
        "standard_release_version": release.version,
        "standard_release_date": (
            release.release_date.isoformat() if release.release_date else None
        ),
        "standard_datapoint_id": datapoint.id,
        "standard_datapoint_code": datapoint.code,
        "standard_datapoint_type": datapoint.datapoint_type,
        "source_metadata": metadata_json,
        "has_sds_identifier": False,
    }
    return {
        "uri": _source_datapoint_uri(release, datapoint, taxonomy),
        "indicator_id": None,
        "label": _bounded(datapoint.label or datapoint.code, 500),
        "description": datapoint.disclosure_text,
        "taxonomy": taxonomy,
        "concept_type": concept_type,
        "unit": _bounded(datapoint.unit, 50),
        "temporal_granularity": None,
        "hierarchy_level": 1,
        "concept_state": ConceptState.CATALOGUED.value,
        "metadata": metadata,
    }


def _taxonomy_for_standard_id(standard_id: str | None) -> str | None:
    normalized = _normalize_standard_id(standard_id)
    if normalized not in CURRENT_SOURCE_STANDARDS:
        return None
    if normalized in {"CSRD", "ESRS"}:
        return "CSRD"
    if normalized == "GRI":
        return "GRI"
    if normalized in {"GHG", "GHG_PROTOCOL"}:
        return "GHG"
    return None


def _normalize_standard_id(standard_id: str | None) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "_", (standard_id or "").strip().upper())
    return normalized.strip("_")


def _source_datapoint_uri(
    release: StandardRelease, datapoint: StandardDatapoint, taxonomy: str
) -> str:
    return ":".join(
        [
            "urn",
            "sds",
            "standard-datapoint",
            taxonomy.lower(),
            _slug(release.standard_id),
            _slug(release.version),
            _slug(datapoint.code),
        ]
    )


def _expected_disclosure_payloads(
    indicator_payloads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped_children: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for payload in indicator_payloads:
        parent_key = _parent_disclosure_key(payload)
        if parent_key is None:
            continue
        grouped_children.setdefault(parent_key, []).append(payload)

    return [
        _parent_disclosure_payload(
            taxonomy=taxonomy,
            parent_code=parent_code,
            children=children,
        )
        for (taxonomy, parent_code), children in sorted(grouped_children.items())
    ]


def _parent_disclosure_key(payload: dict[str, Any]) -> tuple[str, str] | None:
    taxonomy = payload.get("taxonomy")
    metadata = payload.get("metadata") or {}
    source_ref = str(metadata.get("source_ref") or "")
    if taxonomy == "CSRD":
        parent_code = _csrd_parent_code(
            source_ref=source_ref,
            code_esrs=str(metadata.get("code_esrs") or ""),
        )
    elif taxonomy == "GRI":
        parent_code = _gri_parent_code(
            source_ref=source_ref,
            code_gri=str(metadata.get("code_gri") or ""),
            code_gri_expanded=str(metadata.get("code_gri_expanded") or ""),
        )
    else:
        return None

    if not parent_code:
        return None
    return taxonomy, parent_code


def _csrd_parent_code(*, source_ref: str, code_esrs: str) -> str | None:
    match = re.search(r"\b([A-Z]{1,4}[0-9]?(?:-[A-Z0-9]+)+):P\d+\b", source_ref)
    if match:
        return match.group(1)
    if code_esrs:
        return re.split(r"[_:.]", code_esrs, maxsplit=1)[0] or None
    return None


def _gri_parent_code(
    *, source_ref: str, code_gri: str, code_gri_expanded: str
) -> str | None:
    candidates = [code_gri, code_gri_expanded, source_ref]
    for candidate in candidates:
        normalized = candidate.strip()
        if not normalized:
            continue
        topic_match = re.search(r"\b(\d{3}-\d+)\b", normalized)
        if topic_match:
            return topic_match.group(1)
        sector_match = re.search(
            r"\b(\d+(?:\.\d+){1,3})(?:[-.][a-zivx]+)?\b", normalized
        )
        if sector_match:
            return sector_match.group(1)
    return None


def _parent_disclosure_payload(
    *,
    taxonomy: str,
    parent_code: str,
    children: list[dict[str, Any]],
) -> dict[str, Any]:
    child_uris = sorted(child["uri"] for child in children)
    taxonomy_label = "ESRS" if taxonomy == "CSRD" else taxonomy
    slug = _slug(parent_code)
    metadata = {
        "source": DISCLOSURE_PROJECTION_SOURCE,
        "projector_version": DISCLOSURE_PROJECTION_VERSION,
        "source_family": taxonomy,
        "parent_code": parent_code,
        "child_concept_count": len(child_uris),
        "child_concept_uri_sample": child_uris[:50],
        "child_concept_uris_truncated": len(child_uris) > 50,
    }
    return {
        "uri": f"urn:sds:disclosure:{taxonomy.lower()}:{slug}",
        "indicator_id": None,
        "label": f"{taxonomy_label} {parent_code} disclosure",
        "description": (
            f"Disclosure-level grouping derived from {len(child_uris)} active "
            f"{taxonomy_label} SDS catalog datapoint concept(s)."
        ),
        "taxonomy": taxonomy,
        "concept_type": "Disclosure",
        "unit": None,
        "temporal_granularity": None,
        "hierarchy_level": 0,
        "concept_state": ConceptState.CATALOGUED.value,
        "metadata": metadata,
    }


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return slug.strip("-") or "unknown"


def _has_standard_token(value: str, token: str) -> bool:
    return (
        re.search(rf"(^|[^a-z0-9]){re.escape(token)}([^a-z0-9]|$)", value) is not None
    )


def _projection_hash(payload: dict[str, Any]) -> str:
    stable_payload = {
        key: value for key, value in payload.items() if key not in {"metadata"}
    }
    stable_payload["metadata"] = payload["metadata"]
    serialized = json.dumps(
        stable_payload,
        ensure_ascii=True,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _bounded(value: Any, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= max_length:
        return text
    return text[:max_length]


def _concept_state_value(value: Any) -> str:
    if isinstance(value, ConceptState):
        return value.value
    if value:
        return str(value)
    return ConceptState.CATALOGUED.value
