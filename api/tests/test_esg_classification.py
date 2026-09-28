"""Regression guard for the canonical ESG-dimension classifier.

Pins the deterministic framework+code classifier (scripts/esg_classification.py)
and the checked-in indicators distribution, so the prior free-text
``Topic`` keyword heuristic — which mislabelled all 680 GRI datapoints as ``E``
and emitted zero ``G`` — cannot silently return. Root cause + fix attribution:
workspace/consensus/20260612-e1-dimension-fault-attribution (FINAL, unanimous).
"""

from __future__ import annotations

import collections
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "esg_classification.py"
INDICATORS_JSON = REPO_ROOT / "api" / "src" / "data" / "indicators.json"

# Expected distribution over the pinned 1,805-variable ESRS+GRI surface.
EXPECTED_DISTRIBUTION = {"E": 761, "S": 550, "G": 318, "Transversal": 176}


def _load_classifier():
    spec = importlib.util.spec_from_file_location("esg_classification", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


esg = _load_classifier()


@pytest.mark.parametrize(
    "framework, code, expected",
    [
        ("ESRS", "E1-1_03", "E"),
        ("ESRS", "E5-6_01", "E"),
        ("ESRS", "S1-1_01", "S"),
        ("ESRS", "S4-1_02", "S"),
        ("ESRS", "G1-1_01", "G"),
        ("ESRS", "GOV-1_01", "G"),
        ("ESRS", "BP-1_01", "Transversal"),
        ("ESRS", "IRO-1_01", "Transversal"),
        ("ESRS", "MDR-A_01", "Transversal"),
        ("ESRS", "SBM-1_01", "Transversal"),
        ("GRI", "GRI 101-1.a", "E"),
        ("GRI", "GRI 305-1.a", "E"),
        ("GRI", "GRI 201-1.a", "G"),
        ("GRI", "GRI 2-1.a", "G"),
        ("GRI", "GRI 3-1.a", "Transversal"),
        ("GRI", "GRI 401-1.a", "S"),
        ("GRI", "GRI 403-9.a", "S"),
    ],
)
def test_representative_codes(framework, code, expected):
    assert esg.classify_esg_dimension(framework, code) == expected


@pytest.mark.parametrize(
    "framework, code",
    [
        ("ESRS", "ZZ-9_99"),
        ("GRI", "GRI 999-1"),
        ("", "E1-1"),
        ("ESRS", ""),
        ("FOO", "1"),
    ],
)
def test_unknown_codes_return_none(framework, code):
    # No silent 'E' default: unclassifiable input fails loudly upstream.
    assert esg.classify_esg_dimension(framework, code) is None


def test_full_indicator_code_distribution():
    # Raw Atomizer and processed register CSVs are retired local inputs. Keep
    # the regression over the same 1,805-variable checked-in indicator surface.
    indicators = json.loads(INDICATORS_JSON.read_text(encoding="utf-8"))["indicators"]
    dist = collections.Counter()
    for item in indicators:
        framework = "ESRS" if item["code_esrs"] else "GRI"
        code = item["code_esrs"] or item["code_gri"]
        dimension = esg.classify_esg_dimension(framework, code)
        assert dimension is not None, f"Unclassified {framework} code: {code}"
        assert dimension == item["dimension"], item["id"]
        dist[dimension] += 1
    assert dict(dist) == EXPECTED_DISTRIBUTION


def test_indicators_json_dimension_distribution():
    payload = json.loads(INDICATORS_JSON.read_text(encoding="utf-8"))
    indicators = payload["indicators"]
    dist = collections.Counter(item.get("dimension", "") for item in indicators)
    assert dict(dist) == EXPECTED_DISTRIBUTION
    assert dist["G"] > 0
