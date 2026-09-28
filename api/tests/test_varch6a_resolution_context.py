"""VARCH-6a contract tests — resolution context & effective-version-set selection.

Pure deterministic core (resolver contract lines 139-146): resolve the bitemporal read
coordinates (reporting>measurement valid anchor; trace>request>latest decision anchor, with
'latest' allowed ONLY for a new calculation); validate the decision anchor is a committed member
of the fenced global commit order (4b); select+fully-validate the slice EVS (5d) or signal
SNAPSHOT_REQUIRED; reject overlap/gap in a publication chain (4a) and cycles/dangling edges in
the relation graph. No DB layer.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.semantic.profiles import registry
from src.semantic.resolver import (
    SNAPSHOT_REQUIRED,
    PublicationInterval,
    RelationEdge,
    ResolutionRequest,
    ResolverError,
    resolve_decision_context,
    select_effective_version_set,
    validate_publication_chain,
    validate_relation_graph,
)
from src.semantic.write_evs import EffectiveVersionSet, EVSMember, canonical_evs_digest
from src.semantic.write_replay import EVALUATION_ORDER

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=365)
T2 = T1 + timedelta(days=365)
_SCOPE = "public"
_MANIFEST_ID = "rm:evs:1"
_SUBJECT = ("canonical_concept", "syg:WastePlastic")
_RVI = {_SUBJECT: frozenset({"ax-v1", "ax-v2"})}

# A small fenced commit chain: 1 (genesis) -> 2 -> 3, and its replayed order.
_CHAIN = [(1, None), (2, 1), (3, 2)]
_ORDER = (1, 2, 3)


def _manifest(manifest_id=_MANIFEST_ID):
    h = registry.profile_hashes()

    def ref(pid):
        return {"profile_id": pid, "version": "v1", "hash": h[pid]}

    return {
        "manifest_id": manifest_id,
        "manifest_version": "v1",
        "evaluation_order": list(EVALUATION_ORDER),
        "profile_tuple": {
            "canonical_serialization_profile": ref("sds-canonical-json-v1"),
            "computation_profile": ref("sds-computation-profile-v1"),
            "private_commitment_profile": ref("sds:profile:private-commitment:v1"),
            "aggregate_disclosure_profile": ref("sds:profile:aggregate-disclosure:v1"),
            "license_rights_window": ref("sds:profile:license-rights-window:v1"),
        },
        "transition_policy": "fail_closed_require_explicit_transition",
    }


def _evs(**over):
    kwargs = dict(
        id="evs1",
        evs_key="evsk:1",
        scope_context=_SCOPE,
        replay_manifest_ref=_MANIFEST_ID,
        valid_from=T0,
        valid_to=T2,
        decision_commit_id=2,
    )
    kwargs.update(over)
    return EffectiveVersionSet(**kwargs)


def _member(**over):
    kwargs = dict(
        effective_version_set_id="evs1",
        scope_context=_SCOPE,
        subject_kind="canonical_concept",
        subject_ref="syg:WastePlastic",
        resolved_version_id="ax-v1",
    )
    kwargs.update(over)
    return EVSMember(**kwargs)


def _req(**over):
    kwargs = dict(scope_context=_SCOPE, reporting_period=T1, trace_decision_commit_id=2)
    kwargs.update(over)
    return ResolutionRequest(**kwargs)


def _resolve(**over):
    req = _req(**over)
    return resolve_decision_context(req, commit_chain=_CHAIN)


# --- resolution context ---------------------------------------------------------------


def test_resolves_reporting_over_measurement() -> None:
    ctx = resolve_decision_context(
        ResolutionRequest(
            scope_context=_SCOPE,
            reporting_period=T1,
            measurement_period=T0,
            request_decision_commit_id=3,
        ),
        commit_chain=_CHAIN,
    )
    assert ctx.valid_as_of == T1  # reporting wins
    assert ctx.decision_commit_id == 3
    assert ctx.scope_context == _SCOPE


def test_decision_anchor_precedence_trace_wins() -> None:
    ctx = _resolve(trace_decision_commit_id=1, request_decision_commit_id=3)
    assert ctx.decision_commit_id == 1


def test_new_calculation_falls_through_to_latest() -> None:
    ctx = resolve_decision_context(
        ResolutionRequest(
            scope_context=_SCOPE, reporting_period=T1, is_new_calculation=True
        ),
        commit_chain=_CHAIN,
        latest_committed_commit_id=3,
    )
    assert ctx.decision_commit_id == 3


def test_plain_read_may_not_fall_through_to_latest() -> None:
    with pytest.raises(ResolverError, match="never fall through"):
        resolve_decision_context(
            ResolutionRequest(scope_context=_SCOPE, reporting_period=T1),
            commit_chain=_CHAIN,
        )


def test_rejects_missing_valid_anchor() -> None:
    with pytest.raises(ResolverError, match="valid-time anchor"):
        resolve_decision_context(
            ResolutionRequest(scope_context=_SCOPE, trace_decision_commit_id=2),
            commit_chain=_CHAIN,
        )


def test_rejects_naive_valid_anchor() -> None:
    with pytest.raises(ResolverError, match="timezone-aware"):
        _resolve(reporting_period=datetime(2026, 6, 1))  # naive


def test_rejects_decision_not_in_sequence() -> None:
    with pytest.raises(ResolverError, match="not a member"):
        _resolve(trace_decision_commit_id=99)


def test_rejects_reading_uncommitted_future_decision() -> None:
    with pytest.raises(ResolverError, match="uncommitted decision"):
        resolve_decision_context(
            _req(trace_decision_commit_id=3),
            commit_chain=_CHAIN,
            latest_committed_commit_id=2,
        )


def test_rejects_forked_commit_chain() -> None:
    # 2 and 3 both claim predecessor 1 -> fork (4b replay_order rejects).
    with pytest.raises(Exception):  # SequencerError
        resolve_decision_context(
            _req(trace_decision_commit_id=2), commit_chain=[(1, None), (2, 1), (3, 1)]
        )


# --- EVS selection --------------------------------------------------------------------


def _sel(ctx, *, members=None, evs_over=None, hashed=True, commit_order=_ORDER, **kw):
    """Select an EVS with a correctly-bound content hash (unless ``hashed=False``)."""
    members = [_member()] if members is None else members
    base = _evs(**(evs_over or {}))
    evs = (
        replace(base, evs_hash=canonical_evs_digest(base, members)) if hashed else base
    )
    return select_effective_version_set(
        ctx,
        candidate_evs=evs,
        members=members,
        replay_manifest=_manifest(),
        resolved_version_index=_RVI,
        commit_order=commit_order,
        **kw,
    )


def test_snapshot_required_when_no_evs() -> None:
    ctx = _resolve()
    assert select_effective_version_set(ctx, candidate_evs=None) == SNAPSHOT_REQUIRED


def test_selects_valid_evs() -> None:
    assert _sel(_resolve()).id == "evs1"


def test_rejects_evs_scope_mismatch() -> None:
    with pytest.raises(ResolverError, match="scope_context"):
        _sel(_resolve(), evs_over={"scope_context": "tenant_private"})


def test_rejects_superseded_evs() -> None:
    with pytest.raises(ResolverError, match="not published"):
        _sel(_resolve(), evs_over={"status": "superseded"})


def test_rejects_evs_without_content_hash() -> None:
    with pytest.raises(ResolverError, match="content hash"):
        _sel(_resolve(), hashed=False)


def test_rejects_evs_not_effective_at_valid_as_of() -> None:
    ctx = _resolve(reporting_period=T2 + timedelta(days=1))
    with pytest.raises(ResolverError, match="does not contain valid_as_of"):
        _sel(ctx)


def test_rejects_evs_decided_after_read() -> None:
    # EVS decided at commit 2, read at commit 1 -> ordered after the read.
    ctx = _resolve(trace_decision_commit_id=1)
    with pytest.raises(ResolverError, match="ordered after the read"):
        _sel(ctx, evs_over={"decision_commit_id": 2})


def test_rejects_evs_decision_not_in_fenced_order() -> None:
    with pytest.raises(ResolverError, match="not a member"):
        _sel(_resolve(), evs_over={"decision_commit_id": 99})


def test_requires_commit_order() -> None:
    with pytest.raises(ResolverError, match="commit_order"):
        _sel(_resolve(), commit_order=())


def test_rejects_evs_without_validation_inputs() -> None:
    ctx = _resolve()
    with pytest.raises(ResolverError, match="without its replay_manifest"):
        select_effective_version_set(ctx, candidate_evs=_evs())


# --- publication chain ----------------------------------------------------------------


def _iv(vid, vf, vt, status="published"):
    return PublicationInterval(
        version_id=vid,
        logical_key="k",
        valid_from=vf,
        valid_to=vt,
        decision_commit_id=2,
        status=status,
    )


def test_publication_chain_no_overlap_passes() -> None:
    validate_publication_chain([_iv("a", T0, T1), _iv("b", T1, T2)])


def test_publication_chain_rejects_overlap() -> None:
    with pytest.raises(ResolverError, match="overlapping published"):
        validate_publication_chain([_iv("a", T0, T2), _iv("b", T1, None)])


def test_publication_chain_superseded_rows_ignored() -> None:
    # an overlapping superseded row does not count against the published chain.
    validate_publication_chain(
        [_iv("a", T0, T2), _iv("b", T1, T2, status="superseded")]
    )


def test_publication_chain_gap_rejected_when_contiguous_required() -> None:
    with pytest.raises(ResolverError, match="gap"):
        validate_publication_chain(
            [_iv("a", T0, T1), _iv("b", T1 + timedelta(days=1), T2)],
            require_contiguous=True,
        )


def test_publication_chain_open_tail_ok_when_contiguous() -> None:
    validate_publication_chain(
        [_iv("a", T0, T1), _iv("b", T1, None)], require_contiguous=True
    )


def test_publication_chain_rejects_multiple_keys() -> None:
    other = PublicationInterval("c", "other", T0, T1, 2)
    with pytest.raises(ResolverError, match="one logical key"):
        validate_publication_chain([_iv("a", T0, T1), other])


def test_publication_chain_covers_point_passes() -> None:
    mid = T0 + timedelta(days=10)
    validate_publication_chain([_iv("a", T0, T1), _iv("b", T1, T2)], covers_point=mid)


def test_publication_chain_covers_point_gap_rejected() -> None:
    # read point falls in a hole between two sparse published rows.
    hole = T1 + timedelta(hours=12)
    with pytest.raises(ResolverError, match="gap at read slice"):
        validate_publication_chain(
            [_iv("a", T0, T1), _iv("b", T1 + timedelta(days=1), T2)],
            covers_point=hole,
        )


def test_publication_chain_covers_point_empty_rejected() -> None:
    with pytest.raises(ResolverError, match="gap at read slice"):
        validate_publication_chain([], covers_point=T1)


def test_publication_chain_sparse_ok_without_covers_point() -> None:
    # a legitimately sparse chain (hole away from any read point) is fine by default.
    validate_publication_chain([_iv("a", T0, T1), _iv("b", T1 + timedelta(days=1), T2)])


# --- relation graph -------------------------------------------------------------------


def test_relation_graph_acyclic_passes() -> None:
    validate_relation_graph(
        [RelationEdge("a", "b"), RelationEdge("b", "c")],
        in_slice_nodes=["a", "b", "c"],
    )


def test_relation_graph_rejects_cycle() -> None:
    with pytest.raises(ResolverError, match="cycle"):
        validate_relation_graph(
            [RelationEdge("a", "b"), RelationEdge("b", "a")],
            in_slice_nodes=["a", "b"],
        )


def test_relation_graph_rejects_dangling_edge() -> None:
    with pytest.raises(ResolverError, match="not an in-slice"):
        validate_relation_graph([RelationEdge("a", "z")], in_slice_nodes=["a", "b"])
