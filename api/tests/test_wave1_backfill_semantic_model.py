from __future__ import annotations

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF

from src.ontology.semantic_backfill import (
    SDS,
    SKOS,
    _concept_type_for_subject,
    _extract_equivalences,
    _preferred_literal,
    extract_semantic_records,
    load_semantic_records,
    normalize_concept_code,
    normalize_indicator_code,
)


def test_load_semantic_records_extracts_expected_calculable_concepts():
    ontology_path, _graph, records = load_semantic_records()

    assert ontology_path.name == "base.owl"
    assert "csrd:E3_5" in records
    assert "csrd:E1_6" in records
    assert "gri:303_3" in records
    assert "syg:Waste_Plastic" in records

    e35 = records["csrd:E3_5"]
    assert e35.concept_type == "Disclosure"
    assert e35.concept_state == "calculable"
    assert e35.formula is not None
    assert e35.formula.expression == "SUM(urn:sds:reg:esrs:e3_5_01)"
    assert [variable.uri for variable in e35.variables] == [
        "urn:sds:reg:esrs:e3_5_01",
    ]
    assert [
        (equivalence.relationship_type, equivalence.target_uri)
        for equivalence in e35.equivalences
    ] == [("equivalent", "gri:303_3")]


def test_load_semantic_records_materializes_implicit_formula_for_variable_only_disclosure():
    _ontology_path, _graph, records = load_semantic_records()

    gri303 = records["gri:303_3"]
    assert gri303.concept_type == "Disclosure"
    assert gri303.concept_state == "calculable"
    assert gri303.formula is not None
    assert gri303.formula.is_derived is True
    assert gri303.formula.expression_language == "sds_derived"
    assert (
        gri303.formula.expression == "SUM(urn:sds:reg:gri:gri_303_3_a_total_all_areas)"
    )
    assert [variable.uri for variable in gri303.variables] == [
        "urn:sds:reg:gri:gri_303_3_a_total_all_areas"
    ]

    waste = records["syg:Waste_Plastic"]
    assert waste.concept_type == "Variable"
    assert waste.concept_state == "semantically_modelled"
    assert waste.temporal_granularity == "monthly"
    assert waste.unit == "sds:Kilogram"


def test_normalized_code_matching_helpers_are_exact_and_deterministic():
    assert normalize_indicator_code("E3-5") == "E35"
    assert normalize_indicator_code("GRI 303-3.a") == "GRI3033A"
    assert normalize_indicator_code("   ") is None
    assert normalize_concept_code("csrd:E3_5") == "E35"
    assert (
        normalize_concept_code("https://data.globalreporting.org/gri#303_3") == "3033"
    )


def test_semantic_extraction_helpers_cover_literal_and_equivalence_edges(tmp_path):
    graph = Graph()
    subject = URIRef("https://sustainabilitydataspace.com/sygris#Concept")
    variable = URIRef("https://sustainabilitydataspace.com/sygris#Variable")
    target = URIRef("https://data.globalreporting.org/gri#303_3")

    assert _preferred_literal(graph, subject, SKOS.prefLabel) is None
    assert _concept_type_for_subject(graph, subject) is None

    graph.add((subject, RDF.type, SDS.Disclosure))
    graph.add((variable, RDF.type, SDS.Variable))
    graph.add((subject, SKOS.prefLabel, Literal("Etiqueta", lang="es")))
    graph.add((subject, SKOS.prefLabel, Literal("Default label")))
    graph.add((subject, SDS.hasVariable, variable))
    graph.add((subject, OWL.equivalentClass, target))
    graph.add((subject, OWL.equivalentClass, target))

    assert _preferred_literal(graph, subject, SKOS.prefLabel) == "Default label"
    assert _concept_type_for_subject(graph, subject) == "Disclosure"
    assert [item.target_uri for item in _extract_equivalences(graph, subject)] == [
        "gri:303_3"
    ]

    records = extract_semantic_records(graph)
    assert records["syg:Concept"].formula is not None
    assert records["syg:Concept"].concept_state == "calculable"

    ontology_path = tmp_path / "ontology.owl"
    graph.serialize(destination=ontology_path, format="xml")
    loaded_path, _loaded_graph, loaded_records = load_semantic_records(ontology_path)

    assert loaded_path == ontology_path
    assert "syg:Concept" in loaded_records
