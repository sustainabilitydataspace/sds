from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

from src.services.value_csv_import import ALLOWED_VALUE_COLUMNS

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "sync_atomizer_exports.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_atomizer_exports", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync_atomizer = _load_module()


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def test_sync_exports_copies_files_and_writes_manifest(tmp_path):
    source_dir = tmp_path / "atomizer_exports"
    dest_dir = tmp_path / "data" / "atomizer"
    manifest_path = dest_dir / "manifest.json"
    checksum_path = dest_dir / "MANIFEST.sha256"

    _write_csv(
        source_dir / "framework_datapoints.csv",
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
            ]
        ],
    )
    _write_csv(
        source_dir / "atomized_variables.csv",
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
            ]
        ],
    )

    manifest = sync_atomizer.sync_exports(
        source_dir=source_dir,
        dest_dir=dest_dir,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
        repo_root=tmp_path,
    )

    assert (dest_dir / "framework_datapoints.csv").exists()
    assert (dest_dir / "atomized_variables.csv").exists()
    assert manifest_path.exists()
    assert checksum_path.exists()

    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["canonical_dir"] == "data/atomizer"
    assert persisted["contract_mode"] == "legacy"
    assert persisted["policy"]["canonical_inputs"] == [
        "framework_datapoints.csv",
        "atomized_variables.csv",
    ]
    assert persisted["sync"]["mode"] == "source_sync"
    assert persisted["files"][0]["row_count"] == 1
    assert "framework_datapoints.csv" in checksum_path.read_text(encoding="utf-8")
    assert manifest["files"][1]["row_count"] == 1


def test_sync_exports_supports_register_package(tmp_path):
    source_dir = tmp_path / "atomizer_register"
    dest_dir = tmp_path / "data" / "atomizer"
    manifest_path = dest_dir / "manifest.json"
    checksum_path = dest_dir / "MANIFEST.sha256"

    _write_csv(
        source_dir / "sds_dataset_register.csv",
        [
            "identifier",
            "title",
            "indicator",
            "description",
            "dimension",
            "unitName",
            "unitType",
            "periodicity",
            "periodType",
            "sourceRef",
            "codeESRS",
            "codeGRI",
            "codeGRI_expanded",
            "evidencePath",
            "sourceRow",
            "owner",
            "accessRights",
            "validationMethod",
            "doubleMateriality",
            "valueType",
        ],
        [
            [
                "urn:sds:reg:esrs:e1_1",
                "Energy use",
                "Energy use",
                "Energy disclosure",
                "E",
                "kWh",
                "Energy",
                "annual",
                "fiscal_year",
                "Atomizer vNext",
                "E1-1",
                "",
                "",
                "",
                "1",
                "system",
                "Internal",
                "automated",
                "",
                "numeric",
            ]
        ],
    )

    manifest = sync_atomizer.sync_exports(
        source_dir=source_dir,
        dest_dir=dest_dir,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
        repo_root=tmp_path,
    )

    assert (dest_dir / "sds_dataset_register.csv").exists()
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["contract_mode"] == "register"
    assert persisted["policy"]["canonical_inputs"] == ["sds_dataset_register.csv"]
    assert persisted["policy"]["adapter_script"] is None
    assert manifest["files"][0]["filename"] == "sds_dataset_register.csv"


