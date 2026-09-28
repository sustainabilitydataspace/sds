from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from scripts import gate_raw_value_transform_matrix as gate
from src.services.value_csv_import import load_values_from_csv


def test_matrix_report_path_does_not_claim_historical_public_evidence():
    assert gate.DEFAULT_MATRIX.is_relative_to(gate.REPO_ROOT / ".local_artifacts")
    assert gate.DEFAULT_REPORT.is_relative_to(gate.REPO_ROOT / ".local_artifacts")
    assert (
        gate.public_matrix_path(
            Path("/workspace-data/extracted/analysis/e4_raw_transform_matrix.csv")
        )
        == "e4_raw_transform_matrix.csv"
    )


def test_build_fixture_writes_three_real_conversion_rules(tmp_path):
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
    assert raw_rows[0]["unit"] == "L"
    assert raw_rows[0]["expected_unit"] == "m³"
    assert expected_rows[0]["expected_unit"] == "m³"
    assert Decimal(expected_rows[0]["expected_value"]) == Decimal("1")
    assert raw_rows[1]["unit"] == "kWh"
    assert expected_rows[1]["expected_unit"] == "MWh"
    assert raw_rows[2]["unit"] == "kg CO2e"
    assert [row.expected_unit for row in load_values_from_csv(input_path)[:3]] == [
        "m³",
        "MWh",
        "t CO2e",
    ]
    assert expected_rows[2]["expected_unit"] == "t CO2e"
    assert all(not row["concept"].startswith("syg:") for row in raw_rows)


def test_evaluate_transformations_records_observed_result_and_errors():
    expected_rows = [
        {
            "row_id": "R8-000001",
            "rule_id": "water_l_to_m3",
            "external_key": "r8:000001",
            "concept": "urn:sds:reg:esrs:e3_4_01",
            "raw_value": "1000",
            "raw_unit": "L",
            "expected_value": "1",
            "expected_unit": "m³",
        },
        {
            "row_id": "R8-000002",
            "rule_id": "energy_kwh_to_mwh",
            "external_key": "r8:000002",
            "concept": "urn:sds:reg:esrs:e1_5_12",
            "raw_value": "2000",
            "raw_unit": "kWh",
            "expected_value": "2",
            "expected_unit": "MWh",
        },
    ]
    actual = {
        "r8:000001": {
            "value": Decimal("1.0"),
            "unit": "m³",
            "original_value": Decimal("1000"),
            "original_unit": "L",
            "conversion_applied": True,
        },
        "r8:000002": {
            "value": Decimal("2"),
            "unit": "kWh",
            "original_value": None,
            "original_unit": None,
            "conversion_applied": False,
        },
    }

    matrix, summary = gate.evaluate_transformations(expected_rows, actual)

    assert summary == {"total": 2, "success": 1, "error": 1, "success_rate": 0.5}
    assert matrix[0]["status"] == "success"
    assert matrix[0]["observed_value"] == "1"
    assert matrix[1]["status"] == "error"
    assert "expected unit MWh" in matrix[1]["error"]
