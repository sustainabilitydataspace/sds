from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from src.calculation.db_semantic_snapshot import (
    DBSemanticConcept,
    DBSemanticEquivalence,
    DBSemanticVariable,
)
from src.ontology.semantic_backfill import load_semantic_records, ontology_sha256

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "api" / "scripts" / "gate_wave1_parity.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "gate_wave1_parity_script", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate_wave1_parity = _load_module()


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


def test_compare_parity_passes_for_matching_semantic_snapshots():
    ontology_path, graph, records = load_semantic_records()
    db_snapshots = _build_db_snapshots(records)

    report, failures = gate_wave1_parity.compare_parity(
        records,
        db_snapshots,
        graph,
        ontology_path=ontology_path,
        expected_hash=ontology_sha256(ontology_path),
    )

    assert failures == []
    assert report["concept_count"] == len(records)
    assert report["extra_db_uris"] == []
    assert report["concepts"][records["csrd:E3_5"].uri]["evaluation_match"] is True


def test_compare_parity_flags_structural_mismatch():
    ontology_path, graph, records = load_semantic_records()
    db_snapshots = _build_db_snapshots(records)

    broken = db_snapshots[records["csrd:E3_5"].uri]
    db_snapshots[broken.uri] = DBSemanticConcept(
        uri=broken.uri,
        label=broken.label,
        description=broken.description,
        taxonomy=broken.taxonomy,
        concept_type=broken.concept_type,
        unit="sds:Tonne",
        temporal_granularity=broken.temporal_granularity,
        concept_state=broken.concept_state,
        formula_expression=broken.formula_expression,
        formula_language=broken.formula_language,
        variables=broken.variables,
        equivalences=broken.equivalences,
    )

    _report, failures = gate_wave1_parity.compare_parity(
        records,
        db_snapshots,
        graph,
        ontology_path=ontology_path,
        expected_hash=ontology_sha256(ontology_path),
    )

    assert any("csrd:E3_5: unit_match failed" == failure for failure in failures)