def test_sync_exports_register_package_can_include_values_csv(tmp_path):
    source_dir = tmp_path / "atomizer_register"
    dest_dir = tmp_path / "data" / "atomizer"
    manifest_path = dest_dir / "manifest.json"
    checksum_path = dest_dir / "MANIFEST.sha256"

    _write_csv(
        source_dir / "sds_dataset_register.csv",
        [
            "identifier",
            "title",
            "indicator",
            "description",
            "dimension",
            "unitName",
            "unitType",
            "periodicity",
            "periodType",
            "sourceRef",
            "codeESRS",
            "codeGRI",
            "codeGRI_expanded",
            "evidencePath",
            "sourceRow",
            "owner",
            "accessRights",
            "validationMethod",
            "doubleMateriality",
            "valueType",
        ],
        [
            [
                "urn:sds:reg:esrs:e3_5",
                "Water consumption",
                "Water consumption",
                "Water disclosure",
                "E",
                "m3",
                "Volume",
                "annual",
                "fiscal_year",
                "Atomizer vNext",
                "E3-5",
                "",
                "",
                "",
                "1",
                "system",
                "Internal",
                "automated",
                "",
                "numeric",
            ]
        ],
    )
    _write_csv(
        source_dir / "sds_values.csv",
        [
            "concept",
            "entity",
            "period",
            "external_key",
            "value",
            "unit",
            "metadata_json",
        ],
        [
            [
                "csrd:E3_5",
                "test_company",
                "2024",
                "atomizer:test:e3-5:2024",
                "10",
                "m3",
                "{}",
            ]
        ],
    )

    manifest = sync_atomizer.sync_exports(
        source_dir=source_dir,
        dest_dir=dest_dir,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
        repo_root=tmp_path,
    )

    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    filenames = [item["filename"] for item in manifest["files"]]
    assert (dest_dir / "sds_values.csv").exists()
    assert filenames == ["sds_dataset_register.csv", "sds_values.csv"]
    assert persisted["policy"]["canonical_inputs"] == [
        "sds_dataset_register.csv",
        "sds_values.csv",
    ]
    assert "sds_values.csv" in checksum_path.read_text(encoding="utf-8")


def test_sync_exports_values_csv_allows_runtime_value_contract_columns(tmp_path):
    source_dir = tmp_path / "atomizer_register"
    dest_dir = tmp_path / "data" / "atomizer"
    manifest_path = dest_dir / "manifest.json"
    checksum_path = dest_dir / "MANIFEST.sha256"

    _write_csv(
        source_dir / "sds_dataset_register.csv",
        [
            "identifier",
            "title",
            "indicator",
            "description",
            "dimension",
            "unitName",
            "unitType",
            "periodicity",
            "periodType",
            "sourceRef",
            "codeESRS",
            "codeGRI",
            "codeGRI_expanded",
            "evidencePath",
            "sourceRow",
            "owner",
            "accessRights",
            "validationMethod",
            "doubleMateriality",
            "valueType",
        ],
        [
            [
                "urn:sds:reg:esrs:e1_6",
                "Scope 1 emissions",
                "Scope 1 emissions",
                "GHG disclosure",
                "E",
                "kg CO2e",
                "Emissions",
                "annual",
                "calendar_year",
                "Atomizer vNext",
                "E1-6",
                "",
                "",
                "",
                "1",
                "system",
                "Internal",
                "automated",
                "",
                "numeric",
            ]
        ],
    )
    _write_csv(
        source_dir / "sds_values.csv",
        list(ALLOWED_VALUE_COLUMNS),
        [
            [
                "urn:sds:disclosure:csrd:e1-6",
                "nordhaven_group",
                "2024",
                "42",
                "kg CO2e",
                "{}",
                "atomizer:nordhaven:e1-6:2024",
                "numeric",
                "EUR",
                "2024-12-31",
                "2024-01-01",
                "2024-12-31",
                "kg CO2e",
                "EUR",
                "ecb_reference",
            ]
        ],
    )

    manifest = sync_atomizer.sync_exports(
        source_dir=source_dir,
        dest_dir=dest_dir,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
        repo_root=tmp_path,
    )

    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    value_entry = next(
        item for item in persisted["files"] if item["filename"] == "sds_values.csv"
    )
    assert value_entry["headers"] == list(ALLOWED_VALUE_COLUMNS)
    assert value_entry["row_count"] == 1
    assert manifest["files"][1]["filename"] == "sds_values.csv"
    assert "sds_values.csv" in checksum_path.read_text(encoding="utf-8")


