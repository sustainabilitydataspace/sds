"""Tests for sds_core policy selection and export alignment (F09)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SDS_CORE_SRC = REPO_ROOT / "packages" / "sds_core" / "src"
if str(SDS_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(SDS_CORE_SRC))

from sds_core.dcat.builder import build_dcat_catalog
from sds_core.ngsi.export import dataset_entity
from sds_core.policy.selection import select_policy_for_dataset

# ---------------------------------------------------------------------------
# Canonical E6-style registry fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def canonical_registry():
    return {
        "denyByDefault": True,
        "defaultDenyPolicy": "policy-deny-all",
        "policies": [
            {
                "uid": "policy-deny-all",
                "status": "active",
                "regions": [],
                "odrl": {"prohibition": [{"action": "use"}]},
            },
            {
                "uid": "policy-reporting-365d-retention",
                "status": "active",
                "purpose": ["reporting"],
                "regions": ["EU"],
                "odrl": {"permission": [{"action": "use"}]},
            },
            {
                "uid": "policy-supervisory-730d",
                "status": "active",
                "purpose": ["supervisory"],
                "regions": ["EU", "ES"],
                "odrl": {"permission": [{"action": "use"}]},
            },
            {
                "uid": "policy-geofence-eu",
                "status": "active",
                "regions": ["EU"],
                "compositionMode": "additive",
                "odrl": {"prohibition": [{"action": "transfer"}]},
            },
        ],
        "purposeVocabulary": {
            "reporting": {"defaultPolicyId": "policy-reporting-365d-retention"},
            "supervisory": {"defaultPolicyId": "policy-supervisory-730d"},
        },
        "regionVocabulary": {
            "EU": {"description": "EEA"},
            "ES": {"description": "Spain", "parentRegion": "EU"},
            "US": {"description": "United States"},
        },
    }


# ---------------------------------------------------------------------------
# H3: Explicit policy override must respect region compatibility
# ---------------------------------------------------------------------------


class TestExplicitPolicyRegionCompatibility:
    def test_explicit_policy_matching_region_is_accepted(self, canonical_registry):
        primary, additive = select_policy_for_dataset(
            canonical_registry,
            purpose="reporting",
            region="EU",
            explicit_policy_id="policy-reporting-365d-retention",
        )
        assert primary == "policy-reporting-365d-retention"

    def test_explicit_policy_incompatible_region_falls_back_to_deny(
        self, canonical_registry
    ):
        primary, additive = select_policy_for_dataset(
            canonical_registry,
            purpose="reporting",
            region="US",
            explicit_policy_id="policy-reporting-365d-retention",
        )
        assert primary == "policy-deny-all"
        assert additive == []

    def test_explicit_default_deny_policy_always_allowed(self, canonical_registry):
        primary, additive = select_policy_for_dataset(
            canonical_registry,
            purpose="reporting",
            region="US",
            explicit_policy_id="policy-deny-all",
        )
        assert primary == "policy-deny-all"

    def test_explicit_unknown_policy_falls_back_to_deny(self, canonical_registry):
        primary, additive = select_policy_for_dataset(
            canonical_registry,
            purpose="reporting",
            region="EU",
            explicit_policy_id="policy-nonexistent",
        )
        assert primary == "policy-deny-all"
        assert additive == []

    def test_explicit_policy_no_region_restriction_is_accepted_anywhere(
        self, canonical_registry
    ):
        # policy-deny-all has regions=[] which means no restriction
        registry = {
            **canonical_registry,
            "policies": [
                *canonical_registry["policies"],
                {
                    "uid": "policy-global-audit",
                    "status": "active",
                    "purpose": ["audit"],
                    "regions": [],
                    "odrl": {"permission": [{"action": "use"}]},
                },
            ],
            "purposeVocabulary": {
                **canonical_registry.get("purposeVocabulary", {}),
                "audit": {"defaultPolicyId": "policy-global-audit"},
            },
        }
        primary, additive = select_policy_for_dataset(
            registry,
            purpose="audit",
            region="US",
            explicit_policy_id="policy-global-audit",
        )
        assert primary == "policy-global-audit"


# ---------------------------------------------------------------------------
# H2: Additive policies emitted in NGSI-LD and DCAT exports
# ---------------------------------------------------------------------------


class TestAdditivePolicyExportAlignment:
    def test_ngsi_export_includes_additive_policies_for_eu(self, canonical_registry):
        row = {
            "identifier": "test-ds-1",
            "title": "Test Dataset",
            "description": "",
            "dimension": "",
            "codeESRS": "",
            "codeGRI": "",
            "owner": "",
            "accessRights": "",
            "evidencePath": "",
            "sourceRow": "",
            "purpose": "reporting",
            "region": "EU",
        }
        ent, _ = dataset_entity(row, canonical_registry)
        assert ent["hasPolicy"]["object"] == [
            "urn:sds:policy:policy-reporting-365d-retention",
            "urn:sds:policy:policy-geofence-eu",
        ]

    def test_ngsi_export_has_empty_additive_for_non_eu(self, canonical_registry):
        row = {
            "identifier": "test-ds-2",
            "title": "Test Dataset",
            "description": "",
            "dimension": "",
            "codeESRS": "",
            "codeGRI": "",
            "owner": "",
            "accessRights": "",
            "evidencePath": "",
            "sourceRow": "",
            "purpose": "reporting",
            "region": "US",
        }
        ent, _ = dataset_entity(row, canonical_registry)
        assert ent["hasPolicy"]["object"] == "urn:sds:policy:policy-deny-all"

    def test_dcat_export_includes_additive_policies_for_eu(self, canonical_registry):
        datasets = [
            {
                "identifier": "test-ds-3",
                "title": "Test Dataset",
                "description": "",
                "dimension": "",
                "owner": "",
                "purpose": "reporting",
                "region": "EU",
            }
        ]
        catalog = build_dcat_catalog(datasets, policy_registry=canonical_registry)
        ds = catalog["dcat:dataset"][0]
        policy_ids = {p["@id"] for p in _policy_list(ds["odrl:hasPolicy"])}
        assert "urn:sds:policy:policy-reporting-365d-retention" in policy_ids
        assert "urn:sds:policy:policy-geofence-eu" in policy_ids

    def test_dcat_export_has_single_policy_for_non_eu(self, canonical_registry):
        datasets = [
            {
                "identifier": "test-ds-4",
                "title": "Test Dataset",
                "description": "",
                "dimension": "",
                "owner": "",
                "purpose": "reporting",
                "region": "US",
            }
        ]
        catalog = build_dcat_catalog(datasets, policy_registry=canonical_registry)
        ds = catalog["dcat:dataset"][0]
        policy_ids = {p["@id"] for p in _policy_list(ds["odrl:hasPolicy"])}
        assert policy_ids == {"urn:sds:policy:policy-deny-all"}

    def test_edc_ngsi_dcat_policy_selection_stays_aligned(self, canonical_registry):
        """Regression: EDC, NGSI-LD, and DCAT must see the same primary/additive
        policies for a given dataset row."""
        row = {
            "identifier": "aligned-ds",
            "title": "Aligned",
            "description": "",
            "dimension": "",
            "codeESRS": "",
            "codeGRI": "",
            "owner": "",
            "accessRights": "",
            "evidencePath": "",
            "sourceRow": "",
            "purpose": "supervisory",
            "region": "ES",
        }
        primary, additive = select_policy_for_dataset(
            canonical_registry,
            purpose="supervisory",
            region="ES",
        )
        assert primary == "policy-supervisory-730d"
        assert additive == ["policy-geofence-eu"]

        ent, _ = dataset_entity(row, canonical_registry)
        assert ent["hasPolicy"]["object"] == [
            f"urn:sds:policy:{primary}",
            *[f"urn:sds:policy:{pid}" for pid in additive],
        ]

        catalog = build_dcat_catalog(
            [{k: str(v) for k, v in row.items()}], policy_registry=canonical_registry
        )
        ds = catalog["dcat:dataset"][0]
        policy_ids = {p["@id"] for p in _policy_list(ds["odrl:hasPolicy"])}
        assert policy_ids == {
            f"urn:sds:policy:{primary}",
            f"urn:sds:policy:policy-geofence-eu",
        }


def _policy_list(value):
    return value if isinstance(value, list) else [value]
