"""VARCH-5d contract tests — effective-version-set (EVS) assembly validation.

Pure deterministic core: bitemporal EVS window (4a); FULL replay-manifest validation (4c) +
EVS<->manifest reference match; member integrity (same scope_context as parent, belongs to the
EVS, known subject_kind, each subject resolved once, resolved_version_id must be a PUBLISHED
version of that subject per a resolver-materialized index); optional slice-coverage-completeness;
evs_hash bound to the canonical EVS digest (identity + members); successor planning. No DB layer.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.semantic.profiles import registry
from src.semantic.write_evs import (
    EffectiveVersionSet,
    EVSError,
    EVSMember,
    canonical_evs_digest,
    plan_evs_successor,
    validate_evs,
)
from src.semantic.write_replay import EVALUATION_ORDER

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_SCOPE = "public"
_MANIFEST_ID = "rm:evs:1"
_SUBJECT = ("canonical_concept", "syg:WastePlastic")
_RVI = {_SUBJECT: frozenset({"ax-v1", "ax-v2"})}


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
        decision_commit_id=10,
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


def _validate(evs, members, **over):
    kwargs = dict(replay_manifest=_manifest(), resolved_version_index=_RVI)
    kwargs.update(over)
    validate_evs(evs, members, **kwargs)


def test_valid_evs_passes() -> None:
    _validate(_evs(), [_member()])


def test_rejects_manifest_ref_mismatch() -> None:
    with pytest.raises(EVSError, match="replay_manifest_ref"):
        _validate(_evs(replay_manifest_ref="rm:other"), [_member()])


def test_rejects_invalid_manifest() -> None:
    bad = _manifest()
    bad["evaluation_order"] = list(reversed(EVALUATION_ORDER))  # schema violation
    with pytest.raises(Exception):  # ReplayValidationError
        _validate(_evs(), [_member()], replay_manifest=bad)


def test_rejects_member_wrong_scope() -> None:
    with pytest.raises(EVSError, match="scope_context"):
        _validate(_evs(), [_member(scope_context="tenant_private")])


def test_rejects_member_wrong_evs() -> None:
    with pytest.raises(EVSError, match="belongs to EVS"):
        _validate(_evs(), [_member(effective_version_set_id="other")])


def test_rejects_unknown_subject_kind() -> None:
    with pytest.raises(EVSError, match="subject_kind"):
        _validate(_evs(), [_member(subject_kind="bogus")])


def test_rejects_unpublished_resolved_version() -> None:
    # M2: a non-empty but non-published version id is rejected.
    with pytest.raises(EVSError, match="published version"):
        _validate(_evs(), [_member(resolved_version_id="ax-vX")])


def test_rejects_duplicate_subject() -> None:
    with pytest.raises(EVSError, match="more than once"):
        _validate(_evs(), [_member(), _member(resolved_version_id="ax-v2")])


def test_rejects_nonpositive_decision_commit() -> None:
    with pytest.raises(EVSError, match="decision_commit_id"):
        _validate(_evs(decision_commit_id=0), [_member()])


def test_completeness_missing_and_extra() -> None:
    # M3: when expected_subjects is given, member set must equal it exactly.
    extra = ("standard_datapoint", "x:1")
    with pytest.raises(EVSError, match="missing"):
        _validate(_evs(), [_member()], expected_subjects={_SUBJECT, extra})
    with pytest.raises(EVSError, match="extra"):
        _validate(_evs(), [_member()], expected_subjects=set())


def test_completeness_exact_passes() -> None:
    _validate(_evs(), [_member()], expected_subjects={_SUBJECT})


def test_evs_digest_binds_identity_and_members() -> None:
    e = _evs()
    m1 = _member(subject_ref="a", resolved_version_id="v1")
    m2 = _member(subject_ref="b", resolved_version_id="v2")
    assert canonical_evs_digest(e, [m1, m2]) == canonical_evs_digest(e, [m2, m1])
    # different members -> different digest
    assert canonical_evs_digest(e, [m1, m2]) != canonical_evs_digest(
        e, [m1, _member(subject_ref="b", resolved_version_id="v3")]
    )
    # different EVS key -> different digest (no cross-key collision)
    assert canonical_evs_digest(e, [m1]) != canonical_evs_digest(
        _evs(evs_key="other"), [m1]
    )


def test_evs_hash_must_equal_digest() -> None:
    # M1: evs_hash must equal the canonical digest over identity + members.
    e0 = _evs()
    good = canonical_evs_digest(e0, [_member()])
    _validate(_evs(evs_hash=good), [_member()])
    with pytest.raises(EVSError, match="canonical digest"):
        _validate(_evs(evs_hash="0" * 64), [_member()])


def test_evs_successor_plan_composes_4a() -> None:
    current = _evs(id="evs1", decision_commit_id=10)
    proposed = _evs(id="evs2", decision_commit_id=11)
    plan = plan_evs_successor(current, proposed, published_siblings=[current])
    assert plan.supersede is not None and plan.supersede.successor_version_id == "evs2"


def test_evs_first_version_is_insert_only() -> None:
    assert plan_evs_successor(None, _evs()).is_first_version
