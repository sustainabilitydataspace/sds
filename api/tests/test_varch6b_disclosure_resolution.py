"""VARCH-6b contract tests — resolver authorization & disclosure resolution.

Pure deterministic core (resolver contract lines 147-149): steward/scope authorization +
no-retroactive-public-exposure (4f); disclosure policy + profile pin + REPRODUCED pinned
suppression outcome + adjacent-release/restatement-delta stability (4e); consolidation consent +
revocation rule + composition profile + composed disclosure/rights/recipient scope (4f). No DB.
"""

from __future__ import annotations

import pytest

from src.semantic import write_replay
from src.semantic.coverage import EVAL_PUBLIC, EVAL_SHARED, EVAL_TENANT
from src.semantic.profiles import registry
from src.semantic.resolver_disclosure import (
    ComposedConsolidation,
    DisclosurePolicy,
    SuppressionOutcomePin,
    resolve_effective_read_scope,
    validate_consolidation_resolution,
    validate_disclosure_resolution,
)
from src.semantic.write_disclosure import (
    REASON_COMPLEMENTARY,
    Cell,
    DisclosureError,
    DisclosureProfilePin,
    decide_suppression,
)
from src.semantic.write_scope import (
    ConsolidationError,
    DisclosureProfile,
    LicenseError,
    ScopeError,
)

_DPID = "sds:profile:aggregate-disclosure:v1"


def _pin() -> DisclosureProfilePin:
    e = registry.list_profiles()[_DPID]
    return DisclosureProfilePin(_DPID, e["version"], e["hash"])


# --- steward / scope authorization ----------------------------------------------------


def test_public_read_sees_public_after_widen_not_before() -> None:
    # row goes tenant_private -> shared at commit 5; a public read never sees it.
    with pytest.raises(ScopeError, match="not visible"):
        resolve_effective_read_scope(
            prior_scope="tenant_private",
            new_scope="shared",
            transition_decision_commit=5,
            read_decision_commit=9,
            row_tenant="t1",
            eval_scope=EVAL_PUBLIC,
        )


def test_no_retroactive_exposure_before_transition() -> None:
    # shared->public widen at 5; a read at 3 still sees shared (not public).
    eff = resolve_effective_read_scope(
        prior_scope="shared",
        new_scope="public",
        transition_decision_commit=5,
        read_decision_commit=3,
        row_tenant="t1",
        eval_scope=EVAL_SHARED,
    )
    assert eff == "shared"


def test_tenant_read_requires_steward_authority() -> None:
    with pytest.raises(ScopeError, match="not an authorized steward"):
        resolve_effective_read_scope(
            prior_scope="tenant_private",
            new_scope="tenant_private",
            transition_decision_commit=1,
            read_decision_commit=2,
            row_tenant="t1",
            eval_scope=EVAL_TENANT,
            eval_tenant="t1",
            authorized_steward_tenants=["t2"],
        )


def test_authorized_tenant_steward_sees_private_row() -> None:
    eff = resolve_effective_read_scope(
        prior_scope="tenant_private",
        new_scope="tenant_private",
        transition_decision_commit=1,
        read_decision_commit=2,
        row_tenant="t1",
        eval_scope=EVAL_TENANT,
        eval_tenant="t1",
        authorized_steward_tenants=["t1"],
    )
    assert eff == "tenant_private"


# --- disclosure resolution ------------------------------------------------------------

# group g1: a=5,b=1 (b below k=3 -> suppressed; complementary suppresses a). group g2: c=10,d=20.
_CELLS = [
    Cell("a", 5, "g1"),
    Cell("b", 1, "g1"),
    Cell("c", 10, "g2"),
    Cell("d", 20, "g2"),
]
_K = 3


def _policy(**over):
    kwargs = dict(
        policy_key="pk1", scope_context="public", profile_pin=_pin(), min_cohort_k=_K
    )
    kwargs.update(over)
    return DisclosurePolicy(**kwargs)


def _matching_pin(cells=_CELLS, k=_K, **over) -> SuppressionOutcomePin:
    result = decide_suppression(cells, k_threshold=k, profile_pin=_pin())
    released = sorted(d.cell_key for d in result.decisions if not d.suppressed)
    compl = sorted(
        d.cell_key for d in result.decisions if d.reason == REASON_COMPLEMENTARY
    )
    kwargs = dict(
        suppression_key="sk1",
        disclosure_profile_ref=_DPID,
        released_cell_set_hash=write_replay.canonical_json.content_hash(released),
        complementary_selection_hash=write_replay.canonical_json.content_hash(compl),
        suppression_decision="suppressed" if result.suppressed_keys() else "released",
    )
    kwargs.update(over)
    return SuppressionOutcomePin(**kwargs)


