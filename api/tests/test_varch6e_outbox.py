"""VARCH-6e contract tests — resolver restatement, outbox & private resolution.

Pure deterministic core (resolver contract lines 163-164): restatement policy planning;
license expiry/revocation -> restatement/tombstone (4f egress_reevaluation); scope-projected/
disclosure-filtered/license-aware idempotent outbox events (4d leak guard + 4f egress + 4c hash);
private payload/commitment resolution through authorization + key lineage + erasure (4d). No DB.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.semantic import write_replay
from src.semantic.resolver_outbox import (
    ACTION_NONE,
    ACTION_REEVALUATE,
    ACTION_RESTATE,
    ACTION_TOMBSTONE,
    RestatementError,
    assert_public_projection_safe,
    assert_tombstone_reason,
    build_outbox_event,
    decide_license_change_action,
    plan_restatement,
    resolve_private_payload,
    tombstone_required,
)
from src.semantic.write_commitment import CommitmentError, construct_commitment
from src.semantic.write_scope import LicenseError, LicenseGrant, Window

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=100)
T2 = T0 + timedelta(days=200)


# --- restatement planning -------------------------------------------------------------


def test_plan_freeze_has_no_successor() -> None:
    plan = plan_restatement(
        restatement_policy="freeze_prior_trace", prior_trace_ref="t:1"
    )
    assert plan.successor_trace_ref is None and plan.prior_disposition == "frozen"


def test_plan_freeze_rejects_successor() -> None:
    with pytest.raises(RestatementError, match="no successor"):
        plan_restatement(
            restatement_policy="freeze_prior_trace",
            prior_trace_ref="t:1",
            successor_trace_ref="t:2",
        )


def test_plan_supersede_requires_successor() -> None:
    with pytest.raises(RestatementError, match="requires a successor"):
        plan_restatement(restatement_policy="supersede_trace", prior_trace_ref="t:1")


def test_plan_supersede_links_successor() -> None:
    plan = plan_restatement(
        restatement_policy="supersede_trace",
        prior_trace_ref="t:1",
        successor_trace_ref="t:2",
    )
    assert plan.successor_trace_ref == "t:2" and plan.prior_disposition == "superseded"


def test_plan_rejects_self_successor() -> None:
    with pytest.raises(RestatementError, match="must differ"):
        plan_restatement(
            restatement_policy="recompute_trace",
            prior_trace_ref="t:1",
            successor_trace_ref="t:1",
        )


def test_plan_rejects_unknown_policy() -> None:
    with pytest.raises(RestatementError, match="unknown restatement_policy"):
        plan_restatement(restatement_policy="bogus", prior_trace_ref="t:1")


# --- license change -> action ---------------------------------------------------------


def test_revocation_always_tombstones() -> None:
    action, reason = decide_license_change_action(
        change="revocation",
        temporal_egress_policy="trace_decision_time",  # ignored for revocation
        issued_decision_at=T0,
        read_decision_at=T2,
    )
    assert action == ACTION_TOMBSTONE and reason == "license_revocation"


def test_expiry_retroactive_restatement_restates() -> None:
    action, reason = decide_license_change_action(
        change="expiry",
        temporal_egress_policy="retroactive_restatement",
        issued_decision_at=T0,
        read_decision_at=T2,
    )
    assert action == ACTION_RESTATE and reason is None


def test_expiry_read_time_reevaluates() -> None:
    action, _ = decide_license_change_action(
        change="expiry",
        temporal_egress_policy="egress_read_time",
        issued_decision_at=T0,
        read_decision_at=T2,
    )
    assert action == ACTION_REEVALUATE


def test_expiry_trace_decision_time_grandfathers() -> None:
    action, _ = decide_license_change_action(
        change="expiry",
        temporal_egress_policy="trace_decision_time",
        issued_decision_at=T0,
        read_decision_at=T2,  # read after issuance -> grandfathered
    )
    assert action == ACTION_NONE


def test_unknown_change_rejected() -> None:
    with pytest.raises(RestatementError, match="unknown license change"):
        decide_license_change_action(
            change="renewal",
            temporal_egress_policy="egress_read_time",
            issued_decision_at=T0,
            read_decision_at=T2,
        )


# --- tombstone reasons ----------------------------------------------------------------


def test_tombstone_reason_enum() -> None:
    assert_tombstone_reason("erasure")
    with pytest.raises(RestatementError, match="unknown tombstone_reason"):
        assert_tombstone_reason("whatever")


def test_tombstone_required_for_revocation_not_for_other() -> None:
    assert tombstone_required("consent_withdrawal") is True
    assert tombstone_required("license_revocation") is True
    assert tombstone_required("erasure") is True
    assert tombstone_required("other") is False


# --- outbox events --------------------------------------------------------------------


def _grant(**over):
    kwargs = dict(
        valid_window=Window(T0, T2),
        decision_window=Window(T0, T2),
        access_scope="shared",
        rights_scope="shared",
        redistribution_grant=True,  # outbox delivery is redistribution
    )
    kwargs.update(over)
    return LicenseGrant(**kwargs)


def _event(**over):
    kwargs = dict(
        restatement_id="r:1",
        subscriber_ref="sub:1",
        decision_commit_id=10,
        event_schema_version="evt:v1",
        payload={"subject": "x", "delta": "restated"},
        allowed_public_fields=["subject", "delta"],
        license_grant=_grant(),
        subscriber_scope="shared",
        valid_at=T1,
        decision_at=T1,
    )
    kwargs.update(over)
    return build_outbox_event(**kwargs)


def _identity_hash(payload, **over):
    base = dict(
        restatement_id="r:1",
        subscriber_ref="sub:1",
        decision_commit_id=10,
        event_schema_version="evt:v1",
        payload=payload,
    )
    base.update(over)
    return write_replay.canonical_json.content_hash(base)


def test_outbox_event_built_and_hashed() -> None:
    ev = _event()
    expected = _identity_hash({"subject": "x", "delta": "restated"})
    assert ev.event_hash == expected
    assert ev.idempotency_key == expected  # M1: key IS the canonical identity


def test_outbox_idempotency_key_is_deterministic() -> None:
    # M1: the same logical event always yields the same key, regardless of build.
    assert _event().idempotency_key == _event().idempotency_key


def test_outbox_rejects_mismatched_supplied_idempotency_key() -> None:
    with pytest.raises(
        RestatementError, match="does not match the canonical event identity"
    ):
        _event(idempotency_key="not-the-derived-key")


def test_outbox_requires_positive_decision_commit() -> None:
    with pytest.raises(RestatementError, match="decision_commit_id"):
        _event(decision_commit_id=0)


def test_outbox_rejects_denylisted_private_field() -> None:
    with pytest.raises(CommitmentError, match="leaks private field"):
        _event(
            payload={"subject": "x", "commitment": "deadbeef"},
            allowed_public_fields=["subject", "commitment"],
        )


def test_outbox_allowlist_blocks_unlisted_field() -> None:
    # M2: a private field NOT in the denylist is still blocked by the allowlist.
    with pytest.raises(CommitmentError, match="not in the public allowlist"):
        _event(
            payload={"subject": "x", "secret_raw": "y"},
            allowed_public_fields=["subject"],
        )


def test_outbox_rejects_overbroad_subscriber_scope() -> None:
    with pytest.raises(LicenseError):
        _event(subscriber_scope="public")  # license is shared/shared


def test_outbox_rejects_missing_redistribution_grant() -> None:
    # M3: scope matches but the license does not grant redistribution -> fail closed.
    with pytest.raises(LicenseError, match="redistribution"):
        _event(license_grant=_grant(redistribution_grant=False))


def test_outbox_rejects_expired_license() -> None:
    with pytest.raises(LicenseError):
        _event(license_grant=_grant(expiry=T0 + timedelta(days=1)))


# --- private payload resolution -------------------------------------------------------

_KEY = b"k" * 32
_SALT = b"s" * 16
_PAYLOAD = {"value": "42.0"}


def _record(payload=_PAYLOAD):
    return construct_commitment(
        _KEY, payload, key_id="kid:1", key_generation="g1", salt=_SALT
    )


def test_private_payload_resolves_for_authorized_tenant() -> None:
    rec = _record()
    out = resolve_private_payload(
        rec, _KEY, _PAYLOAD, requester_tenant="t1", owner_tenant="t1"
    )
    assert out == _PAYLOAD


def test_private_payload_rejects_unauthorized_tenant() -> None:
    rec = _record()
    with pytest.raises(RestatementError, match="not authorized"):
        resolve_private_payload(
            rec, _KEY, _PAYLOAD, requester_tenant="t2", owner_tenant="t1"
        )


def test_private_payload_fails_closed_after_erasure() -> None:
    rec = _record()
    # key shredded (None) -> erasure model: unverifiable, fail closed.
    with pytest.raises(CommitmentError, match="shredded"):
        resolve_private_payload(
            rec, None, _PAYLOAD, requester_tenant="t1", owner_tenant="t1"
        )


def test_private_payload_rejects_tampered_payload() -> None:
    rec = _record()
    with pytest.raises(CommitmentError, match="verification failed"):
        resolve_private_payload(
            rec, _KEY, {"value": "99.0"}, requester_tenant="t1", owner_tenant="t1"
        )


def test_private_payload_rejects_drifted_profile_binding() -> None:
    # M4: a record claiming a drifted canonicalization binding fails closed at the boundary.
    rec = replace(_record(), canonicalization_binding="sds-canonical-json-v0")
    with pytest.raises(RestatementError, match="profile drift"):
        resolve_private_payload(
            rec, _KEY, _PAYLOAD, requester_tenant="t1", owner_tenant="t1"
        )


def test_public_projection_safe_rejects_private_field() -> None:
    with pytest.raises(CommitmentError, match="leaks private field"):
        assert_public_projection_safe({"ok": 1, "payload_ciphertext": "x"})


def test_public_projection_safe_passes_clean() -> None:
    assert_public_projection_safe({"subject": "x", "count": 5})
