"""Read-only DB semantic snapshot helpers for Wave 1 parity verification."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, Optional

from sqlalchemy.orm import Session, joinedload

from src.calculation.engine import CalculationEngine
from src.calculation.semantic_formula import (
    build_sum_function_expression,
    canonical_formula_for_compare,
    display_unit_from_reference,
    normalize_formula_expression,
)
from src.database.models import (
    Concept,
    ConceptEquivalence,
    ConceptFormula,
    ConceptVariable,
)


@dataclass(frozen=True)
class DBSemanticVariable:
    """DB-backed dependency variable row."""

    uri: str
    label: Optional[str]
    ordering: int
    aggregation_method: str
    temporal_granularity: Optional[str]


@dataclass(frozen=True)
class DBSemanticEquivalence:
    """DB-backed equivalence row."""

    target_uri: str
    target_taxonomy: str
    relationship_type: str


@dataclass(frozen=True)
class DBSemanticConcept:
    """DB-backed semantic concept snapshot."""

    uri: str
    label: str
    description: Optional[str]
    taxonomy: str
    concept_type: str
    unit: Optional[str]
    temporal_granularity: Optional[str]
    concept_state: str
    formula_expression: Optional[str]
    formula_language: Optional[str]
    variables: tuple[DBSemanticVariable, ...] = field(default_factory=tuple)
    equivalences: tuple[DBSemanticEquivalence, ...] = field(default_factory=tuple)

    @property
    def comparable_formula(self) -> Optional[str]:
        return canonical_formula_for_compare(self.formula_expression)

    @property
    def display_unit(self) -> Optional[str]:
        return display_unit_from_reference(self.unit)


def _active_formula(concept: Concept) -> Optional[ConceptFormula]:
    active = [formula for formula in concept.formulas if formula.is_active]
    if not active:
        return None
    return sorted(
        active, key=lambda formula: (formula.version, formula.id), reverse=True
    )[0]


def _snapshot_from_concept(concept: Concept) -> DBSemanticConcept:
    formula = _active_formula(concept)
    variables = tuple(
        DBSemanticVariable(
            uri=variable.variable_uri,
            label=variable.variable_label,
            ordering=variable.ordering,
            aggregation_method=variable.aggregation_method,
            temporal_granularity=variable.temporal_granularity,
        )
        for variable in sorted(
            concept.variables, key=lambda item: (item.ordering, item.id)
        )
    )
    equivalences = tuple(
        DBSemanticEquivalence(
            target_uri=equivalence.target_uri,
            target_taxonomy=equivalence.target_taxonomy,
            relationship_type=equivalence.relationship_type,
        )
        for equivalence in sorted(
            concept.equivalences,
            key=lambda item: (item.relationship_type, item.target_uri, item.id),
        )
    )

    return DBSemanticConcept(
        uri=concept.uri,
        label=concept.label,
        description=concept.description,
        taxonomy=concept.taxonomy,
        concept_type=concept.concept_type,
        unit=concept.unit,
        temporal_granularity=concept.temporal_granularity,
        concept_state=concept.concept_state,
        formula_expression=formula.expression if formula else None,
        formula_language=formula.expression_language if formula else None,
        variables=variables,
        equivalences=equivalences,
    )


def load_db_semantic_snapshots(
    session: Session,
    *,
    uris: Optional[Iterable[str]] = None,
    public_catalog: bool = False,
) -> Dict[str, DBSemanticConcept]:
    """Load DB-backed semantic snapshots keyed by concept URI.

    When ``public_catalog`` is set, only public-catalog concepts are loaded (same
    predicate as the public JSON concept list/detail), so the generated RDF
    projection never publishes internal/future concepts or their formulas
    (codex F09 M1).
    """
    query = (
        session.query(Concept)
        .options(
            joinedload(Concept.formulas),
            joinedload(Concept.variables),
            joinedload(Concept.equivalences),
        )
        .order_by(Concept.uri.asc())
    )

    if public_catalog:
        from src.concept_catalog_policy import public_catalog_concept_conditions

        query = query.filter(*public_catalog_concept_conditions(Concept))

    if uris is not None:
        uri_list = list(uris)
        if not uri_list:
            return {}
        query = query.filter(Concept.uri.in_(uri_list))

    return {concept.uri: _snapshot_from_concept(concept) for concept in query.all()}


def build_seeded_variable_values(variable_uris: Iterable[str]) -> Dict[str, Decimal]:
    """Return deterministic synthetic values keyed by engine variable name."""
    values: Dict[str, Decimal] = {}
    for index, variable_uri in enumerate(variable_uris, start=1):
        local = variable_uri.split(":", 1)[1] if ":" in variable_uri else variable_uri
        values[local] = Decimal(str(index))
    return values


def evaluate_db_semantic_formula(
    snapshot: DBSemanticConcept, variable_values: Dict[str, Decimal]
) -> tuple[Decimal, Optional[str], Optional[str]]:
    """Evaluate a DB-backed concept formula using the current safe engine evaluator."""
    engine = CalculationEngine()
    formula = normalize_formula_expression(snapshot.formula_expression)
    if not formula and snapshot.variables:
        formula = normalize_formula_expression(
            build_sum_function_expression(
                variable.uri for variable in snapshot.variables
            )
        )
    if not formula:
        raise ValueError(f"No evaluable formula available for {snapshot.uri}")

    result = engine._evaluate_formula(formula, variable_values)
    if not isinstance(result, Decimal):
        result = Decimal(str(result))

    return result, snapshot.display_unit, formula
