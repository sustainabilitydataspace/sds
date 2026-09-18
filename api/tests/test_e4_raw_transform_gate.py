from __future__ import annotations

import csv
from decimal import Decimal

from scripts import gate_raw_value_transform_matrix as gate


def test_public_matrix_path_uses_ignored_artifacts_directory():
    assert gate.public_matrix_path(gate.DEFAULT_MATRIX) == "artifacts/e4_raw_transform_matrix.csv"


def test_build_fixture_writes_three_self_authored_conversion_rules(tmp_path):
    input_path = tmp_path / "raw-values.csv"
    expected_path = tmp_path / "expected.csv"

    gate.build_fixture_files(
        input_path=input_path,
        expected_path=expected_path,
        row_count=1000,
        external_key_prefix="pytest-r8",
    )

    with input_path.open(newline="", encoding="utf-8") as handle:
        raw_rows = list(csv.DictReader(handle))
    with expected_path.open(newline="", encoding="utf-8") as handle:
        expected_rows = list(csv.DictReader(handle))

    assert len(raw_rows) == len(expected_rows) == 1000
    assert {row["rule_id"] for row in expected_rows} == {
        "water_l_to_m3",
        "energy_kwh_to_mwh",
        "emissions_kgco2e_to_tco2e",
    }
    assert {row["concept"] for row in raw_rows} == {
        "urn:sds:sample:water_volume",
        "urn:sds:sample:energy_use",
        "urn:sds:sample:emissions_mass",
    }
    assert raw_rows[0]["unit"] == "L"
    assert expected_rows[0]["expected_unit"] == "m3"
    assert Decimal(expected_rows[0]["expected_value"]) == Decimal("1")


def test_evaluate_transformations_records_observed_result_and_errors():
    expected_rows = [
        {
            "row_id": "R8-000001",
            "rule_id": "water_l_to_m3",
            "domain": "water",
            "external_key": "r8:000001",
            "concept": "urn:sds:sample:water_volume",
            "raw_value": "1000",
            "raw_unit": "L",
            "expected_value": "1",
            "expected_unit": "m3",
        }
    ]
    actual = {
        "r8:000001": {
            "value": Decimal("1.0"),
            "unit": "m3",
            "original_value": Decimal("1000"),
            "original_unit": "L",
            "conversion_applied": True,
        }
    }

    matrix, summary = gate.evaluate_transformations(expected_rows, actual)

    assert summary == {"total": 1, "success": 1, "error": 0, "success_rate": 1.0}
    assert matrix[0]["status"] == "success"
