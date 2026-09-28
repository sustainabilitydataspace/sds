"""VARCH-0: conformance for sds-computation-profile-v1 deterministic arithmetic.

Covers backlog ACC-7, ACC-12: addend-order stability, ROUND_HALF_EVEN, intermediate
precision, NaN/Inf/float rejection, division-by-zero, null-vs-absent addend semantics,
and pinned conversion/factor order.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.semantic.profiles import canonical_json
from src.semantic.profiles import computation as comp


def test_deterministic_sum_is_order_independent():
    a = comp.deterministic_sum([Decimal("0.1"), Decimal("0.2"), Decimal("0.3")])
    b = comp.deterministic_sum([Decimal("0.3"), Decimal("0.1"), Decimal("0.2")])
    c = comp.deterministic_sum([Decimal("0.2"), Decimal("0.3"), Decimal("0.1")])
    assert a == b == c == Decimal("0.6")


def test_sum_accepts_int_and_decimal_string():
    assert comp.deterministic_sum([1, "2", Decimal("3")]) == Decimal("6")


def test_sum_rejects_float_and_bool_and_null():
    with pytest.raises(comp.ComputationError):
        comp.deterministic_sum([Decimal("1"), 2.0])
    with pytest.raises(comp.ComputationError):
        comp.deterministic_sum([True])
    with pytest.raises(comp.ComputationError):
        comp.deterministic_sum([Decimal("1"), None])


def test_to_decimal_rejects_nan_inf():
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(comp.ComputationError):
            comp.to_decimal(bad)


def test_to_decimal_rejects_unparseable_string_and_unsupported_type():
    with pytest.raises(comp.ComputationError):
        comp.to_decimal("not-a-number")
    with pytest.raises(comp.ComputationError):
        comp.to_decimal(object())


def test_quantize_uses_banker_rounding_at_output_boundary():
    assert comp.quantize(Decimal("2.5"), Decimal("1")) == Decimal("2")
    assert comp.quantize(Decimal("3.5"), Decimal("1")) == Decimal("4")
    assert comp.quantize(Decimal("1.005"), Decimal("0.01")) == Decimal("1.00")


def test_divide_rejects_zero_denominator():
    assert comp.divide(Decimal("1"), Decimal("4")) == Decimal("0.25")
    with pytest.raises(comp.ComputationError):
        comp.divide(Decimal("1"), 0)


def test_factor_chain_order_is_pinned_independent_of_input_order():
    value = Decimal("10")
    factors_one = [
        ("currency", "usd", Decimal("2")),
        ("unit", "kg", Decimal("3")),
        ("emission_factor", "co2e", Decimal("5")),
    ]
    factors_two = list(reversed(factors_one))
    # unit(3) -> emission(5) -> currency(2): 10*3*5*2 = 300, regardless of input order
    assert comp.apply_factor_chain(value, factors_one) == Decimal("300")
    assert comp.apply_factor_chain(value, factors_two) == Decimal("300")


def test_intermediate_precision_is_pinned():
    ctx = comp.computation_context()
    assert ctx.prec == comp.INTERMEDIATE_PRECISION
    assert str(ctx.rounding) == comp.ROUNDING_MODE


def test_factor_chain_duplicate_category_key_is_order_independent():
    # Two factors share (category, key); a value tie-breaker gives a total order so
    # input permutation cannot change the result despite per-multiply rounding.
    a = comp.apply_factor_chain(
        Decimal("1"), [("unit", "k", Decimal("2")), ("unit", "k", Decimal("7"))]
    )
    b = comp.apply_factor_chain(
        Decimal("1"), [("unit", "k", Decimal("7")), ("unit", "k", Decimal("2"))]
    )
    assert a == b == Decimal("14")


def test_profile_descriptor_and_hash_are_consistent():
    descriptor = comp.profile_descriptor()
    assert descriptor["profile_id"] == "sds-computation-profile-v1"
    assert comp.profile_hash() == canonical_json.content_hash(descriptor)
    assert descriptor["rounding_mode"] == "ROUND_HALF_EVEN"


def test_fixture_corpus_is_bound_into_descriptor():
    descriptor = comp.profile_descriptor()
    assert descriptor["fixture_corpus_sha256"] == comp.fixture_corpus_sha256()
    assert len(descriptor["fixture_corpus_sha256"]) == 64
