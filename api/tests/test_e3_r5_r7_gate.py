from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "e3_r5_r7_gate.py"
SPEC = importlib.util.spec_from_file_location("e3_r5_r7_gate", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def _dataset(identifier: str, title: str, dimension: str, indicator_id: str):
    return {
        "id": f"urn:ngsi-ld:Dataset:{identifier.rsplit(':', 1)[-1]}",
        "type": "Dataset",
        "identifier": {"type": "Property", "value": identifier},
        "title": {"type": "Property", "value": title},
        "description": {"type": "Property", "value": f"Description {title}"},
        "dimension": {"type": "Property", "value": dimension},
        "codeESRS": {"type": "Property", "value": "E1"},
        "codeGRI": {"type": "Property", "value": ""},
        "owner": {"type": "Property", "value": "system"},
        "accessRights": {"type": "Property", "value": "Internal"},
        "hasPolicy": {
            "type": "Relationship",
            "object": ["urn:sds:policy:a", "urn:sds:policy:b"],
        },
        "hasIndicator": {"type": "Relationship", "object": indicator_id},
    }


def _indicator(indicator_id: str, title: str):
    return {
        "id": indicator_id,
        "type": "Indicator",
        "title": {"type": "Property", "value": title},
    }


def test_evaluate_hierarchy_requires_dataset_indicator_and_policy_relations():
    rows = [
        {
            "identifier": "urn:sds:reg:esrs:a",
            "title": "A",
            "indicator": "Indicator A",
            "description": "Description A",
            "dimension": "Environmental",
            "sourceRef": "ESRS Official",
            "codeESRS": "E1",
            "codeGRI": "",
            "owner": "system",
            "accessRights": "Internal",
        },
        {
            "identifier": "urn:sds:reg:gri:b",
            "title": "B",
            "indicator": "Indicator B",
            "description": "Description B",
            "dimension": "Social",
            "sourceRef": "GRI Official",
            "codeESRS": "",
            "codeGRI": "GRI 1",
            "owner": "system",
            "accessRights": "Internal",
        },
    ]
    indicator_a = "urn:ngsi-ld:Indicator:a"
    graph = [
        _dataset(rows[0]["identifier"], "A", "Environmental", indicator_a),
        _indicator(indicator_a, "Indicator A"),
    ]

    details, summary = gate.evaluate_hierarchy(rows, graph)

    assert summary["total_variables"] == 2
    assert summary["represented_variables"] == 1
    assert summary["hierarchy_valid_variables"] == 1
    assert summary["representation_rate"] == 0.5
    assert summary["hierarchy_rate"] == 0.5
    assert details[0]["status"] == "success"
    assert details[1]["status"] == "error"
    assert "dataset missing" in details[1]["error"]


def test_functional_pilot_compares_expected_and_observed_semantics():
    row = {
        "identifier": "urn:sds:reg:esrs:a",
        "title": "A",
        "indicator": "Indicator A",
        "description": "Description A",
        "dimension": "Environmental",
        "sourceRef": "ESRS Official",
        "codeESRS": "E1",
        "codeGRI": "",
        "owner": "system",
        "accessRights": "Internal",
    }
    indicator_id = "urn:ngsi-ld:Indicator:a"
    graph = [
        _dataset(row["identifier"], "Wrong title", "Environmental", indicator_id),
        _indicator(indicator_id, "Indicator A"),
    ]

    matrix, summary = gate.evaluate_functional_pilot([row], graph)

    assert summary == {
        "pilot_rows": 1,
        "success": 0,
        "error": 1,
        "accuracy": 0.0,
    }
    assert matrix[0]["status"] == "error"
    assert "title" in matrix[0]["error"]
