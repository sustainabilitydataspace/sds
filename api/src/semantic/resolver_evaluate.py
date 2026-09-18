"""Resolver — deterministic evaluation & dimension-collapse (VARCH-6d).

Step 6 (resolver), sub-slice (d): the numeric-evaluation stage of the deterministic resolver
pipeline (candidate-v14 resolver contract lines 155-162). Runs after 6c (factors/measurement)
and before 6e (restatement/outbox/private). Pure + deterministic; DB-load is the VARCH-8 seam.

It COMPOSES the converged cores:

* :mod:`src.semantic.profiles.computation` (VARCH-0) — ALL numeric results are evaluated under
  the single pinned ``sds-computation-profile-v1`` context (order-independent decimal sum,
  pinned-precision divide, output-scale quantize, pinned factor-chain order).
* :mod:`src.semantic.write_replay` (4c) — ``verify_computation_profile_pin`` proves the caller's
  pinned profile hash matches the frozen registry profile BEFORE any arithmetic runs.

Resolver-contract steps covered here:
    validate partition MECE closure and joint coverage
    evaluate all derived numeric results under the pinned computation profile
    dimension collapse: partition+complete+commensurable -> rollup;
      else overlap+correction policy -> correction; else explicit aggregation policy ->
      aggregation; else DIMENSION_COLLAPSE_FORBIDDEN
    compose confidence/provenance using the declared versioned policy

Gates contributed here (mechanism; CI wiring in VARCH-10): partition-MECE-closure,
computation-profile-pinned-evaluation, dimension-collapse-decision, confidence-composition.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from src.semantic import write_replay
from src.semantic.profiles import computation

# dimension-collapse decision outcomes (candidate-v14 resolver contract lines 157-161).
COLLAPSE_ROLLUP = "rollup"
COLLAPSE_CORRECTION = "correction_policy"
COLLAPSE_AGGREGATION = "aggregation_policy"

# the deterministic confidence-composition kinds this pure core supports (fail-closed).
CONFIDENCE_MIN = "min"
CONFIDENCE_MEAN = "deterministic_mean"
CONFIDENCE_KINDS = frozenset({CONFIDENCE_MIN, CONFIDENCE_MEAN})


class EvaluationError(ValueError):
    """Raised on a partition/evaluation/collapse/confidence violation."""


# --- partition MECE -------------------------------------------------------------------


def validate_partition_mece(members: Iterable, *, expected_domain: Iterable) -> None:
    """partition-MECE-closure: the members partition the expected domain exactly.

    Mutually Exclusive — no member coordinate appears twice (no double counting). Collectively
    Exhaustive / joint coverage — the member set equals ``expected_domain`` exactly (no missing
    cell, no stray cell). ``expected_domain`` is the resolver-materialized full coordinate set
    (for a multi-axis collapse, the caller passes the cartesian product so joint coverage is
    checked, not per-axis coverage).
    """
    members = list(members)
    counts = Counter(members)
    dupes = sorted((str(m) for m, n in counts.items() if n > 1))
    if dupes:
        raise EvaluationError(
            f"partition is not mutually exclusive (duplicate coordinates: {dupes})"
        )
    present = set(members)
    expected = set(expected_domain)
    missing = present.symmetric_difference(expected)
    if missing:
        raise EvaluationError(
            "partition is not collectively exhaustive / jointly covering: "
            f"missing={sorted(map(str, expected - present))} "
            f"extra={sorted(map(str, present - expected))}"
        )


# --- deterministic evaluation under the pinned computation profile ---------------------


def evaluate_rollup(
    values: Iterable[object],
    *,
    computation_profile_hash: str,
    output_scale: object | None = None,
) -> Decimal:
    """Sum ``values`` order-independently under the pinned computation profile.

    Verifies the pinned profile hash (4c) so a result claiming this profile cannot have been
    computed under different precision/rounding, then sums via the profile's order-independent
    decimal reduction; an ``output_scale`` (e.g. ``'0.01'``) quantizes at the output boundary.
    """
    write_replay.verify_computation_profile_pin(computation_profile_hash)
    total = computation.deterministic_sum(values)
    if output_scale is not None:
        total = computation.quantize(total, output_scale)
    return total


def evaluate_factor_product(
    value: object,
    factors: Sequence[tuple[str, str, object]],
    *,
    computation_profile_hash: str,
    output_scale: object | None = None,
) -> Decimal:
    """Apply a factor chain to ``value`` under the pinned computation profile (pinned order)."""
    write_replay.verify_computation_profile_pin(computation_profile_hash)
    result = computation.apply_factor_chain(value, factors)
    if output_scale is not None:
        result = computation.quantize(result, output_scale)
    return result


# --- dimension collapse ---------------------------------------------------------------


def decide_dimension_collapse(
    *,
    is_partition: bool,
    complete_joint_partition: bool,
    commensurable: bool,
    has_overlap: bool,
    has_correction_policy: bool,
    has_aggregation_policy: bool,
) -> str:
    """Decide HOW (or whether) an axis may be collapsed — the exact candidate-v14 tree.

    1. a complete, jointly-partitioning, commensurable partition rolls up (sum);
    2. else an OVERLAPPING set with a correction evidence/policy applies that policy;
    3. else an explicit aggregation policy applies that policy;
    4. else collapse is FORBIDDEN (fail-closed — never silently sum overlapping cells).

    Returns the collapse MODE; the caller then evaluates the chosen mode under the computation
    profile (rollup via :func:`evaluate_rollup`; correction/aggregation via the declared policy's
    deterministic result, which is itself pinned).
    """
    if is_partition and complete_joint_partition and commensurable:
        return COLLAPSE_ROLLUP
    if has_overlap and has_correction_policy:
        return COLLAPSE_CORRECTION
    if has_aggregation_policy:
        return COLLAPSE_AGGREGATION
    raise EvaluationError("DIMENSION_COLLAPSE_FORBIDDEN")


# --- confidence / provenance composition ----------------------------------------------


@dataclass(frozen=True)
class ConfidencePolicyPin:
    """Pins the exact confidence-aggregation policy (id + version + hash + deterministic kind).

    Mirrors a versioned, hash-addressed confidence policy; the resolver trace pins
    ``confidence_policy_hash``, so composition must bind to the materialized published policy,
    never to a bare ``kind`` string.
    """

    policy_id: str
    policy_version: str
    policy_hash: str
    kind: str


def compose_confidence(
    values: Iterable[object],
    *,
    policy: ConfidencePolicyPin,
    published_confidence_policies: Mapping[str, tuple[str, str, str]],
    computation_profile_hash: str,
) -> Decimal:
    """Compose member confidences using a declared, versioned, hash-pinned policy.

    The policy pin MUST resolve to materialized live state: ``published_confidence_policies`` maps
    ``policy_id -> (version, hash, kind)`` and the pin's ``(version, hash, kind)`` must match
    exactly (fail-closed on unknown id, version/hash drift, or declared-kind mismatch) — so a
    caller cannot compose with one kind while the declared/pinned policy is another. Only the
    deterministic kinds in :data:`CONFIDENCE_KINDS` are supported. Composition runs under the
    pinned computation profile: ``min`` takes the least confident member; ``deterministic_mean``
    is the order-independent sum divided by the count.
    """
    pub = published_confidence_policies.get(policy.policy_id)
    if pub is None:
        raise EvaluationError(
            f"confidence policy {policy.policy_id!r} is not published for this slice"
        )
    pub_version, pub_hash, pub_kind = pub
    if (policy.policy_version, policy.policy_hash) != (pub_version, pub_hash):
        raise EvaluationError(
            f"confidence policy {policy.policy_id!r} version/hash drift: pinned "
            f"({policy.policy_version}, {policy.policy_hash}) != published ({pub_version}, "
            f"{pub_hash})"
        )
    if policy.kind != pub_kind:
        raise EvaluationError(
            f"confidence policy declared-kind mismatch: pinned {policy.kind!r} != published "
            f"{pub_kind!r}"
        )
    if policy.kind not in CONFIDENCE_KINDS:
        raise EvaluationError(
            f"unsupported confidence composition kind {policy.kind!r}; deterministic kinds are "
            f"{sorted(CONFIDENCE_KINDS)}"
        )
    write_replay.verify_computation_profile_pin(computation_profile_hash)
    decimals = [computation.to_decimal(v) for v in values]
    if not decimals:
        raise EvaluationError("no confidence values to compose")
    if policy.kind == CONFIDENCE_MIN:
        # deterministic: min by (value, fixed-point text) — total order, no float.
        return min(decimals, key=lambda d: (d, format(d, "f")))
    # deterministic_mean
    return computation.divide(computation.deterministic_sum(decimals), len(decimals))


# --- DB-load seam (documented) --------------------------------------------------------


def load_evaluation_inputs(session, ctx):  # pragma: no cover - DB seam
    """Materialize partition/operand/confidence inputs for the slice (VARCH-8 read seam)."""
    raise NotImplementedError(
        "load_evaluation_inputs is the VARCH-8 read-path seam; the VARCH-6d deliverable is the "
        "pure evaluation + dimension-collapse core."
    )
