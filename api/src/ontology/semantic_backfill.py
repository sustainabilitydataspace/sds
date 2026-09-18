"""Ontology extraction helpers for Wave 1 semantic backfill."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF

from src.calculation.semantic_formula import (
    build_sum_function_expression,
    compact_reference,
    normalize_temporal_granularity,
)
from src.database.models import ConceptState
from src.ontology.curie import DEFAULT_NAMESPACES, taxonomy_from_curie

SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
SDS = Namespace("https://sustainabilitydataspace.com/ontology#")

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class OntologyVariableRecord:
    """Dependency variable extracted from the frozen ontology."""

    uri: str
    label: Optional[str]
    ordering: int
    aggregation_method: str = "SUM"
    temporal_granularity: Optional[str] = None


@dataclass(frozen=True)
class OntologyFormulaRecord:
    """Formula record extracted or materialized from the frozen ontology."""

    expression: str
    expression_language: str
    is_derived: bool = False


@dataclass(frozen=True)
class OntologyEquivalenceRecord:
    """Equivalence relationship extracted from the frozen ontology."""

    target_uri: str
    target_taxonomy: str
    relationship_type: str


@dataclass(frozen=True)
class OntologyConceptRecord:
    """Semantic concept extracted from the frozen ontology."""

    uri: str
    curie: str
    label: str
    description: Optional[str]
    taxonomy: str
    concept_type: str
    unit: Optional[str]
    temporal_granularity: Optional[str]
    concept_state: str
    variables: tuple[OntologyVariableRecord, ...] = field(default_factory=tuple)
    formula: Optional[OntologyFormulaRecord] = None
    equivalences: tuple[OntologyEquivalenceRecord, ...] = field(default_factory=tuple)


def ontology_sha256(path: Path) -> str:
    """Return the SHA-256 hash for the frozen ontology file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_indicator_code(value: str | None) -> Optional[str]:
    """Normalize indicator codes for deterministic exact matching."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _NON_ALNUM_RE.sub("", text).upper() or None


def normalize_concept_code(curie_or_iri: str) -> Optional[str]:
    """Normalize a concept CURIE/IRI to the matching indicator code token."""
    compacted = compact_reference(curie_or_iri)
    if not compacted:
        return None
    local = compacted.split(":", 1)[1] if ":" in compacted else compacted
    return normalize_indicator_code(local)


def _preferred_literal(
    graph: Graph, subject: URIRef, predicate: URIRef
) -> Optional[str]:
    literals = [
        value
        for value in graph.objects(subject, predicate)
        if isinstance(value, Literal)
    ]
    if not literals:
        return None

    for candidate in literals:
        if candidate.language == "en":
            return str(candidate)

    for candidate in literals:
        if candidate.language in (None, ""):
            return str(candidate)

    return str(literals[0])


def _concept_type_for_subject(graph: Graph, subject: URIRef) -> Optional[str]:
    if (subject, RDF.type, SDS.Disclosure) in graph:
        return "Disclosure"
    if (subject, RDF.type, SDS.Variable) in graph:
        return "Variable"
    return None


def _extract_variables(
    graph: Graph, subject: URIRef
) -> tuple[OntologyVariableRecord, ...]:
    variables: list[OntologyVariableRecord] = []
    for ordering, variable_obj in enumerate(graph.objects(subject, SDS.hasVariable)):
        variable_ref = URIRef(str(variable_obj))
        variables.append(
            OntologyVariableRecord(
                uri=compact_reference(str(variable_ref)) or str(variable_ref),
                label=_preferred_literal(graph, variable_ref, SKOS.prefLabel),
                ordering=ordering,
                temporal_granularity=normalize_temporal_granularity(
                    next(
                        (
                            str(obj)
                            for obj in graph.objects(
                                variable_ref, SDS.hasTemporalGranularity
                            )
                        ),
                        None,
                    )
                ),
            )
        )
    return tuple(variables)


def _extract_formula(
    graph: Graph, subject: URIRef, variables: Iterable[OntologyVariableRecord]
) -> Optional[OntologyFormulaRecord]:
    for formula_obj in graph.objects(subject, SDS.hasFormula):
        formula_ref = URIRef(str(formula_obj))
        for expr_obj in graph.objects(formula_ref, SDS.calculationExpression):
            expression = str(expr_obj).strip()
            if expression:
                return OntologyFormulaRecord(
                    expression=expression, expression_language="sds", is_derived=False
                )

    variable_uris = [variable.uri for variable in variables]
    derived = build_sum_function_expression(variable_uris)
    if derived:
        return OntologyFormulaRecord(
            expression=derived, expression_language="sds_derived", is_derived=True
        )

    return None


def _extract_equivalences(
    graph: Graph, subject: URIRef
) -> tuple[OntologyEquivalenceRecord, ...]:
    relations: list[OntologyEquivalenceRecord] = []
    predicate_map = {
        OWL.equivalentClass: "equivalent",
        OWL.sameAs: "same_as",
        SKOS.exactMatch: "exact_match",
        SKOS.closeMatch: "close_match",
    }

    for predicate, relationship_type in predicate_map.items():
        for target_obj in graph.objects(subject, predicate):
            target_uri = compact_reference(str(target_obj)) or str(target_obj)
            relations.append(
                OntologyEquivalenceRecord(
                    target_uri=target_uri,
                    target_taxonomy=taxonomy_from_curie(target_uri),
                    relationship_type=relationship_type,
                )
            )

    seen: set[tuple[str, str]] = set()
    unique_relations: list[OntologyEquivalenceRecord] = []
    for relation in relations:
        key = (relation.relationship_type, relation.target_uri)
        if key in seen:
            continue
        seen.add(key)
        unique_relations.append(relation)

    return tuple(unique_relations)


def extract_semantic_records(graph: Graph) -> Dict[str, OntologyConceptRecord]:
    """Extract the ontology-backed semantic subset into canonical records."""
    concepts: Dict[str, OntologyConceptRecord] = {}

    for subject in sorted(
        set(graph.subjects(RDF.type, SDS.Disclosure))
        | set(graph.subjects(RDF.type, SDS.Variable)),
        key=str,
    ):
        subject_ref = URIRef(str(subject))
        concept_type = _concept_type_for_subject(graph, subject_ref)
        if concept_type is None:
            continue

        curie = compact_reference(str(subject_ref)) or str(subject_ref)
        variables = _extract_variables(graph, subject_ref)
        formula = _extract_formula(graph, subject_ref, variables)
        unit = compact_reference(
            next((str(obj) for obj in graph.objects(subject_ref, SDS.hasUnit)), None)
        )
        temporal = normalize_temporal_granularity(
            next(
                (
                    str(obj)
                    for obj in graph.objects(subject_ref, SDS.hasTemporalGranularity)
                ),
                None,
            )
        )

        concept_state = (
            ConceptState.CALCULABLE.value
            if concept_type == "Disclosure" and variables
            else ConceptState.SEMANTICALLY_MODELLED.value
        )

        concepts[curie] = OntologyConceptRecord(
            uri=str(subject_ref),
            curie=curie,
            label=_preferred_literal(graph, subject_ref, SKOS.prefLabel) or curie,
            description=_preferred_literal(graph, subject_ref, SKOS.definition),
            taxonomy=taxonomy_from_curie(curie),
            concept_type=concept_type,
            unit=unit,
            temporal_granularity=temporal,
            concept_state=concept_state,
            variables=variables,
            formula=formula,
            equivalences=_extract_equivalences(graph, subject_ref),
        )

    return concepts


def load_semantic_records(
    ontology_path: Path | None = None,
) -> tuple[Path, Graph, Dict[str, OntologyConceptRecord]]:
    """Load the frozen ontology graph and extract semantic records."""
    from src.ontology.local_graph import (  # local import to avoid exporting private helper
        _load_graph,
    )

    if ontology_path is None:
        path = (
            Path(__file__).resolve().parents[2]
            / "ontologies"
            / "generated_projection.owl"
        )
        graph = _load_graph(path)
    else:
        path = ontology_path
        graph = _load_graph(path)

    return path, graph, extract_semantic_records(graph)
