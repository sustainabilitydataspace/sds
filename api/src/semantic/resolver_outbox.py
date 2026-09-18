"""Resolver — restatement, outbox & private resolution (VARCH-6e).

Step 6 (resolver), sub-slice (e): the restatement / outbox / private-payload stage of the
deterministic resolver pipeline (candidate-v14 resolver contract lines 163-164). Runs after 6d
(numeric evaluation) and before 6f (trace-pin assembly). Pure + deterministic; the actual outbox
INSERT and KMS key material are the VARCH-8 write/KMS seam.

It COMPOSES the converged cores:

* :mod:`src.semantic.write_scope` (4f) — ``egress_reevaluation`` maps a license expiry/revocation
  to a grandfather / reevaluate / restate action; ``assert_license_valid`` + ``assert_egress_allowed``
  make the outbox license-aware (an event is never emitted to a scope the license forbids).
* :mod:`src.semantic.write_commitment` (4d) — ``verify_commitment`` (fail-closed after key shred =
  the erasure model) + ``assert_no_private_leak`` for the private payload / public projection.
* :mod:`src.semantic.write_replay` (4c) — canonical content hashing for the outbox ``event_hash``.

Vocabularies bound to the persisted CHECK constraints (semantic_models.py):
``semantic_restatement_events.restatement_policy`` {freeze_prior_trace, supersede_trace,
recompute_trace}; ``semantic_tombstones.tombstone_reason`` {consent_withdrawal, license_revocation,
erasure, restatement, other}.

Resolver-contract steps covered here:
    apply restatement policy and the scope-projected/disclosure-filtered/license-aware outbox,
      including license expiry/revocation restatements or tombstones when policy requires
    resolve private payload/commitment through authorization, commitment profile, key lineage,
      and the erasure model

Gates contributed here (mechanism; CI wiring in VARCH-10): restatement-policy-plan,
license-change-restatement/tombstone, outbox-projection-safety + idempotency + license-aware
egress, private-payload-authorization + erasure-fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from src.semantic import write_replay
from src.semantic.write_commitment import (
    ALGORITHMS,
    CANONICAL_BINDING,
    CommitmentRecord,
    assert_no_private_leak,
    verify_commitment,
)
from src.semantic.write_scope import (
    LicenseGrant,
    assert_egress_allowed,
    assert_license_valid,
    egress_reevaluation,
)

# semantic_restatement_events.restatement_policy (closed enum in the table CHECK).
RESTATEMENT_FREEZE = "freeze_prior_trace"
RESTATEMENT_SUPERSEDE = "supersede_trace"
RESTATEMENT_RECOMPUTE = "recompute_trace"
RESTATEMENT_POLICIES = frozenset(
    {RESTATEMENT_FREEZE, RESTATEMENT_SUPERSEDE, RESTATEMENT_RECOMPUTE}
)

# semantic_tombstones.tombstone_reason (closed enum in the table CHECK).
TOMBSTONE_REASONS = frozenset(
    {"consent_withdrawal", "license_revocation", "erasure", "restatement", "other"}
)
# reasons that MANDATE a tombstone (the subject/trace is retroactively pulled).
TOMBSTONE_REQUIRED_REASONS = frozenset(
    {"consent_withdrawal", "license_revocation", "erasure"}
)

# license-change actions (downstream of 4f egress_reevaluation).
ACTION_NONE = "none"
ACTION_REEVALUATE = "reevaluate"
ACTION_RESTATE = "restate"
ACTION_TOMBSTONE = "tombstone"


class RestatementError(ValueError):
    """Raised on a restatement / outbox / private-resolution violation."""


# --- restatement planning -------------------------------------------------------------


@dataclass(frozen=True)
class RestatementPlan:
    policy: str
    prior_trace_ref: str
    successor_trace_ref: str | None
    prior_disposition: str  # 'frozen' | 'superseded'


def plan_restatement(
    *,
    restatement_policy: str,
    prior_trace_ref: str,
    successor_trace_ref: str | None = None,
) -> RestatementPlan:
    """Plan a restatement per the declared policy (fail-closed on shape mismatch).

    * ``freeze_prior_trace`` — the prior trace is frozen as historical; there is NO successor
      (supplying one is an error).
    * ``supersede_trace`` / ``recompute_trace`` — require a DISTINCT successor trace; the prior
      trace is superseded and linked to the successor.
    """
    if restatement_policy not in RESTATEMENT_POLICIES:
        raise RestatementError(f"unknown restatement_policy {restatement_policy!r}")
    if not prior_trace_ref:
        raise RestatementError("prior_trace_ref is required")

    if restatement_policy == RESTATEMENT_FREEZE:
        if successor_trace_ref is not None:
            raise RestatementError("freeze_prior_trace produces no successor trace")
        return RestatementPlan(
            policy=restatement_policy,
            prior_trace_ref=prior_trace_ref,
            successor_trace_ref=None,
            prior_disposition="frozen",
        )

    if not successor_trace_ref:
        raise RestatementError(f"{restatement_policy} requires a successor_trace_ref")
    if successor_trace_ref == prior_trace_ref:
        raise RestatementError("successor_trace_ref must differ from prior_trace_ref")
    return RestatementPlan(
        policy=restatement_policy,
        prior_trace_ref=prior_trace_ref,
        successor_trace_ref=successor_trace_ref,
        prior_disposition="superseded",
    )


# --- license expiry / revocation -> restatement or tombstone --------------------------


def decide_license_change_action(
    *,
    change: str,  # 'expiry' | 'revocation'
    temporal_egress_policy: str,
    issued_decision_at: datetime,
    read_decision_at: datetime,
) -> tuple[str, str | None]:
    """Decide the action when an applied license later expires or is revoked.

    A REVOCATION always pulls already-issued output: it requires a tombstone with reason
    ``license_revocation`` (revocation is not grandfathered). An EXPIRY follows the source's
    ``temporal_egress_policy`` via 4f ``egress_reevaluation``: ``retroactive_restatement`` ->
    restate; ``egress_read_time`` -> reevaluate at read; ``trace_decision_time`` -> grandfather
    the already-issued output (no action) for reads at/after issuance.

    Returns ``(action, tombstone_reason)`` where action ∈ {none, reevaluate, restate, tombstone}.
    """
    if change == "revocation":
        return (ACTION_TOMBSTONE, "license_revocation")
    if change != "expiry":
        raise RestatementError(f"unknown license change {change!r}")

    reeval = egress_reevaluation(
        temporal_egress_policy,
        issued_decision_at=issued_decision_at,
        read_decision_at=read_decision_at,
    )
    if reeval == "restate":
        return (ACTION_RESTATE, None)
    if reeval == "reevaluate":
        return (ACTION_REEVALUATE, None)
    # grandfathered
    return (ACTION_NONE, None)


def assert_tombstone_reason(reason: str) -> None:
    """Validate a tombstone reason against the table enum (fail-closed)."""
    if reason not in TOMBSTONE_REASONS:
        raise RestatementError(f"unknown tombstone_reason {reason!r}")


def tombstone_required(reason: str) -> bool:
    """Whether a reason mandates retroactively pulling the subject/trace (a tombstone)."""
    assert_tombstone_reason(reason)
    return reason in TOMBSTONE_REQUIRED_REASONS


# --- scope-projected / disclosure-filtered / license-aware outbox ---------------------


@dataclass(frozen=True)
class OutboxEvent:
    restatement_id: str
    subscriber_ref: str
    idempotency_key: str
    decision_commit_id: int
    event_schema_version: str
    payload: Mapping[str, Any]
    event_hash: str


def build_outbox_event(
    *,
    restatement_id: str,
    subscriber_ref: str,
    decision_commit_id: int,
    event_schema_version: str,
    payload: Mapping[str, Any],
    allowed_public_fields: Iterable[str],
    license_grant: LicenseGrant,
    subscriber_scope: str,
    valid_at: datetime,
    decision_at: datetime,
    is_derivative: bool = False,
    idempotency_key: str | None = None,
) -> OutboxEvent:
    """Build one scope-projected, disclosure-filtered, license-aware outbox event.

    Rejected unless: it carries a non-empty ``restatement_id`` / ``event_schema_version`` and a
    positive ``decision_commit_id`` (the ordering key); its ``payload`` passes BOTH the private-
    field denylist AND an explicit ``allowed_public_fields`` ALLOWLIST (4d ``assert_no_private_leak``
    with ``allowed=`` — so a domain-specific private field NOT in the denylist still cannot leak);
    and the subscriber's scope is a permitted egress target under the source license at this
    valid/decision time. Subscriber delivery is REDISTRIBUTION by definition, so egress is gated
    with ``is_redistribution=True`` (4f) — a license without a redistribution grant cannot back an
    outbox event even if the scope matches.

    The ``idempotency_key`` is DERIVED from the canonical event identity (restatement + subscriber
    + decision commit + schema + payload); a supplied key must equal the derived value, so the
    same logical event can never be emitted under two different keys. ``event_hash`` IS that
    canonical identity address (reproducible / idempotent).
    """
    if not restatement_id:
        raise RestatementError("restatement_id is required")
    if not event_schema_version:
        raise RestatementError("event_schema_version is required")
    if decision_commit_id <= 0:
        raise RestatementError("decision_commit_id must be positive (ordering key)")

    # disclosure / private filter: denylist AND explicit public allowlist (defense in depth).
    assert_no_private_leak(payload, allowed=frozenset(allowed_public_fields))
    # license-aware egress: subscriber delivery is redistribution; gate fail-closed.
    assert_license_valid(license_grant, valid_at=valid_at, decision_at=decision_at)
    assert_egress_allowed(
        license_grant,
        target_scope=subscriber_scope,
        is_redistribution=True,
        is_derivative=is_derivative,
    )

    # the idempotency key / event hash IS the canonical event identity (no arbitrary key).
    event_hash = write_replay.canonical_json.content_hash(
        {
            "restatement_id": restatement_id,
            "subscriber_ref": subscriber_ref,
            "decision_commit_id": decision_commit_id,
            "event_schema_version": event_schema_version,
            "payload": payload,
        }
    )
    if idempotency_key is not None and idempotency_key != event_hash:
        raise RestatementError(
            "supplied idempotency_key does not match the canonical event identity; omit it or "
            "pass the derived value"
        )
    return OutboxEvent(
        restatement_id=restatement_id,
        subscriber_ref=subscriber_ref,
        idempotency_key=event_hash,
        decision_commit_id=decision_commit_id,
        event_schema_version=event_schema_version,
        payload=payload,
        event_hash=event_hash,
    )


# --- private payload / commitment resolution ------------------------------------------


def resolve_private_payload(
    record: CommitmentRecord,
    key: bytes | None,
    payload: Any,
    *,
    requester_tenant: str,
    owner_tenant: str,
) -> Any:
    """Resolve a tenant-private payload for an AUTHORIZED tenant (fail-closed otherwise).

    Authorization: the requester must be the owning tenant. Commitment/key-lineage/erasure: the
    payload is verified against its commitment via 4d ``verify_commitment`` — which fails closed
    when the key is ``None`` (crypto-shredded = the erasure model), so an erased value can never
    be resolved. Returns the plaintext payload only to the authorized tenant.
    """
    if not requester_tenant or requester_tenant != owner_tenant:
        raise RestatementError(
            f"requester tenant {requester_tenant!r} is not authorized for owner "
            f"{owner_tenant!r}"
        )
    # commitment-profile guard: 4d verify_commitment does not check the record's declared
    # binding/algorithm, so a record claiming a drifted/unsupported profile could still verify if
    # the digest happens to match. Pin them here at the resolver boundary (fail-closed).
    if record.canonicalization_binding != CANONICAL_BINDING:
        raise RestatementError(
            f"commitment canonicalization_binding {record.canonicalization_binding!r} != "
            f"{CANONICAL_BINDING!r}; profile drift"
        )
    if record.algorithm not in ALGORITHMS:
        raise RestatementError(
            f"commitment algorithm {record.algorithm!r} is not a supported profile algorithm"
        )
    verify_commitment(
        record, key, payload
    )  # CommitmentError on shred/mismatch (fail-closed)
    return payload


def assert_public_projection_safe(projection: Mapping[str, Any]) -> None:
    """Guard a shared/public projection: it must leak no tenant-private field (4d)."""
    assert_no_private_leak(projection)


# --- DB / write-path seam (documented) ------------------------------------------------


def emit_outbox(session, event):  # pragma: no cover - DB seam
    """Insert an outbox event idempotently (VARCH-8 write-path seam)."""
    raise NotImplementedError(
        "emit_outbox is the VARCH-8 write-path seam; the VARCH-6e deliverable is the pure "
        "restatement + outbox + private resolution core."
    )