def test_disclosure_reproduces_pinned_outcome() -> None:
    res = validate_disclosure_resolution(
        _CELLS,
        policy=_policy(),
        suppression_pin=_matching_pin(),
        scope_context="public",
        is_first_release=True,
    )
    assert "b" in res.suppressed_keys() and "a" in res.suppressed_keys()


def test_disclosure_requires_prior_release_context() -> None:
    # neither previous_result nor is_first_release -> fail closed (M1).
    with pytest.raises(DisclosureError, match="prior-release"):
        validate_disclosure_resolution(
            _CELLS,
            policy=_policy(),
            suppression_pin=_matching_pin(),
            scope_context="public",
        )


def test_disclosure_rejects_scope_mismatch() -> None:
    with pytest.raises(DisclosureError, match="!= read scope"):
        validate_disclosure_resolution(
            _CELLS,
            policy=_policy(scope_context="shared"),
            suppression_pin=_matching_pin(),
            scope_context="public",
        )


def test_disclosure_rejects_released_hash_drift() -> None:
    with pytest.raises(DisclosureError, match="released-cell-set hash"):
        validate_disclosure_resolution(
            _CELLS,
            policy=_policy(),
            suppression_pin=_matching_pin(released_cell_set_hash="0" * 64),
            scope_context="public",
        )


def test_disclosure_rejects_complementary_hash_drift() -> None:
    with pytest.raises(DisclosureError, match="complementary-selection hash"):
        validate_disclosure_resolution(
            _CELLS,
            policy=_policy(),
            suppression_pin=_matching_pin(complementary_selection_hash="0" * 64),
            scope_context="public",
        )


def test_disclosure_rejects_decision_label_drift() -> None:
    with pytest.raises(DisclosureError, match="suppression_decision"):
        validate_disclosure_resolution(
            _CELLS,
            policy=_policy(),
            suppression_pin=_matching_pin(suppression_decision="released"),
            scope_context="public",
        )


def test_disclosure_rejects_pin_profile_mismatch() -> None:
    with pytest.raises(DisclosureError, match="pin profile"):
        validate_disclosure_resolution(
            _CELLS,
            policy=_policy(),
            suppression_pin=_matching_pin(disclosure_profile_ref="other:profile"),
            scope_context="public",
        )


def test_disclosure_adjacent_release_flip_rejected() -> None:
    prev = decide_suppression(_CELLS, k_threshold=_K, profile_pin=_pin())
    # current with b now above k -> b flips published; no restatement -> rejected.
    cells2 = [
        Cell("a", 5, "g1"),
        Cell("b", 9, "g1"),
        Cell("c", 10, "g2"),
        Cell("d", 20, "g2"),
    ]
    with pytest.raises(DisclosureError, match="differencing"):
        validate_disclosure_resolution(
            cells2,
            policy=_policy(),
            suppression_pin=_matching_pin(cells=cells2),
            scope_context="public",
            previous_result=prev,
        )


# --- consolidation resolution ---------------------------------------------------------

_M1 = DisclosureProfile("p1", rank=1, lattice="L")
_M2 = DisclosureProfile("p2", rank=2, lattice="L")
_SCOPES = [frozenset({"public", "shared"}), frozenset({"shared", "tenant_private"})]


def _disc_hash(profile_id="p2", rank=2, lattice="L"):
    return write_replay.canonical_json.content_hash(
        {"profile_id": profile_id, "rank": rank, "lattice": lattice}
    )


def _rights_hash(scope=("shared",), recipient=None):
    return write_replay.canonical_json.content_hash(
        {"composed_license_scope": sorted(scope), "allowed_recipient_scope": recipient}
    )