def test_sync_exports_register_package_can_include_calculation_contract(tmp_path):
    source_dir = tmp_path / "atomizer_register"
    dest_dir = tmp_path / "data" / "atomizer"
    manifest_path = dest_dir / "manifest.json"
    checksum_path = dest_dir / "MANIFEST.sha256"

    _write_csv(
        source_dir / "sds_dataset_register.csv",
        [
            "identifier",
            "title",
            "indicator",
            "description",
            "dimension",
            "unitName",
            "unitType",
            "periodicity",
            "periodType",
            "sourceRef",
            "codeESRS",
            "codeGRI",
            "codeGRI_expanded",
            "evidencePath",
            "sourceRow",
            "owner",
            "accessRights",
            "validationMethod",
            "doubleMateriality",
            "valueType",
        ],
        [
            [
                "urn:sds:reg:ghg:scope3_cat15_total",
                "Scope 3 Category 15 total",
                "Scope 3 Category 15 total",
                "Investments emissions",
                "E",
                "tCO2e",
                "GHGEmissions",
                "annual",
                "fiscal_year",
                "Atomizer vNext",
                "",
                "",
                "",
                "",
                "1",
                "system",
                "Internal",
                "automated",
                "",
                "numeric",
            ]
        ],
    )
    (source_dir / "sds_calculation_contract.json").write_text(
        json.dumps(
            {
                "contract_version": "1.0",
                "source_package": {"producer": "atomizer", "manifest_hash": "a" * 64},
                "nodes": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = sync_atomizer.sync_exports(
        source_dir=source_dir,
        dest_dir=dest_dir,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
        repo_root=tmp_path,
    )

    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    filenames = [item["filename"] for item in manifest["files"]]
    assert (dest_dir / "sds_calculation_contract.json").exists()
    assert filenames == ["sds_dataset_register.csv", "sds_calculation_contract.json"]
    assert persisted["policy"]["canonical_inputs"] == [
        "sds_dataset_register.csv",
        "sds_calculation_contract.json",
    ]
    assert "sds_calculation_contract.json" in checksum_path.read_text(encoding="utf-8")


def test_sync_exports_register_dry_run_validates_source_without_local_snapshot(
    tmp_path,
):
    source_dir = tmp_path / "atomizer_register"
    dest_dir = tmp_path / "data" / "atomizer"

    _write_csv(
        source_dir / "sds_dataset_register.csv",
        [
            "identifier",
            "title",
            "indicator",
            "description",
            "dimension",
            "unitName",
            "unitType",
            "periodicity",
            "periodType",
            "sourceRef",
            "codeESRS",
            "codeGRI",
            "codeGRI_expanded",
            "evidencePath",
            "sourceRow",
            "owner",
            "accessRights",
            "validationMethod",
            "doubleMateriality",
            "valueType",
        ],
        [
            [
                "urn:sds:reg:esrs:e1_1",
                "Energy use",
                "Energy use",
                "Energy disclosure",
                "E",
                "kWh",
                "Energy",
                "annual",
                "fiscal_year",
                "Atomizer vNext",
                "E1-1",
                "",
                "",
                "",
                "1",
                "system",
                "Internal",
                "automated",
                "",
                "numeric",
            ]
        ],
    )

    result = sync_atomizer.sync_exports(
        source_dir=source_dir,
        dest_dir=dest_dir,
        manifest_path=dest_dir / "manifest.json",
        checksum_path=dest_dir / "MANIFEST.sha256",
        repo_root=tmp_path,
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["contract_mode"] == "register"
    assert result["validated_files"] == ["sds_dataset_register.csv"]


def test_sync_exports_local_snapshot_dry_run_does_not_write_manifest(tmp_path):
    """codex F12 M1: --dry-run on the local snapshot (no --source-dir) must validate
    without writing manifest/checksum."""
    source_dir = tmp_path / "atomizer_exports"
    dest_dir = tmp_path / "data" / "atomizer"
    manifest_path = dest_dir / "manifest.json"
    checksum_path = dest_dir / "MANIFEST.sha256"

    _write_csv(
        source_dir / "framework_datapoints.csv",
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
        [["ESRS", "Set1", "E1-1", "Energy use", "d", "Numeric", "kWh", "Climate"]],
    )
    _write_csv(
        source_dir / "atomized_variables.csv",
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
                "In",
                "d",
                "kWh",
                "Energy",
                "TRUE",
                "FALSE",
                "",
            ]
        ],
    )

    # populate the local snapshot, then remove the manifest/checksum
    sync_atomizer.sync_exports(
        source_dir=source_dir,
        dest_dir=dest_dir,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
        repo_root=tmp_path,
    )
    manifest_path.unlink()
    checksum_path.unlink()

    result = sync_atomizer.sync_exports(
        source_dir=None,
        dest_dir=dest_dir,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
        repo_root=tmp_path,
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert "framework_datapoints.csv" in result["validated_files"]
    # the read-only dry-run must NOT have re-created the manifest/checksum
    assert not manifest_path.exists()
    assert not checksum_path.exists()
