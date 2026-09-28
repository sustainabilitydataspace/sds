from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "csv_to_json_indicators.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "csv_to_json_indicators_script", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


csv_to_json = _load_module()


def test_csv_to_json_canonicalizes_legacy_identifiers(tmp_path):
    csv_path = tmp_path / "e1_dataset_register.csv"
    json_path = tmp_path / "indicators.json"

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
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
        )
        writer.writeheader()
        writer.writerow(
            {
                "identifier": "urn:sds:reg:gri:gri 101_1_a",
                "title": "Biodiversity policy",
                "indicator": "Biodiversity policy",
                "description": "",
                "dimension": "E",
                "unitName": "",
                "unitType": "",
                "periodicity": "annual",
                "periodType": "fiscal_year",
                "sourceRef": "GRI Official",
                "codeESRS": "",
                "codeGRI": "GRI 101-1.a",
                "codeGRI_expanded": "",
                "evidencePath": "",
                "sourceRow": "1",
                "owner": "system",
                "accessRights": "Internal",
                "validationMethod": "automated",
                "doubleMateriality": "",
                "valueType": "numeric",
            }
        )

    csv_to_json.csv_to_json_indicators(csv_path, json_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["indicators"][0]["identifier"] == "urn:sds:reg:gri:gri_101_1_a"
    assert payload["indicators"][0]["id"] == "urn:sds:reg:gri:gri_101_1_a"
