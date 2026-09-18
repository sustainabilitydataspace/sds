"""``sds-computation-profile-v1`` — deterministic decimal arithmetic profile.

Every regulated/reportable numeric result in the SDS semantic-atomization substrate
is evaluated under this profile so that the same inputs always yield the same value,
independent of input order or host. EVS-equivalence and replay gates compare results
at the *value* level under this profile, not only serialized-payload hashes.

Pinned rules (v1):
- Numeric domain is ``Decimal`` fixed-point. ``float`` ingress is rejected; so are
  NaN/Infinity. ``bool`` is not a number.
- Intermediate precision is pinned (``INTERMEDIATE_PRECISION`` significant digits) and the
  rounding mode is ``ROUND_HALF_EVEN``. Each Decimal operation is exact when it fits in the
  pinned precision and otherwise rounds to ``INTERMEDIATE_PRECISION`` significant digits
  under that mode (e.g. ``1/3`` or a sum whose magnitude spans more than 50 digits). This is
  deterministic, not "no intermediate rounding": rounding to a declared OUTPUT *scale* only
  ever happens at :func:`quantize`. Both behaviors are pinned and bound by corpus vectors.
- The arithmetic context traps ``InvalidOperation``, ``DivisionByZero`` and ``Overflow``
  so non-determinism/loss surfaces as an error instead of a silent special value.
- Aggregation is order-independent: addends are sorted by a deterministic key
  (value, then fixed-point text) before reduction, so any input permutation produces an
  identical result.
- ``None`` addends are rejected (an explicit null is not a number); callers omit *absent*
  values from the input list (absent != null).
- Multiplicative factor application order is pinned by category
  (``PINNED_FACTOR_CATEGORY_ORDER``) then by factor key, independent of input order.
"""

from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_EVEN as _ROUND_HALF_EVEN
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from pathlib import Path
from typing import Iterable, Sequence

from src.semantic.profiles import canonical_json

COMPUTATION_PROFILE_ID = "sds-computation-profile-v1"
COMPUTATION_PROFILE_VERSION = "v1"
INTERMEDIATE_PRECISION = 50
ROUNDING_MODE = "ROUND_HALF_EVEN"
PINNED_FACTOR_CATEGORY_ORDER = ("unit", "emission_factor", "currency")

_FIXTURE_CORPUS_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "computation_corpus.json"
)


class ComputationError(ValueError):
    """Raised when a value/operation violates the deterministic computation profile."""


def computation_context() -> Context:
    """Return a fresh arithmetic context pinned by this profile."""
    return Context(
        prec=INTERMEDIATE_PRECISION,
        rounding=_ROUND_HALF_EVEN,
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


def to_decimal(value: object) -> Decimal:
    """Coerce ``value`` to a finite ``Decimal`` under the profile, or raise."""
    if isinstance(value, bool):
        raise ComputationError("bool is not a numeric value")
    if isinstance(value, float):
        raise ComputationError("float is forbidden; supply a Decimal, int, or str")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ComputationError(f"not a decimal: {value!r}") from exc
    else:
        raise ComputationError(f"unsupported numeric type: {type(value).__name__}")
    if result.is_nan() or result.is_infinite():
        raise ComputationError("NaN/Infinity is not a valid computation value")
    return result


def _addend_sort_key(value: Decimal) -> tuple:
    return (value, format(value, "f"))


def deterministic_sum(values: Iterable[object]) -> Decimal:
    """Sum ``values`` order-independently under the single pinned context.

    The context is NOT caller-overridable: every call runs under exactly the v1
    profile semantics, so a result claiming this profile cannot have used different
    precision/rounding. ``None`` entries are rejected; absent values must be omitted.
    """
    addends: list[Decimal] = []
    for value in values:
        if value is None:
            raise ComputationError("null addend is not summable; omit absent values")
        addends.append(to_decimal(value))
    addends.sort(key=_addend_sort_key)
    with localcontext(computation_context()):
        total = Decimal(0)
        for addend in addends:
            total = total + addend
    return total


def divide(numerator: object, denominator: object) -> Decimal:
    """Divide under the single pinned context; division by zero is an explicit error."""
    denom = to_decimal(denominator)
    if denom == 0:
        raise ComputationError("division by zero")
    with localcontext(computation_context()):
        return to_decimal(numerator) / denom


def quantize(value: object, scale: object) -> Decimal:
    """Round ``value`` to the exponent of ``scale`` (e.g. ``Decimal('0.01')``).

    This is the only place rounding to the declared output *scale* is applied (the output
    boundary); intermediate operations still round to the pinned 50-significant-digit
    context precision. Runs under the single pinned context.
    """
    exponent = to_decimal(scale)
    with localcontext(computation_context()):
        return to_decimal(value).quantize(exponent, rounding=_ROUND_HALF_EVEN)


def apply_factor_chain(
    value: object,
    factors: Sequence[tuple[str, str, object]],
) -> Decimal:
    """Multiply ``value`` by ``factors`` in pinned order, independent of input order.

    Each factor is ``(category, key, value)``. Factors are applied ordered by
    ``PINNED_FACTOR_CATEGORY_ORDER`` then by ``key`` then by canonical value, under the
    single pinned context.
    """
    order = {
        category: index for index, category in enumerate(PINNED_FACTOR_CATEGORY_ORDER)
    }

    def _sort_key(factor: tuple[str, str, object]) -> tuple:
        # Include the canonical factor value as a final tie-breaker so duplicate
        # (category, key) factors have a total order: input permutations cannot
        # change the result even though per-multiply rounding is not commutative.
        return (
            order.get(factor[0], len(order)),
            factor[1],
            canonicalize_decimal_key(to_decimal(factor[2])),
        )

    ordered = sorted(factors, key=_sort_key)
    with localcontext(computation_context()):
        result = to_decimal(value)
        for _category, _key, factor_value in ordered:
            result = result * to_decimal(factor_value)
    return result


def canonicalize_decimal_key(value: Decimal) -> str:
    """Stable total-order key for a Decimal (value then fixed-point text)."""
    return f"{value:+f}|{format(value, 'f')}"


def fixture_corpus_sha256() -> str:
    """Return the newline-normalized SHA-256 of the conformance fixture corpus.

    Bound into the descriptor so a behavior change that alters declared conformance
    flips the profile hash.
    """
    text = _FIXTURE_CORPUS_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def profile_descriptor() -> dict:
    """Return the append-only, hashable descriptor of this profile (v1)."""
    return {
        "profile_id": COMPUTATION_PROFILE_ID,
        "version": COMPUTATION_PROFILE_VERSION,
        "kind": "computation",
        "numeric_domain": "decimal-fixed-point",
        "intermediate_precision": INTERMEDIATE_PRECISION,
        "rounding_mode": ROUNDING_MODE,
        "factor_category_order": list(PINNED_FACTOR_CATEGORY_ORDER),
        "fixture_corpus_sha256": fixture_corpus_sha256(),
        "rules": [
            "float-and-nan-inf-forbidden",
            "bool-not-numeric",
            "intermediate-rounding-to-50-significant-digits-half-even",
            "output-scale-rounding-only-at-quantize",
            "traps-invalidop-divzero-overflow",
            "aggregation-order-independent-via-sort-key",
            "null-addend-rejected-absent-omitted",
            "division-by-zero-explicit-error",
            "factor-chain-pinned-category-then-key-then-value-order",
            "single-pinned-context-no-caller-override",
        ],
    }


def profile_hash() -> str:
    """Return the content-addressable hash of this profile's descriptor."""
    return canonical_json.content_hash(profile_descriptor())
