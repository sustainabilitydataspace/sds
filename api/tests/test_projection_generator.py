from __future__ import annotations

from rdflib import BNode, Literal, URIRef
from rdflib.namespace import OWL, RDF

from src.calculation.db_semantic_snapshot import (
    DBSemanticConcept,
    DBSemanticEquivalence,
    DBSemanticVariable,
)
from src.ontology.curie import DEFAULT_NAMESPACES
from src.ontology.projection_generator import (
    SDS,
    _expand,
    _formula_uri,
    build_core_tbox_graph,
    build_generated_projection_graph,
    load_base_graph,
    write_graph,
)
from src.ontology.semantic_backfill import load_semantic_records


def _build_db_snapshots(records):
    snapshots = {}
    for record in records.values():
        snapshots[record.uri] = DBSemanticConcept(
            uri=record.uri,
            label=record.label,
            description=record.description,
            taxonomy=record.taxonomy,
            concept_type=record.concept_type,
            unit=record.unit,
            temporal_granularity=record.temporal_granularity,
            concept_state=record.concept_state,
            formula_expression=record.formula.expression if record.formula else None,
            formula_language=(
                record.formula.expression_language if record.formula else None
            ),
            variables=tuple(
                DBSemanticVariable(
                    uri=variable.uri,
                    label=variable.label,
                    ordering=variable.ordering,
                    aggregation_method=variable.aggregation_method,
                    temporal_granularity=variable.temporal_granularity,
                )
                for variable in record.variables
            ),
            equivalences=tuple(
                DBSemanticEquivalence(
                    target_uri=equivalence.target_uri,
                    target_taxonomy=equivalence.target_taxonomy,
                    relationship_type=equivalence.relationship_type,
                )
                for equivalence in record.equivalences
            ),
        )
    return snapshots


def test_build_core_tbox_graph_removes_dynamic_conceptabox_but_keeps_tbox():
    _path, base_graph, _records = load_semantic_records()

    core_graph = build_core_tbox_graph(base_graph)

    e3_5 = URIRef(DEFAULT_NAMESPACES.expand("csrd:E3_5"))
    disclosure_class = URIRef(DEFAULT_NAMESPACES.expand("sds:Disclosure"))

    assert not any(core_graph.triples((e3_5, None, None)))
    assert (disclosure_class, RDF.type, OWL.Class) in core_graph
    assert any(core_graph.triples((disclosure_class, None, None)))


def test_core_tbox_skips_dynamic_objects_and_non_uri_subjects():
    base_graph = URIRef(DEFAULT_NAMESPACES.expand("sds:StaticClass"))
    dynamic = URIRef(DEFAULT_NAMESPACES.expand("csrd:E1_6"))
    projected_indicator = URIRef("urn:sds:reg:esrs:e5_5_01")
    graph = __import__("rdflib").Graph()
    graph.add((base_graph, RDF.type, OWL.Class))
    graph.add((base_graph, SDS.hasVariable, dynamic))
    graph.add((base_graph, SDS.hasVariable, projected_indicator))
    graph.add((dynamic, RDF.type, SDS.Disclosure))
    graph.add((projected_indicator, RDF.type, SDS.Indicator))
    graph.add((BNode(), SDS.hasVariable, Literal("ignored")))

    core = build_core_tbox_graph(graph)

    assert (base_graph, RDF.type, OWL.Class) in core
    assert not any(core.triples((base_graph, SDS.hasVariable, None)))
    assert not any(core.triples((projected_indicator, None, None)))
    assert _expand(None) is None


def test_build_generated_projection_graph_emits_formula_variables_and_equivalence():
    _path, _graph, records = load_semantic_records()
    snapshots = _build_db_snapshots(records)

    projection_graph = build_generated_projection_graph(snapshots)

    subject = URIRef(DEFAULT_NAMESPACES.expand("csrd:E3_5"))
    variable = URIRef("urn:sds:reg:esrs:e3_5_01")
    formula_ref = _formula_uri(DEFAULT_NAMESPACES.expand("csrd:E3_5"))
    equivalent = URIRef(DEFAULT_NAMESPACES.expand("gri:303_3"))
    formula_predicate = URIRef(DEFAULT_NAMESPACES.expand("sds:calculationExpression"))
    has_formula = URIRef(DEFAULT_NAMESPACES.expand("sds:hasFormula"))
    has_variable = URIRef(DEFAULT_NAMESPACES.expand("sds:hasVariable"))
    disclosure_class = URIRef(DEFAULT_NAMESPACES.expand("sds:Disclosure"))
    formula_class = URIRef(DEFAULT_NAMESPACES.expand("sds:CalculationFormula"))

    assert (subject, RDF.type, disclosure_class) in projection_graph
    assert (subject, has_variable, variable) in projection_graph
    assert (subject, has_formula, formula_ref) in projection_graph
    assert (formula_ref, RDF.type, formula_class) in projection_graph
    assert any(projection_graph.triples((formula_ref, formula_predicate, None)))
    assert (subject, OWL.equivalentClass, equivalent) in projection_graph


