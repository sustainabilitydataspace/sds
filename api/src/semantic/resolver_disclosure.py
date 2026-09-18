"""Resolver — authorization & disclosure resolution (VARCH-6b).

Step 6 (resolver), sub-slice (b): the authorization + disclosure + consolidation stage of the
deterministic resolver pipeline (candidate-v14 resolver contract lines 147-149). It runs AFTER
6a has pinned the slice (resolution context + EVS) and BEFORE factor/measurement resolution
(6c). Pure + deterministic over resolver-materialized inputs; the DB-load is the VARCH-8 seam.

It COMPOSES the converged cores rather than re-deriving them:

* :mod:`src.semantic.write_scope` (4f) — ``effective_scope_at`` (no-retroactive-public-exposure)
  + ``is_visible`` (scope/tenant authorization); ``assert_consolidation_authorized`` +
  ``compose_disclosure`` + ``compose_license_scope`` (consent, composition, composed scope).
* :mod:`src.semantic.write_disclosure` (4e) — ``assert_disclosure_profile_pinned`` +
  ``decide_suppression`` + ``assert_suppression_stable`` (disclosure policy, pinned suppression
  outcome reproducibility, adjacent-release / restatement-delta differencing).
* :mod:`src.semantic.write_replay` (4c) — canonical content hashing to reproduce the pinned
  suppression / composed-outcome hashes.

All closed vocabularies are bound to the persisted CHECK constraints (semantic_models.py):
``semantic_consolidation_consents.consent_status``,
``consolidation_composition_profiles.composition_kind``,
``consolidation_composed_decisions.decision_outcome``,
``aggregate_suppression_decision_pins.suppression_decision``.

Resolver-contract steps covered here:
    validate steward/scope authorization and no-retroactive-public-exposure
    validate disclosure policy, profile id/version/hash, pinned suppression outcome,
      adjacent-release suppression, restatement-delta suppression
    validate consolidation consent, revocation rule, composition profile, composed disclosure
      outcome, and composed license/recipient/export scope

Gates contributed here (mechanism; CI wiring in VARCH-10): read-scope-authorization,
no-retroactive-public-exposure, disclosure-pin-reproducibility, longitudinal-differencing,
consolidation-consent/revocation, composed-outcome-reproducibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from src.semantic import write_replay
from src.semantic.coverage import EVAL_TENANT
from src.semantic.write_disclosure import (
    REASON_COMPLEMENTARY,
    Cell,
    DisclosureError,
    DisclosureProfilePin,
    SuppressionResult,
    assert_disclosure_profile_pinned,
    assert_suppression_stable,
    decide_suppression,
)
from src.semantic.write_scope import (
    ConsolidationError,
    DisclosureProfile,
    LicenseError,
    ScopeError,
    assert_consolidation_authorized,
    compose_disclosure,
    compose_license_scope,
    effective_scope_at,
    is_visible,
)

# --- Vocabularies bound to the persisted CHECK constraints -----------------------------

CONSENT_GRANTED = "granted"
CONSENT_WITHDRAWN = "withdrawn"
CONSENT_PENDING = "pending"
CONSENT_DENIED = "denied"
CONSENT_STATUSES = frozenset(
    {CONSENT_GRANTED, CONSENT_WITHDRAWN, CONSENT_PENDING, CONSENT_DENIED}
)

COMPOSITION_KINDS = frozenset(
    {"most_restrictive", "group_level_governing", "intersection_only", "custom"}
)
DECISION_PUBLISHED = "published"
DECISION_BLOCKED = "blocked"
DECISION_OUTCOMES = frozenset({DECISION_PUBLISHED, DECISION_BLOCKED})

SUPPRESSION_RELEASED = "released"
SUPPRESSION_SUPPRESSED = "suppressed"


# --- Pure-core data shapes ------------------------------------------------------------


@dataclass(frozen=True)
class DisclosurePolicy:
    """An applied ``aggregate_disclosure_policies`` row for the slice."""

    policy_key: str
    scope_context: str
    profile_pin: DisclosureProfilePin
    min_cohort_k: int


@dataclass(frozen=True)
class SuppressionOutcomePin:
    """A pinned ``aggregate_suppression_decision_pins`` row to reproduce."""

    suppression_key: str
    disclosure_profile_ref: str
    released_cell_set_hash: str
    complementary_selection_hash: str
    suppression_decision: str  # 'released' | 'suppressed' (slice-level)


@dataclass(frozen=True)
class ComposedConsolidation:
    """The reproduced composed consolidation outcome for a published group decision."""

    composed_disclosure: DisclosureProfile
    composed_license_scope: frozenset[str]
    allowed_recipient_scope: str | None


# --- steward / scope authorization ----------------------------------------------------


def resolve_effective_read_scope(
    *,
    prior_scope: str,
    new_scope: str,
    transition_decision_commit: int,
    read_decision_commit: int,
    row_tenant: str,
    eval_scope: str,
    eval_tenant: str | None = None,
    authorized_steward_tenants: Iterable[str] = (),
) -> str:
    """read-scope-authorization + no-retroactive-public-exposure.

    Computes the scope a read at ``read_decision_commit`` SEES (a later widening cannot expose an
    earlier slice — 4f ``effective_scope_at``), then enforces that the row is admissible to the
    evaluation (4f ``is_visible``). A tenant-scoped evaluation additionally requires the principal
    to be an authorized steward of that exact tenant (``authorized_steward_tenants``), so a
    tenant read cannot be performed without steward authority. Returns the effective scope.
    """
    effective = effective_scope_at(
        prior_scope,
        new_scope,
        transition_decision_commit=transition_decision_commit,
        read_decision_commit=read_decision_commit,
    )
    if eval_scope == EVAL_TENANT:
        if not eval_tenant:
            raise ScopeError("EVAL_TENANT read requires eval_tenant")
        if eval_tenant not in set(authorized_steward_tenants):
            raise ScopeError(
                f"principal is not an authorized steward of tenant {eval_tenant!r}"
            )
    if not is_visible(effective, row_tenant, eval_scope, eval_tenant):
        raise ScopeError(
            f"row (effective scope {effective!r}, tenant {row_tenant!r}) is not visible to "
            f"an {eval_scope!r} evaluation (tenant {eval_tenant!r})"
        )
    return effective


# --- disclosure resolution ------------------------------------------------------------


def _released_hash(result: SuppressionResult) -> str:
    released = sorted(d.cell_key for d in result.decisions if not d.suppressed)
    return write_replay.canonical_json.content_hash(released)


def _complementary_hash(result: SuppressionResult) -> str:
    compl = sorted(
        d.cell_key for d in result.decisions if d.reason == REASON_COMPLEMENTARY
    )
    return write_replay.canonical_json.content_hash(compl)


def validate_disclosure_resolution(
    cells: Iterable[Cell],
    *,
    policy: DisclosurePolicy,
    suppression_pin: SuppressionOutcomePin,
    scope_context: str,
    previous_result: SuppressionResult | None = None,
    is_first_release: bool = False,
    restated_cells: frozenset[str] = frozenset(),
) -> SuppressionResult:
    """Validate the disclosure policy + reproduce the pinned suppression outcome.

    The applied policy must govern this ``scope_context`` and pin a registry-current disclosure
    profile (4e ``assert_disclosure_profile_pinned``). Suppression is RECOMPUTED deterministically
    (4e ``decide_suppression``) under the policy's ``min_cohort_k`` and the recomputed released-set
    and complementary-selection hashes MUST equal the pinned outcome — so the pin is a reproducible
    content address of the exact suppression, never a bare stored string. The pinned profile ref
    must match the policy's profile, and the slice-level ``suppression_decision`` must match
    whether anything was suppressed. When a ``previous_result`` is supplied, adjacent-release /
    restatement-delta stability is enforced (4e ``assert_suppression_stable``). Longitudinal-
    differencing is FAIL-CLOSED: the prior-release context is REQUIRED — a caller must supply
    either ``previous_result`` (the adjacent release) or ``is_first_release=True`` (an explicit
    no-prior-release proof); omitting both is rejected so a suppressed<->published flip can never
    slip through unchecked.
    """
    if policy.scope_context != scope_context:
        raise DisclosureError(
            f"disclosure policy scope {policy.scope_context!r} != read scope {scope_context!r}"
        )
    if policy.min_cohort_k < 1:
        raise DisclosureError("disclosure policy min_cohort_k must be >= 1")
    assert_disclosure_profile_pinned(policy.profile_pin)
    if suppression_pin.disclosure_profile_ref != policy.profile_pin.profile_id:
        raise DisclosureError(
            f"suppression pin profile {suppression_pin.disclosure_profile_ref!r} != policy "
            f"profile {policy.profile_pin.profile_id!r}"
        )
    if suppression_pin.suppression_decision not in (
        SUPPRESSION_RELEASED,
        SUPPRESSION_SUPPRESSED,
    ):
        raise DisclosureError(
            f"unknown suppression_decision {suppression_pin.suppression_decision!r}"
        )

    result = decide_suppression(
        cells, k_threshold=policy.min_cohort_k, profile_pin=policy.profile_pin
    )

    if _released_hash(result) != suppression_pin.released_cell_set_hash:
        raise DisclosureError(
            "pinned-suppression-outcome drift: recomputed released-cell-set hash != pin"
        )
    if _complementary_hash(result) != suppression_pin.complementary_selection_hash:
        raise DisclosureError(
            "pinned-suppression-outcome drift: recomputed complementary-selection hash != pin"
        )
    expected_decision = (
        SUPPRESSION_SUPPRESSED if result.suppressed_keys() else SUPPRESSION_RELEASED
    )
    if suppression_pin.suppression_decision != expected_decision:
        raise DisclosureError(
            f"pinned suppression_decision {suppression_pin.suppression_decision!r} != "
            f"recomputed {expected_decision!r}"
        )

    # longitudinal-differencing fail-closed: prior-release context is mandatory.
    if previous_result is None and not is_first_release:
        raise DisclosureError(
            "longitudinal-differencing: a prior-release SuppressionResult is required "
            "(pass previous_result), or assert is_first_release=True explicitly"
        )
    if previous_result is not None:
        assert_suppression_stable(
            previous_result, result, restated_cells=restated_cells
        )
    return result


# --- consolidation resolution ---------------------------------------------------------


def validate_consolidation_resolution(
    *,
    member_consents: Mapping[str, str],
    composition_kind: str,
    member_disclosure_profiles: Iterable[DisclosureProfile],
    member_license_scopes: Iterable[frozenset[str]],
    declared_outcome: str,
    group_disclosure_profile: DisclosureProfile | None = None,
    unanimous_authorization: bool = False,
    allowed_recipient_scope: str | None = None,
    composed_disclosure_outcome_hash: str | None = None,
    composed_rights_decision_hash: str | None = None,
) -> ComposedConsolidation | None:
    """Validate a composed consolidation decision (consent, revocation, composition, scope).

    Consent + revocation rule: every member must be ``granted``; any ``withdrawn`` member
    (revocation) — or any non-granted member — blocks the consolidation (4f
    ``assert_consolidation_authorized``). The disclosure profile is composed fail-closed (4f
    ``compose_disclosure``: most-restrictive, group-governing only when unanimously authorized
    and at least as restrictive; incomparable lattices block) and the license/consent/export
    scope by intersection (4f ``compose_license_scope``: empty blocks).

    ``declared_outcome`` (the persisted ``decision_outcome``) is reconciled with what composition
    actually yields: if composition BLOCKS, the decision must be ``blocked`` (returns ``None``);
    if it succeeds, the decision must be ``published``. ``composition_kind='custom'`` is rejected
    fail-closed (no deterministic evaluator). Any ``allowed_recipient_scope`` must lie within the
    composed license scope. A ``published`` decision MUST pin BOTH ``composed_disclosure_outcome_hash``
    and ``composed_rights_decision_hash``, and each MUST equal the recomputed canonical content
    hash (composed-outcome reproducibility); the rights hash binds the composed license scope AND
    the recipient scope so a recipient drift cannot pass unnoticed.
    """
    if composition_kind not in COMPOSITION_KINDS:
        raise ConsolidationError(f"unknown composition_kind {composition_kind!r}")
    if composition_kind == "custom":
        # 'custom' has no deterministic evaluator in the pure core; fail closed rather than
        # silently composing it like most_restrictive (a custom profile could be looser).
        raise ConsolidationError(
            "composition_kind 'custom' has no deterministic evaluator; publication blocked "
            "(fail-closed) until a pinned custom composition function is implemented"
        )
    if declared_outcome not in DECISION_OUTCOMES:
        raise ConsolidationError(f"unknown decision_outcome {declared_outcome!r}")
    bad_status = sorted(
        s for s in member_consents.values() if s not in CONSENT_STATUSES
    )
    if bad_status:
        raise ConsolidationError(f"unknown consent_status values {bad_status}")
    if composition_kind == "group_level_governing" and group_disclosure_profile is None:
        raise ConsolidationError(
            "group_level_governing composition requires a group disclosure profile"
        )

    withdrawn = sorted(t for t, s in member_consents.items() if s == CONSENT_WITHDRAWN)
    if withdrawn:
        # revocation rule: a withdrawn consent revokes the member's participation outright.
        if declared_outcome == DECISION_BLOCKED:
            return None
        raise ConsolidationError(
            f"consolidation has withdrawn (revoked) members {withdrawn}; cannot publish"
        )

    consents_bool = {t: s == CONSENT_GRANTED for t, s in member_consents.items()}
    group = (
        group_disclosure_profile
        if composition_kind == "group_level_governing"
        else None
    )
    try:
        assert_consolidation_authorized(consents_bool)
        composed_disc = compose_disclosure(
            member_disclosure_profiles,
            group,
            unanimous_authorization=unanimous_authorization,
        )
        composed_scope = compose_license_scope(member_license_scopes)
    except ConsolidationError:
        if declared_outcome == DECISION_BLOCKED:
            return None
        raise

    if declared_outcome == DECISION_BLOCKED:
        raise ConsolidationError(
            "decision_outcome is 'blocked' but composition succeeded; inconsistent decision"
        )

    if (
        allowed_recipient_scope is not None
        and allowed_recipient_scope not in composed_scope
    ):
        raise LicenseError(
            f"allowed_recipient_scope {allowed_recipient_scope!r} is not within the composed "
            f"license scope {sorted(composed_scope)}"
        )

    # composed-outcome reproducibility is MANDATORY for a published decision: both pinned hashes
    # must be present AND equal the recomputed canonical outcomes (no bypass via null pins).
    if (
        composed_disclosure_outcome_hash is None
        or composed_rights_decision_hash is None
    ):
        raise ConsolidationError(
            "a published consolidation decision must pin BOTH composed_disclosure_outcome_hash "
            "and composed_rights_decision_hash (composed-outcome reproducibility)"
        )
    expected_disc = write_replay.canonical_json.content_hash(
        {
            "profile_id": composed_disc.profile_id,
            "rank": composed_disc.rank,
            "lattice": composed_disc.lattice,
        }
    )
    if expected_disc != composed_disclosure_outcome_hash:
        raise ConsolidationError(
            "composed_disclosure_outcome_hash drift: recomputed != pinned"
        )
    # the rights hash binds the composed license scope AND the recipient scope, so a recipient
    # drift cannot pass while the license-scope hash still matches (M3).
    expected_rights = write_replay.canonical_json.content_hash(
        {
            "composed_license_scope": sorted(composed_scope),
            "allowed_recipient_scope": allowed_recipient_scope,
        }
    )
    if expected_rights != composed_rights_decision_hash:
        raise ConsolidationError(
            "composed_rights_decision_hash drift: recomputed != pinned"
        )

    return ComposedConsolidation(
        composed_disclosure=composed_disc,
        composed_license_scope=composed_scope,
        allowed_recipient_scope=allowed_recipient_scope,
    )


# --- DB-load seam (documented) --------------------------------------------------------


def load_disclosure_inputs(session, ctx):  # pragma: no cover - DB seam
    """Materialize disclosure/consolidation inputs for the slice (VARCH-8 read-path seam)."""
    raise NotImplementedError(
        "load_disclosure_inputs is the VARCH-8 read-path seam; the VARCH-6b deliverable is the "
        "pure authorization + disclosure + consolidation resolution core."
    )
