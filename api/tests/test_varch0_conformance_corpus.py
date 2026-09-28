"""VARCH-0: execute the conformance fixture corpora bound into the profile hashes.

The corpora are hashed into each profile descriptor (append-only behavior binding).
These tests actually RUN every vector so the bound corpus is verified, not just hashed.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest

from src.semantic.profiles import canonical_json as cj
from src.semantic.profiles import computation as comp

CANONICAL_CORPUS = json.loads(cj._FIXTURE_CORPUS_PATH.read_text(encoding="utf-8"))
COMPUTATION_CORPUS = json.loads(comp._FIXTURE_CORPUS_PATH.read_text(encoding="utf-8"))


def _build_canonical_value(case: dict):
    kind = case["type"]
    raw = case["input"]
    if kind == "decimal":
        return Decimal(raw)
    if kind == "int":
        return int(raw)
    if kind in ("bool", "null"):
        return raw
    if kind == "datetime_utc":
        return datetime.fromisoformat(raw)
    if kind == "int_list":
        return list(raw)
    if kind == "int_set":
        return set(raw)
    raise AssertionError(f"unknown canonical corpus type: {kind}")


@pytest.mark.parametrize("case", CANONICAL_CORPUS["cases"], ids=lambda c: c["id"])
def test_canonical_corpus_vector(case):
    value = _build_canonical_value(case)
    assert canonicalize_text(value) == case["expected"]


def canonicalize_text(value) -> str:
    return cj.canonicalize(value).decode("utf-8")


@pytest.mark.parametrize("case", COMPUTATION_CORPUS["cases"], ids=lambda c: c["id"])
def test_computation_corpus_vector(case):
    op = case["op"]
    expected = Decimal(case["expected"])
    if op == "sum":
        assert comp.deterministic_sum([Decimal(v) for v in case["inputs"]]) == expected
    elif op == "quantize":
        assert comp.quantize(Decimal(case["value"]), Decimal(case["scale"])) == expected
    elif op == "divide":
        assert comp.divide(Decimal(case["num"]), Decimal(case["den"])) == expected
    elif op == "factor_chain":
        factors = [(c, k, Decimal(v)) for c, k, v in case["factors"]]
        assert comp.apply_factor_chain(Decimal(case["value"]), factors) == expected
    else:
        raise AssertionError(f"unknown computation corpus op: {op}")
