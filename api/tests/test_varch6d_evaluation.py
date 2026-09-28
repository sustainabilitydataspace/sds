"""VARCH-6d contract tests — resolver deterministic evaluation & dimension-collapse.

Pure deterministic core (resolver contract lines 155-162): partition MECE closure + joint
coverage; numeric evaluation under the pinned computation profile (4c pin + VARCH-0 arithmetic);
the dimension-collapse decision tree (rollup / correction / aggregation / FORBIDDEN); confidence
composition under a declared deterministic policy. No DB layer.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.semantic.profiles import computation
from src.semantic.resolver_evaluate import (
    COLLAPSE_AGGREGATION,
    COLLAPSE_CORRECTION,
    COLLAPSE_ROLLUP,
    ConfidencePolicyPin,
    EvaluationError,
    compose_confidence,
    decide_dimension_collapse,
    evaluate_factor_product,
    evaluate_rollup,
    validate_partition_mece,
)

_PH = computation.profile_hash()


# --- partition MECE -------------------------------------------------------------------


def test_partition_mece_passes() -> None:
    validate_partition_mece(["a", "b", "c"], expected_domain=["a", "b", "c"])


def test_partition_mece_joint_coverage_tuples() -> None:
    domain = [(f, s) for f in ("diesel", "petrol") for s in ("s1", "s2")]
    validate_partition_mece(domain, expected_domain=domain)


def test_partition_rejects_duplicate() -> None:
    with pytest.raises(EvaluationError, match="mutually exclusive"):
        validate_partition_mece(["a", "a", "b"], expected_domain=["a", "b"])


def test_partition_rejects_missing_cell() -> None:
    with pytest.raises(EvaluationError, match="collectively exhaustive"):
        validate_partition_mece(["a", "b"], expected_domain=["a", "b", "c"])


def test_partition_rejects_extra_cell() -> None:
    with pytest.raises(EvaluationError, match="collectively exhaustive"):
        validate_partition_mece(["a", "b", "z"], expected_domain=["a", "b"])


# --- evaluation under the pinned computation profile ----------------------------------


def test_evaluate_rollup_order_independent() -> None:
    a = evaluate_rollup(["1.5", "2.25", "0.25"], computation_profile_hash=_PH)
    b = evaluate_rollup(["0.25", "1.5", "2.25"], computation_profile_hash=_PH)
    assert a == b == Decimal("4")


def test_evaluate_rollup_quantizes_output() -> None:
    got = evaluate_rollup(
        ["1.005", "0.001"], computation_profile_hash=_PH, output_scale="0.01"
    )
    assert got == Decimal("1.01")  # half-even at the output boundary


def test_evaluate_rollup_rejects_bad_profile_pin() -> None:
    with pytest.raises(Exception):  # ReplayValidationError (pin drift)
        evaluate_rollup(["1"], computation_profile_hash="0" * 64)


def test_evaluate_rollup_rejects_float() -> None:
    with pytest.raises(computation.ComputationError):
        evaluate_rollup([1.5], computation_profile_hash=_PH)


def test_evaluate_factor_product() -> None:
    got = evaluate_factor_product(
        "10",
        [("emission_factor", "ef", "2"), ("unit", "u", "3")],
        computation_profile_hash=_PH,
    )
    assert got == Decimal("60")


# --- dimension collapse ---------------------------------------------------------------


def test_collapse_rollup_for_complete_commensurable_partition() -> None:
    assert (
        decide_dimension_collapse(
            is_partition=True,
            complete_joint_partition=True,
            commensurable=True,
            has_overlap=False,
            has_correction_policy=False,
            has_aggregation_policy=False,
        )
        == COLLAPSE_ROLLUP
    )


def test_collapse_correction_for_overlap_with_policy() -> None:
    assert (
        decide_dimension_collapse(
            is_partition=False,
            complete_joint_partition=False,
            commensurable=True,
            has_overlap=True,
            has_correction_policy=True,
            has_aggregation_policy=False,
        )
        == COLLAPSE_CORRECTION
    )


def test_collapse_aggregation_when_explicit_policy() -> None:
    assert (
        decide_dimension_collapse(
            is_partition=False,
            complete_joint_partition=False,
            commensurable=False,
            has_overlap=False,
            has_correction_policy=False,
            has_aggregation_policy=True,
        )
        == COLLAPSE_AGGREGATION
    )


def test_collapse_forbidden_otherwise() -> None:
    with pytest.raises(EvaluationError, match="DIMENSION_COLLAPSE_FORBIDDEN"):
        decide_dimension_collapse(
            is_partition=True,
            complete_joint_partition=False,  # incomplete partition
            commensurable=True,
            has_overlap=False,
            has_correction_policy=False,
            has_aggregation_policy=False,
        )


def test_collapse_overlap_without_correction_is_forbidden() -> None:
    with pytest.raises(EvaluationError, match="FORBIDDEN"):
        decide_dimension_collapse(
            is_partition=False,
            complete_joint_partition=False,
            commensurable=True,
            has_overlap=True,
            has_correction_policy=False,
            has_aggregation_policy=False,
        )


# --- confidence composition (versioned, hash-pinned policy) ---------------------------

_PUB_CONF = {
    "cp:min": ("v1", "a" * 64, "min"),
    "cp:mean": ("v1", "b" * 64, "deterministic_mean"),
}


def _cp(policy_id="cp:min", version="v1", policy_hash="a" * 64, kind="min"):
    return ConfidencePolicyPin(policy_id, version, policy_hash, kind)


def _compose(values, policy=None, **over):
    return compose_confidence(
        values,
        policy=policy or _cp(),
        published_confidence_policies=over.pop("published", _PUB_CONF),
        computation_profile_hash=over.pop("computation_profile_hash", _PH),
    )


def test_compose_confidence_min() -> None:
    assert _compose(["0.9", "0.7", "0.95"]) == Decimal("0.7")


def test_compose_confidence_mean_order_independent() -> None:
    mean = _cp("cp:mean", policy_hash="b" * 64, kind="deterministic_mean")
    a = _compose(["0.6", "0.9"], policy=mean)
    b = _compose(["0.9", "0.6"], policy=mean)
    assert a == b == Decimal("0.75")


def test_compose_confidence_rejects_unpublished_policy() -> None:
    with pytest.raises(EvaluationError, match="not published"):
        _compose(["0.9"], policy=_cp(policy_id="cp:bogus"))


def test_compose_confidence_rejects_version_hash_drift() -> None:
    with pytest.raises(EvaluationError, match="version/hash drift"):
        _compose(["0.9"], policy=_cp(policy_hash="0" * 64))


def test_compose_confidence_rejects_declared_kind_mismatch() -> None:
    # pinned policy cp:min is declared 'min'; claiming 'deterministic_mean' must fail closed.
    with pytest.raises(EvaluationError, match="declared-kind mismatch"):
        _compose(["0.9"], policy=_cp(kind="deterministic_mean"))


def test_compose_confidence_rejects_unsupported_kind() -> None:
    pub = {"cp:x": ("v1", "c" * 64, "vibes")}
    with pytest.raises(EvaluationError, match="unsupported confidence"):
        _compose(
            ["0.9"],
            policy=_cp("cp:x", policy_hash="c" * 64, kind="vibes"),
            published=pub,
        )


def test_compose_confidence_rejects_empty() -> None:
    with pytest.raises(EvaluationError, match="no confidence"):
        _compose([])


def test_compose_confidence_rejects_bad_profile_pin() -> None:
    with pytest.raises(Exception):  # ReplayValidationError
        _compose(["0.9"], computation_profile_hash="0" * 64)
