"""Generate split ontology artifacts from the canonical semantic DB model."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from src.calculation.db_semantic_snapshot import DBSemanticConcept
from src.ontology.curie import DEFAULT_NAMESPACES
from src.ontology.local_graph import _load_graph

SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
SDS = Namespace("https://sustainabilitydataspace.com/ontology#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")


def _bind_namespaces(graph: Graph) -> None:
    graph.bind("sds", SDS)
    graph.bind("csrd", Namespace(DEFAULT_NAMESPACES.prefixes["csrd"]))
    graph.bind("gri", Namespace(DEFAULT_NAMESPACES.prefixes["gri"]))
    graph.bind("ghg", Namespace(DEFAULT_NAMESPACES.prefixes["ghg"]))
    graph.bind("syg", Namespace(DEFAULT_NAMESPACES.prefixes["syg"]))
    graph.bind("skos", SKOS)
    graph.bind("owl", OWL)
    graph.bind("rdf", RDF)
    graph.bind("rdfs", RDFS)
    graph.bind("xsd", XSD)


def _dynamic_subjects(base_graph: Graph) -> set[URIRef]:
    subjects: set[URIRef] = set()
    for class_ref in (
        SDS.Indicator,
        SDS.Disclosure,
        SDS.Variable,
        SDS.CalculationFormula,
    ):
        subjects.update(
            subject
            for subject in base_graph.subjects(RDF.type, class_ref)
            if isinstance(subject, URIRef)
        )

    for subject in list(base_graph.subjects()):
        if not isinstance(subject, URIRef):
            continue
        uri = str(subject)
        if (
            uri.startswith(DEFAULT_NAMESPACES.prefixes["csrd"])
            or uri.startswith(DEFAULT_NAMESPACES.prefixes["gri"])
            or uri.startswith(DEFAULT_NAMESPACES.prefixes["syg"])
        ):
            subjects.add(subject)

    return subjects


def build_core_tbox_graph(base_graph: Graph) -> Graph:
    """Build the stable core ontology by removing dynamic concept ABox triples."""
    graph = Graph()
    _bind_namespaces(graph)

    dynamic_subjects = _dynamic_subjects(base_graph)
    for subject, predicate, obj in base_graph:
        if subject in dynamic_subjects:
            continue
        if isinstance(obj, URIRef) and obj in dynamic_subjects:
            continue
        graph.add((subject, predicate, obj))

    return graph


def _formula_uri(concept_uri: str) -> URIRef:
    compacted = DEFAULT_NAMESPACES.compact(concept_uri)
    safe_local = (
        compacted.replace(":", "_")
        .replace("/", "_")
        .replace("#", "_")
        .replace(".", "_")
        .replace("-", "_")
    )
    return URIRef(DEFAULT_NAMESPACES.expand(f"sds:Formula_{safe_local}"))


def _expand(value: str | None) -> URIRef | None:
    if not value:
        return None
    expanded = DEFAULT_NAMESPACES.expand(value)
    return URIRef(expanded)


def build_generated_projection_graph(snapshots: Dict[str, DBSemanticConcept]) -> Graph:
    """Generate the ontology ABox projection from DB-backed semantic concepts."""
    graph = Graph()
    _bind_namespaces(graph)

    ontology_uri = URIRef(
        "https://sustainabilitydataspace.com/ontology/generated-projection"
    )
    graph.add((ontology_uri, RDF.type, OWL.Ontology))
    graph.add(
        (
            ontology_uri,
            RDFS.label,
            Literal("SustainabilityDataSpace generated projection", lang="en"),
        )
    )
    graph.add((ontology_uri, OWL.versionInfo, Literal("wave2-generated")))

    predicate_map = {
        "equivalent": OWL.equivalentClass,
        "same_as": OWL.sameAs,
        "exact_match": SKOS.exactMatch,
        "close_match": SKOS.closeMatch,
    }

    concept_class_map = {
        "Disclosure": SDS.Disclosure,
        "Indicator": SDS.Indicator,
        "Unit": SDS.Unit,
        "Variable": SDS.Variable,
    }

    for snapshot in snapshots.values():
        subject = _expand(snapshot.uri)
        if subject is None:
            continue
        graph.add((subject, RDF.type, OWL.NamedIndividual))
        graph.add(
            (
                subject,
                RDF.type,
                concept_class_map.get(snapshot.concept_type, SDS.Variable),
            )
        )
        graph.add((subject, SKOS.prefLabel, Literal(snapshot.label, lang="en")))

        if snapshot.description:
            graph.add(
                (subject, SKOS.definition, Literal(snapshot.description, lang="en"))
            )

        unit_ref = _expand(snapshot.unit)
        if unit_ref is not None:
            graph.add((subject, SDS.hasUnit, unit_ref))

        temporal_ref = (
            _expand(f"sds:{snapshot.temporal_granularity.capitalize()}")
            if snapshot.temporal_granularity
            else None
        )
        if temporal_ref is not None:
            graph.add((subject, SDS.hasTemporalGranularity, temporal_ref))

        for variable in snapshot.variables:
            variable_ref = _expand(variable.uri)
            if variable_ref is not None:
                graph.add((subject, SDS.hasVariable, variable_ref))

        if snapshot.formula_expression:
            formula_ref = _formula_uri(snapshot.uri)
            graph.add((formula_ref, RDF.type, OWL.NamedIndividual))
            graph.add((formula_ref, RDF.type, SDS.CalculationFormula))
            graph.add(
                (
                    formula_ref,
                    SDS.calculationExpression,
                    Literal(snapshot.formula_expression),
                )
            )
            graph.add((subject, SDS.hasFormula, formula_ref))

        for equivalence in snapshot.equivalences:
            predicate = predicate_map.get(equivalence.relationship_type)
            target_ref = _expand(equivalence.target_uri)
            if predicate is not None and target_ref is not None:
                graph.add((subject, predicate, target_ref))

    return graph


def write_graph(graph: Graph, path: Path) -> None:
    """Serialize a graph as RDF/XML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = graph.serialize(format="xml")
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def load_base_graph(path: Path) -> Graph:
    """Load the frozen base ontology as the source for the split."""
    return _load_graph(path)