def test_build_generated_projection_graph_expands_compact_snapshot_uris():
    snapshots = {
        "csrd:E3_5": DBSemanticConcept(
            uri="csrd:E3_5",
            label="Water consumption",
            description=None,
            taxonomy="CSRD",
            concept_type="Disclosure",
            unit="sds:CubicMeter",
            temporal_granularity="annual",
            concept_state="active",
            formula_expression="SUM(syg:Water_Cooling)",
            formula_language="SDS-FORMULA",
            variables=(
                DBSemanticVariable(
                    uri="syg:Water_Cooling",
                    label="Water cooling",
                    ordering=1,
                    aggregation_method="sum",
                    temporal_granularity="annual",
                ),
            ),
            equivalences=(
                DBSemanticEquivalence(
                    target_uri="gri:303_3",
                    target_taxonomy="GRI",
                    relationship_type="equivalent",
                ),
            ),
        )
    }

    projection_graph = build_generated_projection_graph(snapshots)

    subject = URIRef(DEFAULT_NAMESPACES.expand("csrd:E3_5"))
    compact_subject = URIRef("csrd:E3_5")
    variable = URIRef(DEFAULT_NAMESPACES.expand("syg:Water_Cooling"))
    compact_variable = URIRef("syg:Water_Cooling")
    target = URIRef(DEFAULT_NAMESPACES.expand("gri:303_3"))

    assert (
        subject,
        RDF.type,
        URIRef(DEFAULT_NAMESPACES.expand("sds:Disclosure")),
    ) in projection_graph
    assert (
        subject,
        URIRef(DEFAULT_NAMESPACES.expand("sds:hasVariable")),
        variable,
    ) in projection_graph
    assert (subject, OWL.equivalentClass, target) in projection_graph
    assert not any(projection_graph.triples((compact_subject, None, None)))
    assert not any(projection_graph.triples((None, None, compact_variable)))


def test_build_generated_projection_graph_preserves_indicator_type():
    snapshots = {
        "urn:sds:reg:esrs:e1_6_01": DBSemanticConcept(
            uri="urn:sds:reg:esrs:e1_6_01",
            label="Gross Scope 1 greenhouse gas emissions",
            description=None,
            taxonomy="CSRD",
            concept_type="Indicator",
            unit="tCO2e",
            temporal_granularity="annual",
            concept_state="active",
            formula_expression=None,
            formula_language=None,
        )
    }

    projection_graph = build_generated_projection_graph(snapshots)

    subject = URIRef("urn:sds:reg:esrs:e1_6_01")
    assert (
        subject,
        RDF.type,
        URIRef(DEFAULT_NAMESPACES.expand("sds:Indicator")),
    ) in projection_graph
    assert (
        not (
            subject,
            RDF.type,
            URIRef(DEFAULT_NAMESPACES.expand("sds:Variable")),
        )
        in projection_graph
    )


def test_projection_skips_unexpandable_subject_and_writes_roundtrippable_graph(
    tmp_path,
):
    snapshots = {
        "missing": DBSemanticConcept(
            uri=None,
            label="Missing",
            description=None,
            taxonomy="SDS",
            concept_type="Variable",
            unit=None,
            temporal_granularity=None,
            concept_state="active",
            formula_expression=None,
            formula_language=None,
        ),
        "syg:Variable": DBSemanticConcept(
            uri="syg:Variable",
            label="Variable",
            description="Description",
            taxonomy="SDS",
            concept_type="Variable",
            unit=None,
            temporal_granularity=None,
            concept_state="active",
            formula_expression=None,
            formula_language=None,
            equivalences=(
                DBSemanticEquivalence(
                    target_uri=None,
                    target_taxonomy="SDS",
                    relationship_type="unsupported",
                ),
            ),
        ),
    }

    graph = build_generated_projection_graph(snapshots)
    path = tmp_path / "projection.owl"
    write_graph(graph, path)
    loaded = load_base_graph(path)

    assert path.exists()
    assert len(loaded) == len(graph)
