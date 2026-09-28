"""VARCH-4f contract tests — scope projection, consolidation composition, license egress.

Two layers: (1) the pure deterministic core (no DB) — tenant-scope isolation, scope-transition
no-retroactive-exposure, consolidation authorization + fail-closed disclosure / intersection-
only license composition, and license temporal-window + license-aware egress decisions;
(2) a disposable-DB smoke proving the consolidation + license profile pins match the persisted
seed (skipped unless SDS_MIGRATION_TEST_DATABASE_URL + SDS_MIGRATION_TEST_ALLOW_RESET=true).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from src.semantic.coverage import (
    EVAL_PUBLIC,
    EVAL_SHARED,
    EVAL_TENANT,
    SCOPE_PUBLIC,
    SCOPE_SHARED,
    SCOPE_TENANT_PRIVATE,
)
from src.semantic.profiles import registry
from src.semantic.write_scope import (
    EGRESS_READ_TIME,
    EGRESS_RETROACTIVE_RESTATEMENT,
    EGRESS_TRACE_DECISION_TIME,
    ConsolidationError,
    DisclosureProfile,
    LicenseError,
    LicenseGrant,
    ScopeError,
    Window,
    assert_consolidation_authorized,
    assert_egress_allowed,
    assert_license_valid,
    compose_disclosure,
    compose_license_scope,
    effective_scope_at,
    egress_reevaluation,
    is_visible,
    window_contains,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 1, tzinfo=timezone.utc)


# --- scope projection / isolation ----------------------------------------------------


def test_tenant_private_visible_only_to_matching_tenant() -> None:
    assert is_visible(SCOPE_TENANT_PRIVATE, "t1", EVAL_TENANT, "t1")
    assert not is_visible(SCOPE_TENANT_PRIVATE, "t1", EVAL_TENANT, "t2")
    assert not is_visible(SCOPE_TENANT_PRIVATE, "t1", EVAL_SHARED)
    assert not is_visible(SCOPE_TENANT_PRIVATE, "t1", EVAL_PUBLIC)


def test_public_shared_visibility() -> None:
    assert is_visible(SCOPE_PUBLIC, "__public__", EVAL_PUBLIC)
    assert not is_visible(
        SCOPE_SHARED, "__public__", EVAL_PUBLIC
    )  # shared hidden from public
    assert is_visible(SCOPE_SHARED, "__public__", EVAL_SHARED)


def test_is_visible_validation() -> None:
    with pytest.raises(ScopeError, match="unknown row scope"):
        is_visible("weird", "t", EVAL_SHARED)
    with pytest.raises(ScopeError, match="requires eval_tenant"):
        is_visible(SCOPE_PUBLIC, "t", EVAL_TENANT)


def test_scope_transition_no_retroactive_exposure() -> None:
    # tenant_private -> shared transition decided at commit 10.
    assert (
        effective_scope_at(
            SCOPE_TENANT_PRIVATE,
            SCOPE_SHARED,
            transition_decision_commit=10,
            read_decision_commit=5,
        )
        == SCOPE_TENANT_PRIVATE  # read before the transition still sees private
    )
    assert (
        effective_scope_at(
            SCOPE_TENANT_PRIVATE,
            SCOPE_SHARED,
            transition_decision_commit=10,
            read_decision_commit=10,
        )
        == SCOPE_SHARED
    )


# --- consolidation composition -------------------------------------------------------


def test_consolidation_requires_unanimous_consent() -> None:
    assert_consolidation_authorized({"a": True, "b": True})
    with pytest.raises(ConsolidationError, match="did not authorize"):
        assert_consolidation_authorized({"a": True, "b": False})
    with pytest.raises(ConsolidationError, match="no members"):
        assert_consolidation_authorized({})


def _dp(rank, pid=None, lattice="default"):
    return DisclosureProfile(pid or f"p{rank}", rank, lattice)


def test_disclosure_composition_fail_closed_most_restrictive() -> None:
    members = [_dp(0), _dp(2), _dp(1)]
    assert compose_disclosure(members).rank == 2  # most restrictive member
    # group profile allowed only if unanimously authorized AND >= every member.
    assert (
        compose_disclosure(
            [_dp(0), _dp(1)], group=_dp(2, "g"), unanimous_authorization=True
        ).rank
        == 2
    )
    with pytest.raises(ConsolidationError, match="fail-closed"):  # looser group
        compose_disclosure(
            [_dp(0), _dp(2)], group=_dp(1, "g"), unanimous_authorization=True
        )
    with pytest.raises(ConsolidationError, match="fail-closed"):  # not authorized
        compose_disclosure(
            [_dp(0), _dp(1)], group=_dp(2, "g"), unanimous_authorization=False
        )


def test_disclosure_composition_blocks_incomparable_lattices() -> None:
    with pytest.raises(ConsolidationError, match="incomparable"):
        compose_disclosure([_dp(1, lattice="A"), _dp(2, lattice="B")])


def test_license_scope_intersection_only() -> None:
    assert compose_license_scope(
        [frozenset({"x", "y", "z"}), frozenset({"y", "z"}), frozenset({"y"})]
    ) == frozenset({"y"})
    with pytest.raises(ConsolidationError, match="empty .*intersection"):
        compose_license_scope([frozenset({"a"}), frozenset({"b"})])


# --- license-aware egress ------------------------------------------------------------


def _grant(**over):
    kwargs = dict(
        valid_window=Window(T0, None),
        decision_window=Window(T0, None),
        access_scope=SCOPE_SHARED,
        rights_scope=SCOPE_SHARED,
        redistribution_grant=False,
        derivative_use_grant=False,
    )
    kwargs.update(over)
    return LicenseGrant(**kwargs)


def test_window_contains() -> None:
    assert window_contains(Window(T0, T2), T1)
    assert not window_contains(Window(T1, T2), T0)
    assert window_contains(Window(T0, None), T2)


def test_license_temporal_window() -> None:
    g = _grant(valid_window=Window(T0, T2), decision_window=Window(T0, None))
    assert_license_valid(g, valid_at=T1, decision_at=T1)
    with pytest.raises(LicenseError, match="valid window"):
        assert_license_valid(g, valid_at=T2, decision_at=T1)  # T2 == end (exclusive)
    with pytest.raises(LicenseError, match="expired"):
        assert_license_valid(_grant(expiry=T1), valid_at=T0, decision_at=T2)
    with pytest.raises(LicenseError, match="revoked"):
        assert_license_valid(_grant(revocation=T1), valid_at=T0, decision_at=T2)


def test_license_aware_egress_scope_and_grants() -> None:
    g = _grant(access_scope=SCOPE_SHARED)
    assert_egress_allowed(g, target_scope=SCOPE_SHARED)  # ok: equally restrictive
    assert_egress_allowed(g, target_scope=SCOPE_TENANT_PRIVATE)  # ok: more restrictive
    with pytest.raises(LicenseError, match="more permissive"):
        assert_egress_allowed(g, target_scope=SCOPE_PUBLIC)  # public > shared license
    with pytest.raises(LicenseError, match="redistribution"):
        assert_egress_allowed(g, target_scope=SCOPE_SHARED, is_redistribution=True)
    with pytest.raises(LicenseError, match="derivative"):
        assert_egress_allowed(g, target_scope=SCOPE_SHARED, is_derivative=True)
    assert_egress_allowed(
        _grant(redistribution_grant=True, derivative_use_grant=True),
        target_scope=SCOPE_SHARED,
        is_redistribution=True,
        is_derivative=True,
    )


def test_egress_gated_by_narrower_rights_scope() -> None:
    # M1: broad access_scope but narrow rights_scope -> egress ceiling is the more restrictive.
    g = _grant(access_scope=SCOPE_SHARED, rights_scope=SCOPE_TENANT_PRIVATE)
    assert_egress_allowed(
        g, target_scope=SCOPE_TENANT_PRIVATE
    )  # ok: meets the rights ceiling
    with pytest.raises(LicenseError, match="access/rights ceiling"):
        assert_egress_allowed(
            g, target_scope=SCOPE_SHARED
        )  # access ok but rights blocks


def test_egress_reevaluation_policies() -> None:
    assert (
        egress_reevaluation(
            EGRESS_TRACE_DECISION_TIME, issued_decision_at=T0, read_decision_at=T1
        )
        == "grandfathered"
    )
    assert (
        egress_reevaluation(
            EGRESS_READ_TIME, issued_decision_at=T0, read_decision_at=T1
        )
        == "reevaluate"
    )
    assert (
        egress_reevaluation(
            EGRESS_RETROACTIVE_RESTATEMENT, issued_decision_at=T0, read_decision_at=T1
        )
        == "restate"
    )
    with pytest.raises(LicenseError, match="unknown temporal_egress_policy"):
        egress_reevaluation("nope", issued_decision_at=T0, read_decision_at=T1)


# --- Layer 2: disposable-DB smoke ----------------------------------------------------


def _disposable_engine():
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    from src.database.init_db import init_db_for_engine

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    init_db_for_engine(engine)
    return engine


def test_disposable_db_4f_profiles_seeded() -> None:
    engine = _disposable_engine()
    try:
        with engine.begin() as conn:
            rows = dict(
                conn.execute(
                    text(
                        "SELECT schema_id, schema_hash FROM contract_schema_versions "
                        "WHERE schema_id IN (:c, :l)"
                    ),
                    {
                        "c": "sds:profile:consolidation-composition:v1",
                        "l": "sds:profile:license-rights-window:v1",
                    },
                ).all()
            )
        # both 4f-relevant ratified profiles are seeded and match the live registry.
        live = registry.profile_hashes()
        assert (
            rows["sds:profile:consolidation-composition:v1"]
            == live["sds:profile:consolidation-composition:v1"]
        )
        assert (
            rows["sds:profile:license-rights-window:v1"]
            == live["sds:profile:license-rights-window:v1"]
        )
    finally:
        engine.dispose()
