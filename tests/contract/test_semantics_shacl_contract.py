from pathlib import Path

import pytest

try:
    from rdflib import Graph, Literal, Namespace
    from rdflib.namespace import DCTERMS, RDF
    from pyshacl import validate  # type: ignore
except Exception as e:  # pragma: no cover
    pytest.skip(f"SHACL contract test skipped (missing rdflib/pyshacl): {e}", allow_module_level=True)

from sds_core.semantics.uris import CANONICAL_SDS_NAMESPACE


def test_semantics_shacl_shapes_validate_minimal_dataset_and_indicator():
    shapes_paths = [
        Path("semantics/shacl/shapes_register_shacl.ttl"),
        Path("semantics/shacl/shapes_indicator_shacl.ttl"),
    ]

    shapes_graph = Graph()
    for p in shapes_paths:
        assert p.exists(), f"Missing SHACL shapes file: {p}"
        shapes_graph.parse(str(p), format="turtle")

    sds = Namespace(CANONICAL_SDS_NAMESPACE)

    data_graph = Graph()
    ds = sds["dataset-1"]
    data_graph.add((ds, RDF.type, sds.Dataset))
    data_graph.add((ds, DCTERMS.identifier, Literal("urn:sds:reg:test-1")))
    data_graph.add((ds, DCTERMS.title, Literal("Test dataset")))
    data_graph.add((ds, sds.dimension, Literal("Environmental")))
    data_graph.add((ds, sds.evidencePath, Literal("documents/source.pdf#page=1")))
    data_graph.add((ds, sds.sourceRow, Literal(1)))

    ind = sds["indicator-1"]
    data_graph.add((ind, RDF.type, sds.Indicator))
    data_graph.add((ind, DCTERMS.title, Literal("Energy consumption")))

    conforms, _report_graph, report_text = validate(
        data_graph=data_graph,
        shacl_graph=shapes_graph,
        inference="rdfs",
        abort_on_first=False,
        allow_infos=True,
        allow_warnings=True,
    )
    assert conforms, report_text


def test_semantics_shacl_dataset_requires_evidence_path_and_source_row():
    shapes_paths = [
        Path("semantics/shacl/shapes_register_shacl.ttl"),
        Path("semantics/shacl/shapes_indicator_shacl.ttl"),
    ]

    shapes_graph = Graph()
    for p in shapes_paths:
        assert p.exists(), f"Missing SHACL shapes file: {p}"
        shapes_graph.parse(str(p), format="turtle")

    sds = Namespace(CANONICAL_SDS_NAMESPACE)

    data_graph = Graph()
    ds = sds["dataset-2"]
    data_graph.add((ds, RDF.type, sds.Dataset))
    data_graph.add((ds, DCTERMS.identifier, Literal("urn:sds:reg:test-2")))
    data_graph.add((ds, DCTERMS.title, Literal("Dataset missing provenance")))
    data_graph.add((ds, sds.dimension, Literal("Environmental")))

    conforms, _report_graph, report_text = validate(
        data_graph=data_graph,
        shacl_graph=shapes_graph,
        inference="rdfs",
        abort_on_first=False,
        allow_infos=True,
        allow_warnings=True,
    )
    assert not conforms, report_text
