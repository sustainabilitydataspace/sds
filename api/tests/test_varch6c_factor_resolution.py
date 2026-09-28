"""VARCH-6c contract tests — resolver factor & measurement resolution.

Pure deterministic core (resolver contract lines 150-154): required axes + known terms;
deterministic factor-vintage selection per the explicit policy_kind; factor source authority +
license-rights window + egress (4f); measurement basis + unit commensurability; calculation pin
bundle (5c) cross-checked against the selected vintage. No DB layer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.semantic.profiles import computation
from src.semantic.resolver_factors import (
    FactorResolutionError,
    FactorSetProvenance,
    FactorVintageCandidate,
    assert_measurement_commensurable,
    select_factor_vintage,
    validate_calculation_pins,
    validate_factor_source_rights,
    validate_required_axes_and_terms,
)
from src.semantic.write_binding import (
    CalculationBindingPins,
    FactorSetPin,
    VintagePolicyPin,
)
from src.semantic.write_scope import LicenseError, LicenseGrant, Window

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=365)
T2 = T1 + timedelta(days=365)


# --- required axes & terms ------------------------------------------------------------

_PUB_AXES = ["ax:fuel", "ax:scope"]
_AXIS_TERMS = {"ax:fuel": {"diesel", "petrol"}, "ax:scope": {"s1", "s2"}}


def test_required_axes_and_terms_pass() -> None:
    validate_required_axes_and_terms(
        required_axes=["ax:fuel"],
        required_axis_terms={"ax:fuel": ["diesel"]},
        published_axis_ids=_PUB_AXES,
        axis_terms=_AXIS_TERMS,
    )


def test_rejects_unpublished_required_axis() -> None:
    with pytest.raises(FactorResolutionError, match="not published"):
        validate_required_axes_and_terms(
            required_axes=["ax:bogus"],
            required_axis_terms={},
            published_axis_ids=_PUB_AXES,
            axis_terms=_AXIS_TERMS,
        )


def test_rejects_unknown_term() -> None:
    with pytest.raises(FactorResolutionError, match="unknown term"):
        validate_required_axes_and_terms(
            required_axes=["ax:fuel"],
            required_axis_terms={"ax:fuel": ["kerosene"]},
            published_axis_ids=_PUB_AXES,
            axis_terms=_AXIS_TERMS,
        )


# --- deterministic factor-vintage selection -------------------------------------------


def _cand(version, vf, vt, dcid, status="published", fid=None):
    return FactorVintageCandidate(
        factor_set_id=fid or f"fs:{version}",
        factor_set_key="fs",
        factor_set_version=version,
        valid_from=vf,
        valid_to=vt,
        decision_commit_id=dcid,
        factor_set_hash=str(version) * 64 if version < 10 else "a" * 64,
    )


_CANDS = [
    _cand(1, T0, T1, 10),
    _cand(2, T1, T2, 20),
    _cand(3, T2, None, 30),
]


def test_vintage_latest_published() -> None:
    got = select_factor_vintage("latest_published", _CANDS)
    assert got.factor_set_version == 3


def test_vintage_reporting_period_match() -> None:
    got = select_factor_vintage(
        "reporting_period_match", _CANDS, reporting_period=T1 + timedelta(days=10)
    )
    assert got.factor_set_version == 2


def test_vintage_reporting_period_no_match() -> None:
    with pytest.raises(FactorResolutionError, match="need exactly 1"):
        select_factor_vintage(
            "reporting_period_match", _CANDS, reporting_period=T0 - timedelta(days=1)
        )


def test_vintage_fixed() -> None:
    got = select_factor_vintage("fixed_vintage", _CANDS, fixed_version=2)
    assert got.factor_set_version == 2


def test_vintage_as_of_decision_time() -> None:
    got = select_factor_vintage("as_of_decision_time", _CANDS, decision_commit_id=25)
    assert got.factor_set_version == 2  # greatest decision_commit_id <= 25


def test_vintage_as_of_decision_time_none_eligible() -> None:
    with pytest.raises(FactorResolutionError, match="at or before"):
        select_factor_vintage("as_of_decision_time", _CANDS, decision_commit_id=5)


def test_vintage_custom_requires_selection_id() -> None:
    with pytest.raises(FactorResolutionError, match="custom_selection_id"):
        select_factor_vintage("custom", _CANDS)


def test_vintage_rejects_unknown_policy() -> None:
    with pytest.raises(FactorResolutionError, match="unknown vintage policy"):
        select_factor_vintage("bogus", _CANDS)


def test_vintage_ignores_unpublished_candidates() -> None:
    cands = [
        _cand(1, T0, T1, 10),
        FactorVintageCandidate(
            "fs:2", "fs", 2, T1, 20, "2" * 64, valid_to=T2, status="superseded"
        ),
    ]
    got = select_factor_vintage("latest_published", cands)
    assert got.factor_set_version == 1  # the superseded v2 is ignored


def test_vintage_latest_published_tie_is_ambiguous() -> None:
    # M1: two published vintages share the highest version -> fail closed.
    cands = [_cand(2, T0, T1, 10, fid="fs:a"), _cand(2, T1, T2, 20, fid="fs:b")]
    with pytest.raises(FactorResolutionError, match="ambiguous"):
        select_factor_vintage("latest_published", cands)


def test_vintage_as_of_decision_time_tie_is_ambiguous() -> None:
    # M2: two vintages share the greatest eligible decision_commit_id -> fail closed.
    cands = [_cand(1, T0, T1, 20, fid="fs:a"), _cand(2, T1, T2, 20, fid="fs:b")]
    with pytest.raises(FactorResolutionError, match="ambiguous"):
        select_factor_vintage("as_of_decision_time", cands, decision_commit_id=25)


# --- factor source rights -------------------------------------------------------------


def _grant(**over):
    kwargs = dict(
        valid_window=Window(T0, T2),
        decision_window=Window(T0, T2),
        access_scope="shared",
        rights_scope="shared",
    )
    kwargs.update(over)
    return LicenseGrant(**kwargs)


def _prov(**over):
    kwargs = dict(
        source_authority="DEFRA",
        source_dataset_id="ds:ghg",
        source_dataset_version="2026",
        evidence_hash="e" * 64,
        license_ref="lic:defra:1",
    )
    kwargs.update(over)
    return FactorSetProvenance(**kwargs)


def _rights(grant=None, *, provenance=None, applied_license_ref="lic:defra:1", **over):
    kwargs = dict(
        known_authorities=["DEFRA", "EPA"],
        target_scope="shared",
        valid_at=T1,
        decision_at=T1,
    )
    kwargs.update(over)
    validate_factor_source_rights(
        grant or _grant(),
        provenance=provenance or _prov(),
        applied_license_ref=applied_license_ref,
        **kwargs,
    )


def test_factor_source_rights_pass() -> None:
    _rights()


def test_factor_source_rejects_unknown_authority() -> None:
    with pytest.raises(FactorResolutionError, match="not a recognized authority"):
        _rights(
            provenance=_prov(source_authority="MYSTERY"), known_authorities=["DEFRA"]
        )


def test_factor_source_rejects_incomplete_provenance() -> None:
    with pytest.raises(FactorResolutionError, match="incomplete"):
        _rights(provenance=_prov(evidence_hash=""))


def test_factor_source_rejects_unbound_license() -> None:
    # applied license is valid but not THIS factor set's licensed window (M3).
    with pytest.raises(FactorResolutionError, match="not bound to this factor set"):
        _rights(applied_license_ref="lic:other:9")


def test_factor_source_rejects_expired_license() -> None:
    with pytest.raises(LicenseError):
        _rights(_grant(expiry=T0 + timedelta(days=1)))


def test_factor_source_rejects_overbroad_egress() -> None:
    with pytest.raises(LicenseError):
        _rights(target_scope="public")  # more permissive than shared/shared


# --- measurement / unit commensurability ----------------------------------------------

_CONV = {"mass": {"kg", "t"}, "energy": {"kWh", "MJ"}}


def test_measurement_commensurable_pass() -> None:
    assert_measurement_commensurable(
        value_quantity_kind="mass",
        factor_quantity_kind="mass",
        value_unit="kg",
        factor_unit="t",
        measurement_basis="mass",
        allowed_bases=["mass", "energy"],
        convertible_units=_CONV,
    )


def test_measurement_rejects_disallowed_basis() -> None:
    with pytest.raises(FactorResolutionError, match="not allowed"):
        assert_measurement_commensurable(
            value_quantity_kind="mass",
            factor_quantity_kind="mass",
            value_unit="kg",
            factor_unit="t",
            measurement_basis="volume",
            allowed_bases=["mass"],
            convertible_units=_CONV,
        )


def test_measurement_rejects_incommensurable_kinds() -> None:
    with pytest.raises(FactorResolutionError, match="incommensurable"):
        assert_measurement_commensurable(
            value_quantity_kind="mass",
            factor_quantity_kind="energy",
            value_unit="kg",
            factor_unit="kWh",
            measurement_basis="mass",
            allowed_bases=["mass"],
            convertible_units=_CONV,
        )


def test_measurement_rejects_unregistered_unit() -> None:
    with pytest.raises(FactorResolutionError, match="not registered"):
        assert_measurement_commensurable(
            value_quantity_kind="mass",
            factor_quantity_kind="mass",
            value_unit="lb",  # not in registry
            factor_unit="kg",
            measurement_basis="mass",
            allowed_bases=["mass"],
            convertible_units=_CONV,
        )


# --- calculation pins + selected-vintage cross-check ----------------------------------

_SEL = FactorVintageCandidate("fs:ghg:2026", "fs:ghg", 1, T0, 10, "a" * 64, valid_to=T2)


def _pins(**over):
    kwargs = dict(
        contract_hash="c" * 64,
        computation_profile_hash=computation.profile_hash(),
        factor_set=FactorSetPin("fs:ghg:2026", "1", "a" * 64),
        vintage_policy=VintagePolicyPin("vp:latest", "v1", "d" * 64),
        confidence_policy="cp:source-weighted",
        replay_manifest_ref="rm:1",
    )
    kwargs.update(over)
    return CalculationBindingPins(**kwargs)


def _validate(pins=None, selected=_SEL, **over):
    kwargs = dict(
        active_contract_hashes={"c" * 64},
        published_factor_sets={"fs:ghg:2026": ("1", "a" * 64)},
        published_vintage_policies={"vp:latest": ("v1", "d" * 64)},
        known_confidence_policies={"cp:source-weighted"},
        known_replay_manifests={"rm:1"},
    )
    kwargs.update(over)
    validate_calculation_pins(pins or _pins(), selected, **kwargs)


def test_calculation_pins_pass_with_matching_vintage() -> None:
    _validate()


def test_calculation_pins_reject_vintage_id_mismatch() -> None:
    other = FactorVintageCandidate("fs:other", "fs", 1, T0, 10, "a" * 64)
    with pytest.raises(FactorResolutionError, match="!= selected vintage"):
        _validate(selected=other)


def test_calculation_pins_reject_vintage_hash_drift() -> None:
    drifted = FactorVintageCandidate("fs:ghg:2026", "fs:ghg", 1, T0, 10, "b" * 64)
    with pytest.raises(FactorResolutionError, match="hash != selected"):
        _validate(selected=drifted)


def test_calculation_pins_reject_inactive_contract() -> None:
    # delegates to 5c validate_calculation_binding (BindingError) for pin-bundle defects.
    with pytest.raises(Exception):
        _validate(pins=_pins(contract_hash="f" * 64))


def test_calculation_pins_reject_noncanonical_version() -> None:
    # M4: '01' must not compare equal to selected version 1.
    pins = _pins(factor_set=FactorSetPin("fs:ghg:2026", "01", "a" * 64))
    with pytest.raises(FactorResolutionError, match="non-canonical"):
        _validate(
            pins=pins,
            published_factor_sets={"fs:ghg:2026": ("01", "a" * 64)},
        )
