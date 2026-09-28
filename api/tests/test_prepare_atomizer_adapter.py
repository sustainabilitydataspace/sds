from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "prepare_atomizer_for_import.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "prepare_atomizer_for_import", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


prepare_atomizer = _load_module()


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def test_prepare_from_atomizer_converts_minimal_exports(tmp_path):
    framework_csv = tmp_path / "framework_datapoints.csv"
    atomized_csv = tmp_path / "atomized_variables.csv"
    output_csv = tmp_path / "e1_dataset_register.csv"
    prepare_atomizer.LOG_PATH = tmp_path / "prepare_log.txt"

    _write_csv(
        framework_csv,
        [
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "Label",
            "Description",
            "DataType",
            "DefaultUnit",
            "Topic",
        ],
        [
            [
                "ESRS",
                "Set1",
                "E1-1",
                "Energy use",
                "Energy disclosure",
                "Numeric",
                "kWh",
                "Climate Change",
            ],
            [
                "GRI",
                "2021",
                "GRI 302-1",
                "Fuel use",
                "Fuel disclosure",
                "Numeric",
                "GJ",
                "Energy",
            ],
        ],
    )
    _write_csv(
        atomized_csv,
        [
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "AtomizedID",
            "Label",
            "Description",
            "UnitName",
            "UnitType",
            "IsActivityData",
            "IsCalculated",
            "CalculationLogic",
        ],
        [
            [
                "ESRS",
                "Set1",
                "E1-1",
                "E1-1-000",
                "Energy input",
                "Input",
                "kWh",
                "Energy",
                "TRUE",
                "FALSE",
                "",
            ],
            [
                "GRI",
                "2021",
                "GRI 302-1",
                "302-1-000",
                "Fuel input",
                "Input",
                "GJ",
                "Energy",
                "TRUE",
                "FALSE",
                "",
            ],
        ],
    )

    count = prepare_atomizer.prepare_from_atomizer(
        framework_path=framework_csv,
        atomized_path=atomized_csv,
        output_path=output_csv,
        verbose=False,
    )

    assert count == 2
    with output_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["identifier"] == "urn:sds:reg:esrs:e1_1"
    assert rows[0]["unitName"] == "kWh"
    assert rows[1]["identifier"] == "urn:sds:reg:gri:gri_302_1"
    assert rows[1]["codeGRI"] == "GRI 302-1"

    meta = json.loads(output_csv.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert meta["count"] == 2
    assert meta["dimensions"]["E"] == 2


def test_prepare_from_atomizer_corrects_esrs_e1_5_energy_metadata(tmp_path):
    framework_csv = tmp_path / "framework_datapoints.csv"
    atomized_csv = tmp_path / "atomized_variables.csv"
    output_csv = tmp_path / "e1_dataset_register.csv"
    prepare_atomizer.LOG_PATH = tmp_path / "prepare_log.txt"

    _write_csv(
        framework_csv,
        [
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "Label",
            "Description",
            "DataType",
            "DefaultUnit",
            "Topic",
        ],
        [
            [
                "ESRS",
                "Set1",
                "E1-5_13",
                "Fuel consumption from other fossil sources",
                "Disclosure datapoint as defined in IG3.",
                "Text",
                "Text",
                "Climate Change",
            ],
        ],
    )
    _write_csv(
        atomized_csv,
        [
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "AtomizedID",
            "Label",
            "Description",
            "UnitName",
            "UnitType",
        ],
        [
            [
                "ESRS",
                "Set1",
                "E1-5_13",
                "E1-5_13-000",
                "Fuel consumption from other fossil sources",
                "Input",
                "Text",
                "Text",
            ],
        ],
    )

    count = prepare_atomizer.prepare_from_atomizer(
        framework_path=framework_csv,
        atomized_path=atomized_csv,
        output_path=output_csv,
        verbose=False,
    )

    assert count == 1
    with output_csv.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["unitName"] == "MWh"
    assert row["unitType"] == "Energy"
    assert row["valueType"] == "numeric"
