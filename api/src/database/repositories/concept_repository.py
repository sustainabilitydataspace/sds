"""Repository for canonical semantic concept data."""

from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import false, func, or_
from sqlalchemy.orm import Session, joinedload

from src.concept_catalog_policy import (
    CATALOG_PROJECTION_SOURCES,
    FUTURE_CATALOG_TAXONOMIES,
    PUBLIC_CATALOG_TAXONOMIES,
    PUBLIC_SOURCE_TAXONOMIES,
    UNIFIED_SYGRIS_TAXONOMY,
)

from ..models import (
    CanonicalCalculationContract,
    CanonicalConcept,
    Concept,
    ConceptEquivalence,
    ConceptFormula,
    ConceptIndicatorLink,
    ConceptVariable,
    MappingAssertionComponent,
    MappingAssertionGroup,
    StandardDatapoint,
    StandardRelease,
)

#: Exposure value marking a calculation contract as fit for the public register.
PUBLIC_CONTRACT_EXPOSURE = "public_register"


class ConceptRepository:
    """Repository for DB-backed semantic concept operations."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _normalize_taxonomy(taxonomy: Optional[str]) -> Optional[str]:
        return taxonomy.strip().upper() if taxonomy and taxonomy.strip() else None

    @staticmethod
    def _normalize_concept_type(concept_type: Optional[str]) -> Optional[str]:
        if not concept_type or not concept_type.strip():
            return None
        normalized = concept_type.strip().lower()
        return normalized.capitalize()

    @staticmethod
    def _apply_filters(
        query,
        *,
        taxonomy: Optional[str] = None,
        concept_type: Optional[str] = None,
        concept_state: Optional[str] = None,
        search: Optional[str] = None,
        public_catalog: bool = False,
    ):
        normalized_taxonomy = ConceptRepository._normalize_taxonomy(taxonomy)
        normalized_concept_type = ConceptRepository._normalize_concept_type(
            concept_type
        )

        sygris_unified_public_view = (
            public_catalog and normalized_taxonomy == UNIFIED_SYGRIS_TAXONOMY
        )
        if normalized_taxonomy and not sygris_unified_public_view:
            query = query.filter(func.upper(Concept.taxonomy) == normalized_taxonomy)
        if normalized_concept_type:
            query = query.filter(
                func.upper(Concept.concept_type) == normalized_concept_type.upper()
            )
        if concept_state:
            query = query.filter(Concept.concept_state == concept_state)
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    Concept.uri.ilike(pattern),
                    Concept.label.ilike(pattern),
                    Concept.description.ilike(pattern),
                )
            )
        if public_catalog:
            public_membership = Concept.projection_source.in_(
                CATALOG_PROJECTION_SOURCES
            )
            not_future_catalog = func.upper(Concept.taxonomy).notin_(
                FUTURE_CATALOG_TAXONOMIES
            )
            if normalized_taxonomy in FUTURE_CATALOG_TAXONOMIES:
                query = query.filter(false())
            elif normalized_taxonomy == UNIFIED_SYGRIS_TAXONOMY:
                query = query.filter(public_membership, not_future_catalog)
            elif normalized_taxonomy in PUBLIC_SOURCE_TAXONOMIES:
                query = query.filter(public_membership)
            elif normalized_taxonomy is None:
                query = query.filter(
                    not_future_catalog,
                    or_(
                        func.upper(Concept.taxonomy).notin_(PUBLIC_CATALOG_TAXONOMIES),
                        public_membership,
                    ),
                )
        return query

    def get_by_uri(self, uri: str) -> Optional[Concept]:
        """Return a single concept by canonical URI."""
        return self.db.query(Concept).filter(Concept.uri == uri).first()

    def get_by_uri_with_children(self, uri: str) -> Optional[Concept]:
        """Return a concept with formulas, variables, and equivalences eager-loaded."""
        return (
            self.db.query(Concept)
            .options(
                joinedload(Concept.formulas),
                joinedload(Concept.variables),
                joinedload(Concept.equivalences),
            )
            .filter(Concept.uri == uri)
            .first()
        )

    def get_public_by_uri_with_children(self, uri: str) -> Optional[Concept]:
        """Return a public-catalog concept by URI with child objects loaded."""
        query = self.db.query(Concept).options(
            joinedload(Concept.formulas),
            joinedload(Concept.variables),
            joinedload(Concept.equivalences),
        )
        query = self._apply_filters(query, public_catalog=True)
        return query.filter(Concept.uri == uri).first()

    def get_active_calculation_contract(
        self, indicator_id: Optional[str], *, public_only: bool = False
    ) -> Optional[CanonicalCalculationContract]:
        """Return the single active calculation contract for an indicator, if any.

        At most one active contract exists per indicator (unique partial index
        ``ix_canonical_calc_contracts_active_indicator``), so this selection is
        deterministic. When ``public_only`` is set, only a publicly-registered
        contract (``exposure == "public_register"``) is returned so non-public
        rows (``runtime_support`` / ``container`` / ``audit_only``) never leak
        their semantics onto the public catalog.
        """
        if not indicator_id:
            return None
        query = (
            self.db.query(CanonicalCalculationContract)
            .options(
                joinedload(CanonicalCalculationContract.dimensions),
                joinedload(CanonicalCalculationContract.components),
            )
            .filter(
                CanonicalCalculationContract.indicator_id == indicator_id,
                CanonicalCalculationContract.is_active.is_(True),
            )
        )
        if public_only:
            query = query.filter(
                CanonicalCalculationContract.exposure == PUBLIC_CONTRACT_EXPOSURE
            )
        return query.first()

    def count_concepts(
        self,
        *,
        taxonomy: Optional[str] = None,
        concept_type: Optional[str] = None,
        concept_state: Optional[str] = None,
        search: Optional[str] = None,
        public_catalog: bool = False,
    ) -> int:
        """Count concepts with the same filters used by the public API."""
        query = self.db.query(Concept)
        query = self._apply_filters(
            query,
            taxonomy=taxonomy,
            concept_type=concept_type,
            concept_state=concept_state,
            search=search,
            public_catalog=public_catalog,
        )
        return query.count()

    def list_concepts(
        self,
        *,
        taxonomy: Optional[str] = None,
        concept_type: Optional[str] = None,
        concept_state: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        public_catalog: bool = False,
    ) -> List[Concept]:
        """List concepts with optional filters."""
        query = self.db.query(Concept).options(
            joinedload(Concept.formulas),
            joinedload(Concept.variables),
            joinedload(Concept.equivalences),
        )
        query = self._apply_filters(
            query,
            taxonomy=taxonomy,
            concept_type=concept_type,
            concept_state=concept_state,
            public_catalog=public_catalog,
        )
        return query.order_by(Concept.uri).offset(offset).limit(limit).all()

    def list_all_public_catalog_concepts(
        self, *, taxonomy: Optional[str] = None
    ) -> List[Concept]:
        """Return every concept visible in the public catalog, without a page cap."""
        query = self.db.query(Concept).options(
            joinedload(Concept.formulas),
            joinedload(Concept.variables),
            joinedload(Concept.equivalences),
        )
        query = self._apply_filters(
            query,
            taxonomy=taxonomy,
            public_catalog=True,
        )
        return query.order_by(Concept.uri).all()

    def search_concepts(
        self,
        *,
        query: Optional[str] = None,
        taxonomy: Optional[str] = None,
        concept_type: Optional[str] = None,
        concept_state: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        public_catalog: bool = False,
    ) -> List[Concept]:
        """Search concepts by text or URI prefix using canonical DB fields."""
        if not query or not query.strip():
            return self.list_concepts(
                taxonomy=taxonomy,
                concept_type=concept_type,
                concept_state=concept_state,
                limit=limit,
                offset=offset,
                public_catalog=public_catalog,
            )

        db_query = self.db.query(Concept).options(
            joinedload(Concept.formulas),
            joinedload(Concept.variables),
            joinedload(Concept.equivalences),
        )
        db_query = self._apply_filters(
            db_query,
            taxonomy=taxonomy,
            concept_type=concept_type,
            concept_state=concept_state,
            search=query,
            public_catalog=public_catalog,
        )
        return db_query.order_by(Concept.uri).offset(offset).limit(limit).all()

    def get_active_formula(self, concept_id: int) -> Optional[ConceptFormula]:
        """Return the active formula for a concept, if any."""
        return (
            self.db.query(ConceptFormula)
            .filter(
                ConceptFormula.concept_id == concept_id,
                ConceptFormula.is_active.is_(True),
            )
            .order_by(ConceptFormula.version.desc(), ConceptFormula.id.desc())
            .first()
        )

    def get_variables(self, concept_id: int) -> List[ConceptVariable]:
        """Return dependency variables for a concept ordered deterministically."""
        return (
            self.db.query(ConceptVariable)
            .filter(ConceptVariable.concept_id == concept_id)
            .order_by(ConceptVariable.ordering.asc(), ConceptVariable.id.asc())
            .all()
        )

    def list_variables(
        self,
        *,
        taxonomy: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Tuple[ConceptVariable, Concept]]:
        """Return operational variables joined with their parent concept."""
        query = self.db.query(ConceptVariable, Concept).join(
            Concept, Concept.id == ConceptVariable.concept_id
        )
        if taxonomy:
            query = query.filter(Concept.taxonomy == taxonomy)
        return (
            query.order_by(ConceptVariable.ordering.asc(), ConceptVariable.id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def get_equivalences(self, concept_id: int) -> List[ConceptEquivalence]:
        """Return explicit concept equivalences and related links."""
        return (
            self.db.query(ConceptEquivalence)
            .filter(ConceptEquivalence.source_concept_id == concept_id)
            .order_by(
                ConceptEquivalence.relationship_type.asc(),
                ConceptEquivalence.target_uri.asc(),
            )
            .all()
        )

    def list_equivalences(
        self,
        *,
        concept_uri: Optional[str] = None,
        compact_concept_uri: Optional[str] = None,
        source_taxonomy: Optional[str] = None,
        target_taxonomy: Optional[str] = None,
        public_catalog: bool = False,
    ) -> List[tuple[Concept, ConceptEquivalence]]:
        """Return equivalences with optional symmetric filtering for the public API.

        When ``public_catalog`` is set, the SOURCE concept is gated to the public
        catalog (projection-source membership, future-taxonomy fail-closed) using the
        same predicate as concept list/detail, so an internal/future/projection-NULL
        concept's equivalences cannot leak through the public ontology read path
        (codex F09 M2).
        """
        query = self.db.query(Concept, ConceptEquivalence).join(
            ConceptEquivalence, Concept.id == ConceptEquivalence.source_concept_id
        )

        normalized_source = self._normalize_taxonomy(source_taxonomy)
        normalized_target = self._normalize_taxonomy(target_taxonomy)

        if public_catalog:
            # apply the public-catalog membership + future fail-closed to the source
            query = self._apply_filters(
                query, taxonomy=source_taxonomy, public_catalog=True
            )
        elif normalized_source:
            query = query.filter(func.upper(Concept.taxonomy) == normalized_source)
        if normalized_target:
            query = query.filter(
                func.upper(ConceptEquivalence.target_taxonomy) == normalized_target
            )
        if concept_uri or compact_concept_uri:
            query = query.filter(
                or_(
                    Concept.uri == concept_uri,
                    ConceptEquivalence.target_uri
                    == (compact_concept_uri or concept_uri),
                )
            )

        return query.order_by(
            Concept.uri.asc(),
            ConceptEquivalence.relationship_type.asc(),
            ConceptEquivalence.target_uri.asc(),
        ).all()

    def list_assertion_equivalence_rows(
        self,
    ) -> List[
        tuple[
            MappingAssertionGroup,
            StandardRelease,
            StandardDatapoint,
            MappingAssertionComponent,
            CanonicalConcept,
        ]
    ]:
        """Return the public, current, approved external->Sygris assertion footprint.

        Gated to the schema's own current-approved notion plus full active/current
        revisioning so no draft/internal/superseded/retired row leaks onto the
        public equivalences surface (codex P2 M1): the assertion group must be
        current (valid_to NULL), approved, and not superseded; its source release
        and datapoint must be lifecycle-active; and the Sygris canonical target
        must be the current revision (effective_to NULL, not superseded). Taxonomy
        and concept filtering are applied in the service over the derived URIs.
        """
        return (
            self.db.query(
                MappingAssertionGroup,
                StandardRelease,
                StandardDatapoint,
                MappingAssertionComponent,
                CanonicalConcept,
            )
            .join(
                StandardDatapoint,
                MappingAssertionGroup.source_datapoint_id == StandardDatapoint.id,
            )
            .join(
                StandardRelease,
                StandardDatapoint.standard_release_id == StandardRelease.id,
            )
            .join(
                MappingAssertionComponent,
                MappingAssertionComponent.assertion_group_id
                == MappingAssertionGroup.id,
            )
            .join(
                CanonicalConcept,
                MappingAssertionComponent.canonical_concept_id == CanonicalConcept.id,
            )
            .filter(
                MappingAssertionGroup.valid_to.is_(None),
                MappingAssertionGroup.approval_status == "approved",
                MappingAssertionGroup.superseded_by.is_(None),
                StandardRelease.lifecycle_status == "active",
                StandardDatapoint.lifecycle_status == "active",
                CanonicalConcept.effective_to.is_(None),
                CanonicalConcept.superseded_by.is_(None),
            )
            .order_by(
                StandardRelease.standard_id.asc(),
                StandardDatapoint.code.asc(),
                MappingAssertionGroup.mapping_profile.asc(),
                MappingAssertionGroup.id.asc(),
                MappingAssertionComponent.component_order.asc(),
                CanonicalConcept.canonical_uri.asc(),
            )
            .all()
        )

    def get_indicator_links(self, concept_id: int) -> List[ConceptIndicatorLink]:
        """Return deterministic concept-to-indicator links for a concept."""
        return (
            self.db.query(ConceptIndicatorLink)
            .filter(ConceptIndicatorLink.concept_id == concept_id)
            .order_by(
                ConceptIndicatorLink.link_type.asc(), ConceptIndicatorLink.id.asc()
            )
            .all()
        )
