from __future__ import annotations

from sds_core.semantics.bundle import default_semantics_bundle_sources


def test_semantics_bundle_sources_use_active_split_ontology():
    bundle_paths = {
        source.bundle_path for source in default_semantics_bundle_sources()
    }

    assert "ontology/core_tbox.owl" in bundle_paths
    assert "ontology/generated_projection.owl" in bundle_paths
    assert "ontology/base.owl" not in bundle_paths
    assert len(bundle_paths) == 8


def test_semantics_bundle_sources_use_governance_policy_schema():
    sources_by_bundle_path = {
        source.bundle_path: source.source_path
        for source in default_semantics_bundle_sources()
    }

    assert (
        str(sources_by_bundle_path["policies/policy_registry_schema.json"])
        == "governance\\policies\\policy_registry_schema.json"
        or str(sources_by_bundle_path["policies/policy_registry_schema.json"])
        == "governance/policies/policy_registry_schema.json"
    )
