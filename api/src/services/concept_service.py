"""DB-backed semantic concept service for the public ontology router."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import structlog
from src.concept_catalog_policy import UNIFIED_SYGRIS_TAXONOMY
from src.concept_runtime_aliases import (
    concept_uri_candidates,
    normalize_runtime_concept_uri,
)
from src.database.repositories.concept_repository import ConceptRepository
from src.database.session import SessionLocal
from src.ontology.curie import DEFAULT_NAMESPACES
from src.services.canonical_data import canonical_data_required, require_canonical_data
from src.services.canonical_mapping_relationship_policy import (
    is_operational_relationship_type,
    normalize_relationship_type,
)
from src.services.localization import (
    DEFAULT_LANGUAGE,
    language_fallback_chain,
    normalize_language,
    text_hash,
)
from src.services.localization_service import LocalizationRepository
from src.services.semantic_concept_projector import (
    SemanticConceptProjector,
    _source_datapoint_payload,
)

logger = structlog.get_logger(__name__)


def _local_name(value: str) -> str:
    """Return a human-friendly local name for a URI-like identifier."""
    if not value:
        return ""
    for separator in ("#", "/", ":"):
        if separator in value:
            value = value.rsplit(separator, 1)[-1]
    return value


class ConceptService:
    """Service layer backed by canonical PostgreSQL semantic tables."""

    def __init__(self, db: Optional[Session] = None):
        self.logger = logger.bind(component="ConceptService")
        self._db = db if db is not None else SessionLocal()
        self._owns_session = db is None
        self._repo = ConceptRepository(self._db) if self._db is not None else None

    def close(self) -> None:
        """Close the owned database session, if any."""
        if self._owns_session and self._db is not None:
            try:
                self._db.close()
            except Exception:
                pass

    def _handle_failure(self, operation: str, error: Exception):
        if canonical_data_required():
            require_canonical_data(
                component="ConceptService",
                operation=operation,
                reason="semantic database query failed",
                error=error,
            )
        return None

    def has_semantic_data(self) -> bool:
        """Return whether canonical semantic concepts exist in the DB."""
        try:
            if self._repo is None:
                if canonical_data_required():
                    require_canonical_data(
                        component="ConceptService",
                        operation="has_semantic_data",
                        reason="no database session is available",
                    )
                return False
            return self._repo.count_concepts() > 0
        except Exception as exc:
            self.logger.error("Failed to count semantic concepts", error=str(exc))
            self._handle_failure("has_semantic_data", exc)
            return False

    def semantic_projection_coverage(self) -> Dict[str, Any]:
        """Return whether active catalog indicators have DB-backed concepts."""
        try:
            if self._db is None:
                if canonical_data_required():
                    require_canonical_data(
                        component="ConceptService",
                        operation="semantic_projection_coverage",
                        reason="no database session is available",
                    )
                return {
                    "active_indicators": 0,
                    "projected_active_indicators": 0,
                    "missing_active_indicator_identifiers": [],
                    "stale_active_indicator_identifiers": [],
                    "active_source_datapoints": 0,
                    "projected_source_datapoints": 0,
                    "missing_source_datapoint_uris": [],
                    "stale_source_datapoint_uris": [],
                    "ready": False,
                }
            return SemanticConceptProjector(self._db).coverage_summary()
        except Exception as exc:
            self.logger.error(
                "Failed to inspect semantic projection coverage", error=str(exc)
            )
            self._handle_failure("semantic_projection_coverage", exc)
            return {
                "active_indicators": 0,
                "projected_active_indicators": 0,
                "missing_active_indicator_identifiers": [],
                "stale_active_indicator_identifiers": [],
                "active_source_datapoints": 0,
                "projected_source_datapoints": 0,
                "missing_source_datapoint_uris": [],
                "stale_source_datapoint_uris": [],
                "ready": False,
                "error": str(exc),
            }

    def list_concepts_paginated(
        self,
        *,
        taxonomy: Optional[str] = None,
        concept_type: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Return DB-backed concepts plus total count for stable API pagination."""
        try:
            if self._repo is None:
                return [], 0
            total = self._repo.count_concepts(
                taxonomy=taxonomy,
                concept_type=concept_type,
                search=search,
                public_catalog=True,
            )
            concepts = self._repo.search_concepts(
                query=search,
                taxonomy=taxonomy,
                concept_type=concept_type,
                limit=limit,
                offset=offset,
                public_catalog=True,
            )
            return [
                self._concept_to_dict(
                    concept, formula=self._formula_from_concept(concept)
                )
                for concept in concepts
            ], total
        except Exception as exc:
            self.logger.error("Failed to list paginated concepts", error=str(exc))
            self._handle_failure("list_concepts_paginated", exc)
            return [], 0

    def get_concepts(
        self,
        taxonomy: str | None = None,
        concept_type: str | None = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List concepts from canonical DB semantic tables."""
        try:
            if self._repo is None:
                return []
            concepts = self._repo.list_concepts(
                taxonomy=taxonomy,
                concept_type=concept_type,
                limit=limit,
                public_catalog=True,
            )
            return [
                self._concept_to_dict(
                    concept, formula=self._formula_from_concept(concept)
                )
                for concept in concepts
            ]
        except Exception as exc:
            self.logger.error("Failed to get concepts", error=str(exc))
            self._handle_failure("get_concepts", exc)
            return []

    def search_concepts(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search concepts using deterministic DB-backed text matching."""
        try:
            if self._repo is None:
                return []
            concepts = self._repo.search_concepts(
                query=query,
                limit=limit,
                public_catalog=True,
            )
            scored = [
                (
                    self._similarity_score(
                        query, concept.label, concept.uri, concept.description
                    ),
                    concept,
                )
                for concept in concepts
            ]
            scored.sort(key=lambda item: (-item[0], item[1].uri))
            return [
                self._concept_to_dict(concept, similarity_score=score)
                for score, concept in scored[:limit]
            ]
        except Exception as exc:
            self.logger.error("Failed to search concepts", error=str(exc))
            self._handle_failure("search_concepts", exc)
            return []

    def get_concept_by_uri(
        self, uri: str, *, public_catalog: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Get a single concept by canonical URI."""
        try:
            if self._repo is None:
                return None
            concept = None
            for candidate in concept_uri_candidates(uri):
                concept = (
                    self._repo.get_public_by_uri_with_children(candidate)
                    if public_catalog
                    else self._repo.get_by_uri_with_children(candidate)
                )
                if concept is not None:
                    break
            if concept is None:
                return None

            contract = None
            loader = getattr(self._repo, "get_active_calculation_contract", None)
            if callable(loader):
                contract = loader(
                    getattr(concept, "indicator_id", None),
                    public_only=public_catalog,
                )
            payload = self._contract_payload(contract, public_only=public_catalog)

            formula = self._formula_from_concept(concept)
            if formula is None:
                formula = payload.get("_formula_fallback")
            projected_vars = [
                DEFAULT_NAMESPACES.compact(variable.variable_uri)
                for variable in concept.variables
            ]
            related_variables = projected_vars or payload.get("_vars_fallback", [])

            return self._concept_to_dict(
                concept,
                formula=formula,
                related_variables=related_variables,
                formula_kind=payload.get("formula_kind"),
                dimensions=payload.get("dimensions"),
                aggregation=payload.get("aggregation"),
                calculation_contract_id=payload.get("calculation_contract_id"),
                calculation_contract_version=payload.get(
                    "calculation_contract_version"
                ),
            )
        except Exception as exc:
            self.logger.error("Failed to get concept by URI", uri=uri, error=str(exc))
            self._handle_failure("get_concept_by_uri", exc)
            return None

    def get_variables(
        self, taxonomy: str | None = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Return operational variables backed by concept-variable links."""
        try:
            if self._repo is None:
                return []
            rows = self._repo.list_variables(taxonomy=taxonomy, limit=limit)
            return [
                self._variable_to_dict(variable, concept) for variable, concept in rows
            ]
        except Exception as exc:
            self.logger.error("Failed to get variables", error=str(exc))
            self._handle_failure("get_variables", exc)
            return []

    def get_relations_by_concept(self, concept_uri: str) -> List[Dict[str, Any]]:
        """Return all semantic relations involving a concept."""
        try:
            concept = self._get_concept(concept_uri)
            if concept is None:
                return []

            relations: List[Dict[str, Any]] = []
            for variable in self._repo.get_variables(concept.id):
                relations.append(
                    {
                        "subject_uri": concept.uri,
                        "predicate_uri": "hasVariable",
                        "object_uri": variable.variable_uri,
                        "relation_type": "hasVariable",
                        "source_ontology": concept.taxonomy,
                        "confidence": 1.0,
                    }
                )

            for equivalence in self._repo.get_equivalences(concept.id):
                relations.append(
                    {
                        "subject_uri": concept.uri,
                        "predicate_uri": equivalence.relationship_type,
                        "object_uri": equivalence.target_uri,
                        "relation_type": equivalence.relationship_type,
                        "source_ontology": equivalence.target_taxonomy
                        or concept.taxonomy,
                        "confidence": float(equivalence.confidence or 1.0),
                    }
                )

            for link in self._repo.get_indicator_links(concept.id):
                relations.append(
                    {
                        "subject_uri": concept.uri,
                        "predicate_uri": link.link_type,
                        "object_uri": link.indicator_id,
                        "relation_type": link.link_type,
                        "source_ontology": "postgres",
                        "confidence": float(link.confidence or 1.0),
                    }
                )

            relations.sort(key=lambda item: (item["relation_type"], item["object_uri"]))
            return relations
        except Exception as exc:
            self.logger.error(
                "Failed to get relations", concept_uri=concept_uri, error=str(exc)
            )
            self._handle_failure("get_relations_by_concept", exc)
            return []

    def find_equivalences(self, concept_uri: str) -> List[str]:
        """Find canonical equivalent/related concept URIs."""
        try:
            requested = {
                normalize_runtime_concept_uri(candidate)
                for candidate in concept_uri_candidates(concept_uri)
            }
            rows = self._list_equivalence_rows(concept_uri=concept_uri)
            if not rows:
                return []

            equivalents: List[str] = []
            for concept, equivalence in rows:
                source_curie = normalize_runtime_concept_uri(concept.uri)
                target_curie = normalize_runtime_concept_uri(equivalence.target_uri)
                counterpart = (
                    target_curie if source_curie in requested else source_curie
                )
                if (
                    counterpart
                    and counterpart not in requested
                    and counterpart not in equivalents
                ):
                    equivalents.append(counterpart)
            return equivalents
        except Exception as exc:
            self.logger.error(
                "Failed to find equivalences", concept_uri=concept_uri, error=str(exc)
            )
            self._handle_failure("find_equivalences", exc)
            return []

    def list_equivalences(
        self,
        *,
        concept: Optional[str] = None,
        source_taxonomy: Optional[str] = None,
        target_taxonomy: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return public equivalence payloads from canonical DB semantic tables."""
        try:
            if self._repo is None:
                return []

            # This is the PUBLIC ontology read path: gate source concepts to the
            # public catalog so internal/future concept equivalences cannot leak
            # (codex F09 M2).
            rows = self._list_equivalence_rows(
                concept_uri=concept,
                source_taxonomy=source_taxonomy,
                target_taxonomy=target_taxonomy,
                public_catalog=True,
            )

            equivalences: List[Dict[str, Any]] = []
            # Metadata-preserving dedup key (codex P2 M3): explicit-equivalence rows
            # use empty metadata slots, so an assertion row for the same pair/type but
            # a distinct mapping_profile / assertion_group / component is NOT collapsed.
            seen: set[tuple] = set()
            for source_concept, equivalence in rows:
                source_curie = normalize_runtime_concept_uri(source_concept.uri)
                target_curie = normalize_runtime_concept_uri(equivalence.target_uri)
                equivalence_type = self._equivalence_bucket(
                    equivalence.relationship_type
                )
                key = (source_curie, target_curie, equivalence_type, None, None, None)
                if key in seen:
                    continue
                seen.add(key)
                equivalences.append(
                    {
                        "source_concept": source_curie,
                        "target_concept": target_curie,
                        "equivalence_type": equivalence_type,
                        "confidence": float(equivalence.confidence or 1.0),
                        "metadata": {
                            "relationship_type": equivalence.relationship_type,
                            "target_taxonomy": equivalence.target_taxonomy,
                        },
                    }
                )

            # Merge the curated external->Sygris assertion footprint, with
            # concept_equivalences taking precedence on byte-identical rows.
            for row in self._assertion_equivalences(
                concept=concept,
                source_taxonomy=source_taxonomy,
                target_taxonomy=target_taxonomy,
            ):
                meta = row["metadata"]
                key = (
                    row["source_concept"],
                    row["target_concept"],
                    row["equivalence_type"],
                    meta.get("mapping_profile"),
                    meta.get("assertion_group_id"),
                    meta.get("component_role"),
                )
                if key in seen:
                    continue
                seen.add(key)
                equivalences.append(row)

            return equivalences
        except Exception as exc:
            self.logger.error(
                "Failed to list equivalences", concept=concept, error=str(exc)
            )
            self._handle_failure("list_equivalences", exc)
            return []

    def get_variables_for_concept(self, concept_uri: str) -> List[Dict[str, Any]]:
        """Return variable details attached to a concept."""
        try:
            concept = self._get_concept(concept_uri)
            if concept is None:
                return []
            return [
                {
                    "uri": variable.variable_uri,
                    "taxonomy": concept.taxonomy,
                    "name": variable.variable_label
                    or _local_name(variable.variable_uri),
                    "description": variable.variable_label or concept.description or "",
                    "unit": concept.unit or "",
                    "data_type": "decimal",
                }
                for variable in self._repo.get_variables(concept.id)
            ]
        except Exception as exc:
            self.logger.error(
                "Failed to get variables for concept",
                concept_uri=concept_uri,
                error=str(exc),
            )
            self._handle_failure("get_variables_for_concept", exc)
            return []

    def _get_concept(self, concept_uri: str):
        if self._repo is None:
            return None
        for candidate in concept_uri_candidates(concept_uri):
            concept = self._repo.get_by_uri_with_children(candidate)
            if concept is not None:
                return concept
        return None

    def _list_equivalence_rows(
        self,
        *,
        concept_uri: Optional[str] = None,
        source_taxonomy: Optional[str] = None,
        target_taxonomy: Optional[str] = None,
        public_catalog: bool = False,
    ):
        if self._repo is None:
            return []
        if not concept_uri:
            return self._repo.list_equivalences(
                source_taxonomy=source_taxonomy,
                target_taxonomy=target_taxonomy,
                public_catalog=public_catalog,
            )

        rows = []
        for candidate in concept_uri_candidates(concept_uri):
            compact_concept = DEFAULT_NAMESPACES.compact(
                DEFAULT_NAMESPACES.expand(candidate)
            )
            rows.extend(
                self._repo.list_equivalences(
                    concept_uri=DEFAULT_NAMESPACES.expand(candidate),
                    compact_concept_uri=compact_concept,
                    source_taxonomy=source_taxonomy,
                    target_taxonomy=target_taxonomy,
                    public_catalog=public_catalog,
                )
            )
        return rows

    @staticmethod
    def _equivalence_bucket(relationship_type: Optional[str]) -> str:
        """Map a (possibly aliased) relationship type to a public equivalence bucket.

        Reuses the canonical operational normalizer (codex P2 M2) so aliases such as
        exact/same_as -> equivalent, overlap -> partial, broad_match/narrow_match ->
        broader/narrower are bucketed consistently with the rest of SDS.
        """
        normalized = normalize_relationship_type(relationship_type)
        if normalized == "equivalent":
            return "exact"
        if normalized in ("partial", "broader", "narrower"):
            return "partial"
        return "related"

    def _concept_filter_candidates(self, concept: Optional[str]) -> Optional[set[str]]:
        """Build the normalized/expanded/compact candidate set for a concept filter."""
        if not concept:
            return None
        candidates: set[str] = set()
        for candidate in concept_uri_candidates(concept):
            candidates.add(candidate)
            candidates.add(normalize_runtime_concept_uri(candidate))
            try:
                candidates.add(DEFAULT_NAMESPACES.compact(candidate))
                candidates.add(
                    DEFAULT_NAMESPACES.compact(DEFAULT_NAMESPACES.expand(candidate))
                )
            except Exception:
                pass
        candidates.discard("")
        return candidates

    def _assertion_equivalences(
        self,
        *,
        concept: Optional[str] = None,
        source_taxonomy: Optional[str] = None,
        target_taxonomy: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Project the curated external->Sygris assertion footprint as equivalences.

        Surfaces `mapping_assertion_groups` + components + canonical concepts that the
        `concept_equivalences`-only path misses. Source identity reuses the projector's
        datapoint-URI convention (so it lines up with the projected source-datapoint
        concept and the existing concept/source_taxonomy filters); the target is the
        Sygris canonical CURIE. Taxonomy/concept filters are applied here over the
        derived URIs (codex P2 M4 — not via `taxonomy_from_curie` on the generated URN).
        """
        lister = getattr(self._repo, "list_assertion_equivalence_rows", None)
        if not callable(lister):
            return []

        normalized_source = (
            source_taxonomy.strip().upper()
            if source_taxonomy and source_taxonomy.strip()
            else None
        )
        normalized_target = (
            target_taxonomy.strip().upper()
            if target_taxonomy and target_taxonomy.strip()
            else None
        )
        concept_candidates = self._concept_filter_candidates(concept)

        results: List[Dict[str, Any]] = []
        for group, release, datapoint, component, canonical in lister():
            payload = _source_datapoint_payload(release, datapoint)
            if payload is None:
                # SDS-native or non-current-source datapoint: no consistent public
                # source-concept identity, so it is not a public equivalence.
                continue
            source_uri = payload["uri"]
            source_tax = (payload.get("taxonomy") or "").upper()

            canonical_uri = canonical.canonical_uri
            target_curie = normalize_runtime_concept_uri(canonical_uri)
            target_tax = (
                UNIFIED_SYGRIS_TAXONOMY
                if str(canonical_uri).lower().startswith("syg:")
                else (canonical.taxonomy or "").upper()
            )

            if not is_operational_relationship_type(group.relationship_type):
                # validator/inspection-only relationship: never public-operational.
                continue
            equivalence_type = self._equivalence_bucket(group.relationship_type)

            if normalized_source and source_tax != normalized_source:
                continue
            if normalized_target and target_tax != normalized_target:
                continue
            if concept_candidates is not None:
                source_norm = normalize_runtime_concept_uri(source_uri)
                if not ({source_uri, source_norm, target_curie} & concept_candidates):
                    continue

            coverage_fraction = (
                float(component.coverage_fraction)
                if component.coverage_fraction is not None
                else None
            )
            results.append(
                {
                    "source_concept": source_uri,
                    "target_concept": target_curie,
                    "equivalence_type": equivalence_type,
                    "confidence": float(group.confidence or 1.0),
                    "metadata": {
                        "relationship_type": group.relationship_type,
                        "coverage_status": group.coverage_status,
                        "mapping_profile": group.mapping_profile,
                        "assertion_group_id": group.id,
                        "component_role": component.component_role,
                        "coverage_fraction": coverage_fraction,
                        "source_standard_id": release.standard_id,
                        "source_standard_version": release.version,
                        "source_code": datapoint.code,
                        "target_taxonomy": target_tax,
                        "provenance": "mapping_assertion",
                    },
                }
            )
        return results

    def list_taxonomies(self) -> Dict[str, Any]:
        """Return DB-backed taxonomy statistics mirroring the public API contract."""
        try:
            if self._repo is None:
                return {
                    "taxonomies": {},
                    "total_taxonomies": 0,
                    "total_concepts": 0,
                    "last_updated": None,
                }

            concepts = self._repo.list_all_public_catalog_concepts()
            by_taxonomy: Dict[str, Dict[str, Any]] = {}
            latest_updated = None

            def new_bucket(
                name: str, *, catalog_scope: str = "source_taxonomy"
            ) -> Dict[str, Any]:
                return {
                    "name": name,
                    "description": None,
                    "type": None,
                    "catalog_scope": catalog_scope,
                    "concepts_count": 0,
                    "indicators_count": 0,
                    "disclosures_count": 0,
                    "variables_count": 0,
                    "units_count": 0,
                    "version": None,
                }

            def add_to_bucket(bucket: Dict[str, Any], concept) -> None:
                bucket["concepts_count"] += 1
                if concept.concept_type == "Indicator":
                    bucket["indicators_count"] += 1
                elif concept.concept_type == "Disclosure":
                    bucket["disclosures_count"] += 1
                elif concept.concept_type == "Variable":
                    bucket["variables_count"] += 1
                elif concept.concept_type == "Unit":
                    bucket["units_count"] += 1

            def track_updated(concept) -> None:
                nonlocal latest_updated
                updated_at = getattr(concept, "updated_at", None) or getattr(
                    concept, "created_at", None
                )
                if updated_at and (
                    latest_updated is None or updated_at > latest_updated
                ):
                    latest_updated = updated_at

            for concept in concepts:
                bucket = by_taxonomy.setdefault(
                    concept.taxonomy,
                    new_bucket(concept.taxonomy),
                )
                add_to_bucket(bucket, concept)
                track_updated(concept)

            sygris_concepts = self._repo.list_all_public_catalog_concepts(
                taxonomy="Sygris"
            )
            if sygris_concepts:
                sygris_bucket = new_bucket(
                    "Sygris", catalog_scope="unified_public_catalog"
                )
                for concept in sygris_concepts:
                    add_to_bucket(sygris_bucket, concept)
                    track_updated(concept)
                by_taxonomy["Sygris"] = sygris_bucket

            return {
                "taxonomies": by_taxonomy,
                "total_taxonomies": len(by_taxonomy),
                "total_concepts": len(concepts),
                "last_updated": (
                    latest_updated.isoformat() if latest_updated is not None else None
                ),
            }
        except Exception as exc:
            self.logger.error("Failed to list taxonomies", error=str(exc))
            self._handle_failure("list_taxonomies", exc)
            return {
                "taxonomies": {},
                "total_taxonomies": 0,
                "total_concepts": 0,
                "last_updated": None,
            }

    @staticmethod
    def _concept_to_dict(
        concept,
        *,
        similarity_score: Optional[float] = None,
        formula: Optional[str] = None,
        related_variables: Optional[List[str]] = None,
        formula_kind: Optional[str] = None,
        dimensions: Optional[List[Dict[str, Any]]] = None,
        aggregation: Optional[Dict[str, Any]] = None,
        calculation_contract_id: Optional[str] = None,
        calculation_contract_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "uri": DEFAULT_NAMESPACES.compact(concept.uri),
            "taxonomy": concept.taxonomy,
            "concept_type": concept.concept_type,
            "type": concept.concept_type,
            "label": concept.label,
            "description": concept.description or "",
            "unit": concept.unit or "",
            "temporal_granularity": concept.temporal_granularity or "",
            "hierarchy_level": (
                concept.hierarchy_level if concept.hierarchy_level is not None else 0
            ),
        }
        if similarity_score is not None:
            payload["similarity_score"] = similarity_score
        if formula is not None:
            payload["formula"] = formula
        if related_variables is not None:
            payload["related_variables"] = related_variables
        if formula_kind is not None:
            payload["formula_kind"] = formula_kind
        if dimensions is not None:
            payload["dimensions"] = dimensions
        if aggregation is not None:
            payload["aggregation"] = aggregation
        if calculation_contract_id is not None:
            payload["calculation_contract_id"] = calculation_contract_id
        if calculation_contract_version is not None:
            payload["calculation_contract_version"] = calculation_contract_version
        return payload

    def localization_readiness(
        self, lang: str, *, field: str = "label", limit: int = 5000
    ) -> Dict[str, Any]:
        """Approved/current translation coverage for public concepts in ``lang``."""
        if self._db is None:
            return {
                "subject_kind": "concept",
                "field": field,
                "language": normalize_language(lang),
                "total_subjects": 0,
                "approved_current": 0,
                "stale": 0,
                "draft_only": 0,
                "missing": 0,
                "coverage_percent": 0.0,
            }
        items, _total = self.list_concepts_paginated(limit=limit, offset=0)
        subjects = {
            item["uri"]: {field: item.get(field) or ""}
            for item in items
            if item.get("uri")
        }
        return LocalizationRepository(self._db).coverage(
            subject_kind="concept", language=lang, subjects=subjects, field=field
        )

    def search_concepts_localized(
        self, query: str, *, limit: int = 50, lang: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Translation-aware concept search.

        Queries canonical text AND approved/current translations for ``lang``; every result
        carries the canonical label plus localized display fields, ``matched_language`` and
        ``matched_field`` (so users can always verify the regulatory object). Falls back to the
        plain canonical search when no language is requested.
        """
        results = self.search_concepts(query, limit=limit)
        if not lang or self._db is None:
            return results

        q = (query or "").lower()
        for row in results:
            label = (row.get("label") or "").lower()
            description = (row.get("description") or "").lower()
            row["matched_language"] = DEFAULT_LANGUAGE
            row["matched_field"] = (
                "label"
                if q and q in label
                else ("description" if q and q in description else "uri")
            )

        from src.database.models import LocalizedText

        chain = language_fallback_chain(lang)
        seen = {row.get("uri") for row in results}
        pattern = f"%{query.strip()}%"
        translation_rows = (
            self._db.query(LocalizedText)
            .filter(
                LocalizedText.subject_kind == "concept",
                LocalizedText.status == "approved",
                LocalizedText.effective_to.is_(None),
                LocalizedText.tenant_id.is_(None),
                LocalizedText.language.in_(chain),
                LocalizedText.text.ilike(pattern),
            )
            .all()
        )
        for trow in translation_rows:
            uri = trow.subject_uri
            existing = next((row for row in results if row.get("uri") == uri), None)
            if existing is not None:
                payload = existing
            else:
                concept = None
                for candidate in concept_uri_candidates(uri):
                    concept = self._repo.get_public_by_uri_with_children(candidate)
                    if concept is not None:
                        break
                if concept is None:
                    continue
                payload = self._concept_to_dict(concept)

            # A translation only counts as a search match when it is still CURRENT against the
            # live source text. An approved row whose source_hash no longer matches is stale and
            # MUST NOT surface or relabel a result (the design requires search to use only
            # approved/current translations, exactly like display resolution).
            if trow.field == "label":
                source_value = payload.get("label")
            elif trow.field == "description":
                source_value = payload.get("description")
            else:
                continue
            if text_hash(source_value or "") != (trow.source_hash or "").lower():
                continue

            payload["matched_language"] = trow.language
            payload["matched_field"] = trow.field
            if existing is None:
                results.append(payload)
                seen.add(uri)

        self.localize_concept_payloads(results, lang)
        return results[:limit]

    def localize_concept_payloads(
        self, payloads: List[Dict[str, Any]], lang: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Add display_label/display_description/localization to concept dicts for ``lang``.

        Additive (V1): canonical ``label``/``description`` are never replaced. No-op when no
        language is requested or no DB session is available, keeping the default path
        byte-for-byte unchanged.
        """
        if not lang or self._db is None or not payloads:
            return payloads
        repo = LocalizationRepository(self._db)
        for payload in payloads:
            uri = payload.get("uri")
            source_fields: Dict[str, str] = {}
            if payload.get("label"):
                source_fields["label"] = payload["label"]
            if payload.get("description"):
                source_fields["description"] = payload["description"]
            if not uri or not source_fields:
                continue
            result = repo.localize(
                subject_kind="concept",
                subject_uri=uri,
                source_fields=source_fields,
                requested_language=lang,
            )
            payload["display_label"] = result.display.get("label", payload.get("label"))
            payload["display_description"] = result.display.get(
                "description", payload.get("description")
            )
            payload["localization"] = result.as_metadata()
        return payloads

    @staticmethod
    def _contract_payload(contract, *, public_only: bool) -> Dict[str, Any]:
        """Project an active calculation contract into concept-detail fields.

        Returns an empty dict when no contract applies, so concepts without a
        (public) contract serialize exactly as before. The public path never
        exposes ``runtime_expression`` — only the ``semantic_expression`` is used
        as a formula fallback — and dimensions are ordered by ``dimension_id`` for
        determinism. ``_formula_fallback`` / ``_vars_fallback`` are private hints
        applied only when the projection has no value of its own.
        """
        if contract is None:
            return {}
        dims = sorted(
            getattr(contract, "dimensions", None) or [],
            key=lambda d: getattr(d, "dimension_id", "") or "",
        )
        dimensions = [
            {
                "dimension_id": dim.dimension_id,
                "mode": dim.mode,
                "fixed_value": dim.fixed_value,
                "member_values": dim.member_values,
                "required": bool(dim.required),
            }
            for dim in dims
        ]
        seen: set[str] = set()
        vars_fallback: List[str] = []
        components = sorted(
            getattr(contract, "components", None) or [],
            key=lambda c: (
                getattr(c, "component_order", 0) or 0,
                getattr(c, "id", 0) or 0,
            ),
        )
        for comp in components:
            variable_uri = getattr(comp, "variable_uri", None)
            if variable_uri and variable_uri not in seen:
                seen.add(variable_uri)
                vars_fallback.append(DEFAULT_NAMESPACES.compact(variable_uri))
        formula_fallback = contract.semantic_expression
        if not public_only:
            formula_fallback = formula_fallback or contract.runtime_expression
        return {
            "formula_kind": contract.formula_kind,
            "dimensions": dimensions,
            "aggregation": contract.aggregation_policy,
            "calculation_contract_id": contract.contract_hash,
            "calculation_contract_version": contract.contract_version,
            "_formula_fallback": formula_fallback,
            "_vars_fallback": vars_fallback,
        }

    @staticmethod
    def _formula_from_concept(concept) -> Optional[str]:
        formulas = [
            formula
            for formula in getattr(concept, "formulas", [])
            if getattr(formula, "is_active", False)
        ]
        if not formulas:
            return None
        formulas.sort(
            key=lambda item: (getattr(item, "version", 0), getattr(item, "id", 0)),
            reverse=True,
        )
        return formulas[0].expression

    @staticmethod
    def _variable_to_dict(variable, concept) -> Dict[str, Any]:
        return {
            "uri": variable.variable_uri,
            "taxonomy": concept.taxonomy,
            "name": variable.variable_label or _local_name(variable.variable_uri),
            "description": variable.variable_label or concept.description or "",
            "unit": concept.unit or "",
            "data_type": "decimal",
        }

    @staticmethod
    def _similarity_score(
        query: str, label: str, uri: str, description: Optional[str]
    ) -> float:
        normalized_query = (query or "").strip().lower()
        if not normalized_query:
            return 0.0

        candidates = [label or "", uri or "", description or ""]
        normalized_candidates = [
            candidate.lower() for candidate in candidates if candidate
        ]

        if normalized_query in normalized_candidates:
            return 1.0

        best = 0.0
        for candidate in normalized_candidates:
            if candidate.startswith(normalized_query) or normalized_query.startswith(
                candidate
            ):
                best = max(best, 0.98)
            best = max(best, SequenceMatcher(None, normalized_query, candidate).ratio())
        return round(best, 4)
