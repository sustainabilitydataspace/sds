from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from src.services.value_versioning import (
    CONTEXT_HASH_RECIPE_VERSION,
    CONTEXT_HASH_RECIPE_VERSION_V2,
    SDS_CANONICAL_OPERATIONAL_RELEASE_ID,
    SOURCE_OBSERVATION_CANONICAL_OPERATIONAL,
    ValueContextIdentity,
    ValueVersioningError,
    assert_state_transition_allowed,
    build_context_hash,
    canonical_numeric_string,
    normalize_dimensions,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("001.2300", "1.23"),
        ("0.000", "0"),
        ("1E+3", "1000"),
        (Decimal("-0.00"), "0"),
        ("-12.3400", "-12.34"),
        (42, "42"),
    ],
)
def test_canonical_numeric_string_normalizes_hash_material_values(raw, expected):
    assert canonical_numeric_string(raw) == expected


@pytest.mark.parametrize(
    "raw", ["NaN", "Infinity", "-Infinity", "1,000", "1_000", "0x10", True]
)
def test_canonical_numeric_string_rejects_non_canonical_numeric_inputs(raw):
    with pytest.raises(ValueVersioningError):
        canonical_numeric_string(raw)


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), "", "not-a-number"])
def test_canonical_numeric_string_rejects_runtime_edge_inputs(raw):
    with pytest.raises(ValueVersioningError):
        canonical_numeric_string(raw)


def test_normalize_dimensions_preserves_none_members():
    assert normalize_dimensions({"scope": None, "gas": {"ch4", "co2"}}) == {
        "gas": ["ch4", "co2"],
        "scope": None,
    }


def test_context_hash_is_deterministic_and_excludes_revision_level_unit_policy():
    first = ValueContextIdentity(
        tenant_id="tenant-a",
        entity_id="entity-1",
        reporting_period_id="FY2026",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        period_close_date=None,
        period_type="annual",
        reporting_boundary_id="operational_control",
        sds_indicator_id="indicator-1",
        indicator_identifier="urn:sds:reg:esrs:e1_6_07",
        standard_release_id="ESRS_SET1_2023_12_22",
        standard_datapoint_id="E1-6_07",
        dimensions={"scope": "scope1", "gas": ["co2", "ch4"]},
        expected_unit="tCO2e",
        expected_currency=None,
        value_kind="numeric",
    )
    second = ValueContextIdentity(
        tenant_id="tenant-a",
        entity_id="entity-1",
        reporting_period_id="FY2026",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        period_close_date=None,
        period_type="annual",
        reporting_boundary_id="operational_control",
        sds_indicator_id="indicator-1",
        indicator_identifier="urn:sds:reg:esrs:e1_6_07",
        standard_release_id="ESRS_SET1_2023_12_22",
        standard_datapoint_id="E1-6_07",
        dimensions={"gas": ["ch4", "co2"], "scope": "scope1"},
        expected_unit="kgCO2e",
        expected_currency="EUR",
        value_kind="numeric",
    )

    assert build_context_hash(first) == build_context_hash(second)
    assert first.context_payload()["context_hash_recipe_version"] == (
        CONTEXT_HASH_RECIPE_VERSION
    )
    assert "expected_unit" not in first.context_payload()["identity_fields"]


def test_context_hash_recipe_version_is_pinned_into_hash():
    identity = ValueContextIdentity(
        tenant_id="tenant-a",
        entity_id="entity-1",
        reporting_period_id="FY2026",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        period_close_date=None,
        period_type="annual",
        reporting_boundary_id="operational_control",
        sds_indicator_id="indicator-1",
        indicator_identifier="urn:sds:reg:esrs:e1_6_07",
        standard_release_id="ESRS_SET1_2023_12_22",
        standard_datapoint_id="E1-6_07",
        dimensions={},
        expected_unit=None,
        expected_currency=None,
        value_kind="numeric",
    )

    assert build_context_hash(identity) != build_context_hash(
        identity,
        recipe_version="sds-value-context-v2",
    )


