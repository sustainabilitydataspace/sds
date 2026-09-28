from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "api" / "scripts" / "gate_wave2_projection.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "gate_wave2_projection_script", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate_wave2 = _load_module()


def _concept(uri: str, concept_type: str, formula_count: int = 0):
    formulas = [SimpleNamespace(is_active=True) for _ in range(formula_count)]
    return SimpleNamespace(uri=uri, concept_type=concept_type, formulas=formulas)


def test_compare_projection_passes_for_matching_counts_and_runtime_path():
    report = {
        "projected_concepts": 2,
        "projected_disclosures": 1,
        "projected_variables": 1,
        "projected_formulas": 1,
    }
    concepts = [
        _concept("https://data.efrag.org/esrs#E3_5", "Disclosure", formula_count=1),
        _concept(
            "https://sustainabilitydataspace.com/sygris#Water_Cooling", "Variable"
        ),
    ]
    graph = Graph()
    graph.add((URIRef(concepts[0].uri), RDF.type, gate_wave2.SDS_DISCLOSURE))
    graph.add((URIRef(concepts[1].uri), RDF.type, gate_wave2.SDS_VARIABLE))
    graph.add(
        (
            URIRef("https://sustainabilitydataspace.com/ontology#Formula_csrd_E3_5"),
            RDF.type,
            gate_wave2.SDS_FORMULA,
        )
    )

    gate_report, failures = gate_wave2.compare_projection(
        report,
        concepts,
        graph,
        "D:/x/core_tbox.owl + D:/x/generated_projection.owl",
    )

    assert failures == []
    assert gate_report["db_concepts"] == 2
    assert gate_report["merged_formulas"] == 1


def test_compare_projection_expands_compact_db_concept_uri():
    report = {
        "projected_concepts": 1,
        "projected_disclosures": 1,
        "projected_variables": 0,
        "projected_formulas": 0,
    }
    concepts = [_concept("csrd:E3_5", "Disclosure")]
    graph = Graph()
    graph.add(
        (
            URIRef("https://data.efrag.org/esrs#E3_5"),
            RDF.type,
            gate_wave2.SDS_DISCLOSURE,
        )
    )

    _gate_report, failures = gate_wave2.compare_projection(
        report,
        concepts,
        graph,
        "D:/x/core_tbox.owl + D:/x/generated_projection.owl",
    )

    assert failures == []


def test_compare_projection_flags_missing_projection_triples():
    report = {
        "projected_concepts": 1,
        "projected_disclosures": 1,
        "projected_variables": 0,
        "projected_formulas": 0,
    }
    concepts = [_concept("https://data.efrag.org/esrs#E3_5", "Disclosure")]
    graph = Graph()

    _gate_report, failures = gate_wave2.compare_projection(
        report,
        concepts,
        graph,
        "D:/x/base.owl",
    )

    assert any(
        "Runtime is not loading the split ontology pair" in failure
        for failure in failures
    )
    assert any(
        "Projected ontology is missing triples for concept https://data.efrag.org/esrs#E3_5"
        == failure
        for failure in failures
    )


def test_compare_exact_projection_rejects_predicate_drift_and_stale_extras():
    concept = URIRef("https://data.efrag.org/esrs#E3_5")
    unit_predicate = URIRef("https://sustainabilitydataspace.com/ontology#unit")
    expected = Graph()
    expected.add((concept, RDF.type, gate_wave2.SDS_DISCLOSURE))
    expected.add((concept, unit_predicate, URIRef("https://qudt.org/vocab/unit/M3")))

    actual = Graph()
    actual.add((concept, RDF.type, gate_wave2.SDS_DISCLOSURE))
    actual.add(
        (
            URIRef("https://sustainabilitydataspace.com/ontology#stale"),
            RDF.type,
            gate_wave2.SDS_VARIABLE,
        )
    )

    failures = gate_wave2.compare_exact_projection(expected, actual)

    assert failures == [
        "Generated projection does not exactly match the canonical DB semantics "
        "(missing=1, stale_or_extra=1)"
    ]