def _consolidate(*, auto_hashes=True, **over):
    kwargs = dict(
        member_consents={"t1": "granted", "t2": "granted"},
        composition_kind="most_restrictive",
        member_disclosure_profiles=[_M1, _M2],
        member_license_scopes=_SCOPES,
        declared_outcome="published",
    )
    kwargs.update(over)
    # a published decision must pin both composed hashes; compute the correct ones by default
    # so success tests pass, unless a test overrides or opts out via auto_hashes=False.
    if auto_hashes and kwargs["declared_outcome"] == "published":
        kwargs.setdefault("composed_disclosure_outcome_hash", _disc_hash())
        kwargs.setdefault(
            "composed_rights_decision_hash",
            _rights_hash(recipient=kwargs.get("allowed_recipient_scope")),
        )
    return validate_consolidation_resolution(**kwargs)


def test_consolidation_published_composes_most_restrictive() -> None:
    out = _consolidate()
    assert isinstance(out, ComposedConsolidation)
    assert out.composed_disclosure.profile_id == "p2"  # rank 2 wins
    assert out.composed_license_scope == frozenset({"shared"})  # intersection


def test_consolidation_withdrawn_member_blocks() -> None:
    with pytest.raises(ConsolidationError, match="withdrawn"):
        _consolidate(member_consents={"t1": "granted", "t2": "withdrawn"})


def test_consolidation_withdrawn_consistent_with_blocked() -> None:
    assert (
        _consolidate(
            member_consents={"t1": "granted", "t2": "withdrawn"},
            declared_outcome="blocked",
        )
        is None
    )


def test_consolidation_pending_member_blocks() -> None:
    with pytest.raises(ConsolidationError, match="did not authorize"):
        _consolidate(member_consents={"t1": "granted", "t2": "pending"})


def test_consolidation_rejects_unknown_consent_status() -> None:
    with pytest.raises(ConsolidationError, match="unknown consent_status"):
        _consolidate(member_consents={"t1": "granted", "t2": "bogus"})


def test_consolidation_incomparable_lattices_block() -> None:
    other = DisclosureProfile("p3", rank=1, lattice="OTHER")
    with pytest.raises(ConsolidationError, match="incomparable"):
        _consolidate(member_disclosure_profiles=[_M1, other])


def test_consolidation_empty_license_intersection_blocks() -> None:
    with pytest.raises(ConsolidationError, match="empty license"):
        _consolidate(
            member_license_scopes=[frozenset({"public"}), frozenset({"tenant_private"})]
        )


def test_consolidation_group_governing_requires_group_profile() -> None:
    with pytest.raises(ConsolidationError, match="requires a group disclosure profile"):
        _consolidate(composition_kind="group_level_governing")


def test_consolidation_blocked_claim_but_composes_is_inconsistent() -> None:
    with pytest.raises(ConsolidationError, match="inconsistent decision"):
        _consolidate(declared_outcome="blocked")


def test_consolidation_recipient_scope_must_be_within_composed() -> None:
    with pytest.raises(LicenseError, match="not within the composed"):
        _consolidate(allowed_recipient_scope="public")  # composed is {shared}


def test_consolidation_recipient_scope_within_composed_ok() -> None:
    out = _consolidate(allowed_recipient_scope="shared")
    assert out.allowed_recipient_scope == "shared"


def test_consolidation_reproduces_composed_hashes() -> None:
    out = _consolidate(
        composed_disclosure_outcome_hash=_disc_hash(),
        composed_rights_decision_hash=_rights_hash(),
    )
    assert out.composed_disclosure.profile_id == "p2"


def test_consolidation_rejects_composed_hash_drift() -> None:
    with pytest.raises(
        ConsolidationError, match="composed_disclosure_outcome_hash drift"
    ):
        _consolidate(composed_disclosure_outcome_hash="0" * 64)


def test_consolidation_custom_kind_blocked() -> None:
    # M2: 'custom' has no deterministic evaluator -> fail closed.
    with pytest.raises(ConsolidationError, match="no deterministic evaluator"):
        _consolidate(composition_kind="custom")


def test_consolidation_published_requires_both_hashes() -> None:
    # M4: a published decision missing the composed hashes is rejected.
    with pytest.raises(ConsolidationError, match="must pin BOTH"):
        _consolidate(auto_hashes=False)


def test_consolidation_recipient_scope_drift_rejected() -> None:
    # M3: rights hash binds the recipient scope; a recipient drift cannot pass.
    with pytest.raises(ConsolidationError, match="composed_rights_decision_hash drift"):
        _consolidate(
            allowed_recipient_scope="shared",
            composed_rights_decision_hash=_rights_hash(
                recipient=None
            ),  # wrong recipient
        )