def test_canonical_operational_context_uses_recipe_v2_identity_fields():
    canonical_uri = "syg:TotalEnergyConsumptionWithinOrganization"
    identity = ValueContextIdentity(
        tenant_id="tenant-a",
        entity_id="nh_group",
        reporting_period_id="FY2024",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        period_close_date=None,
        period_type="annual",
        reporting_boundary_id="operational_control",
        sds_indicator_id=None,
        indicator_identifier=canonical_uri,
        standard_release_id=SDS_CANONICAL_OPERATIONAL_RELEASE_ID,
        standard_datapoint_id=canonical_uri,
        dimensions={},
        expected_unit="MWh",
        expected_currency=None,
        value_kind="numeric",
        canonical_concept_id=101,
        canonical_uri=canonical_uri,
        source_observation_type=SOURCE_OBSERVATION_CANONICAL_OPERATIONAL,
        context_hash_recipe_version=CONTEXT_HASH_RECIPE_VERSION_V2,
    )

    payload = identity.context_payload()
    fields = payload["identity_fields"]

    assert payload["context_hash_recipe_version"] == CONTEXT_HASH_RECIPE_VERSION_V2
    assert fields["indicator_identifier"] == canonical_uri
    assert fields["standard_release_id"] == SDS_CANONICAL_OPERATIONAL_RELEASE_ID
    assert fields["standard_datapoint_id"] == canonical_uri
    assert fields["canonical_uri"] == canonical_uri
    assert fields["source_observation_type"] == SOURCE_OBSERVATION_CANONICAL_OPERATIONAL
    assert "canonical_concept_id" not in fields
    assert build_context_hash(identity) == build_context_hash(
        identity, recipe_version=CONTEXT_HASH_RECIPE_VERSION_V2
    )


def test_recipe_v1_context_payload_does_not_add_canonical_fields():
    identity = ValueContextIdentity(
        tenant_id="tenant-a",
        entity_id="entity-1",
        reporting_period_id="FY2026",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        period_close_date=None,
        period_type="annual",
        reporting_boundary_id="operational_control",
        sds_indicator_id=None,
        indicator_identifier="urn:sds:reg:esrs:e1_5_02",
        standard_release_id="ESRS_SET1_2023_12_22",
        standard_datapoint_id="E1-5_02",
        dimensions={},
        expected_unit="MWh",
        expected_currency=None,
        value_kind="numeric",
        canonical_uri="syg:TotalEnergyConsumptionWithinOrganization",
        source_observation_type=SOURCE_OBSERVATION_CANONICAL_OPERATIONAL,
    )

    fields = identity.context_payload()["identity_fields"]

    assert "canonical_uri" not in fields
    assert "source_observation_type" not in fields


def test_context_hash_allows_unresolved_sds_indicator_id():
    identity = ValueContextIdentity(
        tenant_id="tenant-a",
        entity_id="entity-1",
        reporting_period_id="FY2026",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        period_close_date=None,
        period_type="annual",
        reporting_boundary_id="operational_control",
        sds_indicator_id="indicator-1",
        indicator_identifier="urn:sds:legacy:csrd:E3_5",
        standard_release_id="legacy_current",
        standard_datapoint_id="csrd:E3_5",
        dimensions={},
        expected_unit=None,
        expected_currency=None,
        value_kind="numeric",
    )

    unresolved = replace(identity, sds_indicator_id=None)
    payload = unresolved.context_payload()

    assert payload["identity_fields"]["sds_indicator_id"] is None
    assert len(build_context_hash(unresolved)) == 64


def test_value_revision_transition_policy_blocks_reported_to_draft():
    assert_state_transition_allowed("approved", "locked")
    assert_state_transition_allowed("reported", "restated")

    with pytest.raises(ValueVersioningError):
        assert_state_transition_allowed("reported", "draft")

    with pytest.raises(ValueVersioningError):
        assert_state_transition_allowed("reported", "voided")

    with pytest.raises(ValueVersioningError):
        assert_state_transition_allowed("unknown", "reported")

    with pytest.raises(ValueVersioningError):
        assert_state_transition_allowed("reported", "unknown")


def test_context_payload_rejects_blank_required_identity_fields():
    identity = ValueContextIdentity(
        tenant_id=" ",
        entity_id="entity-1",
        reporting_period_id="FY2026",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        period_close_date=None,
        period_type="annual",
        reporting_boundary_id="operational_control",
        sds_indicator_id="indicator-1",
        indicator_identifier="urn:sds:reg:esrs:e1_6_07",
        standard_release_id="ESRS_SET1_2023_12_22",
        standard_datapoint_id="E1-6_07",
        dimensions={},
        expected_unit=None,
        expected_currency=None,
        value_kind="numeric",
    )

    with pytest.raises(ValueVersioningError, match="tenant_id is required"):
        identity.context_payload()
