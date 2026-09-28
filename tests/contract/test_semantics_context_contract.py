import json
from pathlib import Path

from sds_core.semantics.uris import CANONICAL_SDS_NAMESPACE


def test_versioned_context_v1_has_canonical_vocab():
    doc = json.loads(Path("semantics/context/sds/v1.0.jsonld").read_text(encoding="utf-8"))
    ctx = doc["@context"]

    assert ctx["@vocab"] == CANONICAL_SDS_NAMESPACE
    assert ctx["sds"] == CANONICAL_SDS_NAMESPACE


def test_dev_context_has_canonical_vocab():
    doc = json.loads(Path("semantics/context/ngsi_ld_context.jsonld").read_text(encoding="utf-8"))
    ctx = doc["@context"]

    assert ctx["@vocab"] == CANONICAL_SDS_NAMESPACE
    assert ctx["sds"] == CANONICAL_SDS_NAMESPACE


def test_contexts_cover_active_standard_relationship_policy_and_calculation_terms():
    required_terms = {
        "codeGHG",
        "sourceStandard",
        "standardVersion",
        "hasPolicy",
        "conformsTo",
        "relationshipType",
        "relationshipStatus",
        "activationStatus",
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
        "effectiveFrom",
        "effectiveTo",
        "purpose",
        "spatialCoverage",
        "provider",
        "provenance",
    }

    for path in (
        Path("semantics/context/sds/v1.0.jsonld"),
        Path("semantics/context/ngsi_ld_context.jsonld"),
    ):
        doc = json.loads(path.read_text(encoding="utf-8"))
        ctx = doc["@context"]
        missing = sorted(required_terms - set(ctx))
        assert not missing, f"{path} missing active semantics terms: {missing}"
