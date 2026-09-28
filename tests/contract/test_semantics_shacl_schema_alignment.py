import json
from pathlib import Path

import pytest

try:
    from rdflib import Graph, Namespace, URIRef
except Exception as e:  # pragma: no cover
    pytest.skip(f"SHACL schema alignment test skipped (missing rdflib): {e}", allow_module_level=True)

from sds_core.semantics.uris import CANONICAL_SDS_NAMESPACE


SH = Namespace("http://www.w3.org/ns/shacl#")


def _expand_compact_iri(compact: str, prefixes: dict[str, str], vocab: str) -> str:
    if compact.startswith("http://") or compact.startswith("https://"):
        return compact
    if ":" in compact:
        prefix, suffix = compact.split(":", 1)
        assert prefix in prefixes, f"Unknown prefix '{prefix}' in compact IRI: {compact}"
        return prefixes[prefix] + suffix
    return vocab + compact


def _context_term_to_iri(term: str, ctx: dict) -> str:
    mapping = ctx.get(term)
    assert mapping is not None, f"Missing JSON-LD context mapping for term: {term}"

    vocab = ctx.get("@vocab")
    assert isinstance(vocab, str) and vocab, "Missing @vocab in JSON-LD context"

    prefixes = {k: v for k, v in ctx.items() if isinstance(v, str) and v.startswith("http")}

    if isinstance(mapping, str):
        return _expand_compact_iri(mapping, prefixes, vocab)
    if isinstance(mapping, dict) and "@id" in mapping:
        return _expand_compact_iri(mapping["@id"], prefixes, vocab)
    raise AssertionError(f"Unsupported JSON-LD context mapping for term {term}: {mapping}")


def test_dataset_shacl_shape_covers_json_schema_required_fields():
    schema = json.loads(Path("semantics/schema/register_schema.json").read_text(encoding="utf-8"))
    required_terms = schema.get("required") or []
    assert required_terms, "Schema has no 'required' terms to validate"

    context_doc = json.loads(Path("semantics/context/ngsi_ld_context.jsonld").read_text(encoding="utf-8"))
    ctx = context_doc.get("@context") or {}

    expected_paths = {term: URIRef(_context_term_to_iri(term, ctx)) for term in required_terms}

    shapes_graph = Graph()
    shapes_graph.parse("semantics/shacl/shapes_register_shacl.ttl", format="turtle")

    sds = Namespace(CANONICAL_SDS_NAMESPACE)
    dataset_shapes = set(shapes_graph.subjects(SH.targetClass, sds.Dataset))
    assert dataset_shapes, "No SHACL NodeShape found targeting sds:Dataset"

    path_to_min_count: dict[URIRef, int] = {}
    for shape in dataset_shapes:
        for prop in shapes_graph.objects(shape, SH.property):
            path = shapes_graph.value(prop, SH.path)
            if not isinstance(path, URIRef):
                continue
            min_count_literal = shapes_graph.value(prop, SH.minCount)
            if min_count_literal is None:
                continue
            try:
                min_count = int(str(min_count_literal))
            except ValueError:
                continue
            path_to_min_count[path] = max(path_to_min_count.get(path, 0), min_count)

    for term, path in expected_paths.items():
        assert path in path_to_min_count, f"Missing SHACL sh:path for required term: {term} ({path})"
        assert (
            path_to_min_count[path] >= 1
        ), f"SHACL minCount must be >=1 for required term: {term} ({path})"


def test_register_schema_context_and_dataset_shape_cover_active_optional_terms():
    active_terms = {
        "codeGHG",
        "sourceStandard",
        "standardVersion",
        "hasPolicy",
        "conformsTo",
        "relationshipType",
        "relationshipStatus",
        "activationStatus",
        "unitType",
        "unitConversionPolicy",
        "conversionStatus",
        "nonConvertible",
        "calculationStatus",
        "formula",
        "formulaLanguage",
        "componentRef",
        "runtimeExternalInput",
        "valueBoundary",
        "referenceDataSource",
        "purpose",
        "spatialCoverage",
        "provider",
        "provenance",
        "effectiveFrom",
        "effectiveTo",
    }

    schema = json.loads(Path("semantics/schema/register_schema.json").read_text(encoding="utf-8"))
    schema_terms = set(schema.get("properties") or {})
    missing_schema_terms = sorted(active_terms - schema_terms)
    assert not missing_schema_terms, f"Register schema missing active terms: {missing_schema_terms}"

    context_doc = json.loads(Path("semantics/context/ngsi_ld_context.jsonld").read_text(encoding="utf-8"))
    ctx = context_doc.get("@context") or {}
    expected_paths = {term: URIRef(_context_term_to_iri(term, ctx)) for term in active_terms}

    shapes_graph = Graph()
    shapes_graph.parse("semantics/shacl/shapes_register_shacl.ttl", format="turtle")

    shacl_paths = {
        path
        for path in shapes_graph.objects(None, SH.path)
        if isinstance(path, URIRef)
    }

    missing_shacl_terms = sorted(
        term for term, path in expected_paths.items() if path not in shacl_paths
    )
    assert not missing_shacl_terms, f"Dataset SHACL shape missing active terms: {missing_shacl_terms}"


def test_indicator_shape_covers_active_indicator_terms():
    context_doc = json.loads(Path("semantics/context/ngsi_ld_context.jsonld").read_text(encoding="utf-8"))
    ctx = context_doc.get("@context") or {}
    expected_terms = {
        "codeESRS",
        "codeGRI",
        "codeGHG",
        "unitName",
        "unitType",
        "calculationStatus",
        "relationshipStatus",
        "hasPolicy",
        "evidencePath",
        "sourceRow",
    }
    expected_paths = {term: URIRef(_context_term_to_iri(term, ctx)) for term in expected_terms}

    shapes_graph = Graph()
    shapes_graph.parse("semantics/shacl/shapes_indicator_shacl.ttl", format="turtle")

    sds = Namespace(CANONICAL_SDS_NAMESPACE)
    indicator_shapes = set(shapes_graph.subjects(SH.targetClass, sds.Indicator))
    assert indicator_shapes, "No SHACL NodeShape found targeting sds:Indicator"

    shacl_paths = {
        path
        for shape in indicator_shapes
        for prop in shapes_graph.objects(shape, SH.property)
        for path in [shapes_graph.value(prop, SH.path)]
        if isinstance(path, URIRef)
    }

    missing_terms = sorted(
        term for term, path in expected_paths.items() if path not in shacl_paths
    )
    assert not missing_terms, f"Indicator SHACL shape missing active terms: {missing_terms}"
