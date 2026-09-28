from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "atomizer_packages"
    / "calculation-contract-regression-v1"
)

REQUIRED_PACKAGE_FILES = {
    "sds_dataset_register.csv",
    "sds_calculation_contract.json",
    "sds_values.csv",
}

HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_csv(name: str) -> list[dict[str, str]]:
    with (FIXTURE_DIR / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _nodes_by_datapoint(contract: dict) -> dict[str, dict]:
    return {node["canonical_datapoint_id"]: node for node in contract["nodes"]}


def _assert_contract_graph_is_acyclic(nodes: dict[str, dict]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(datapoint_id: str) -> None:
        if datapoint_id in visited:
            return
        assert datapoint_id not in visiting, f"cycle detected at {datapoint_id}"
        visiting.add(datapoint_id)
        node = nodes[datapoint_id]
        for component_id in node.get("formula", {}).get("component_ids", []):
            if component_id in nodes:
                visit(component_id)
        visiting.remove(datapoint_id)
        visited.add(datapoint_id)

    for datapoint_id in nodes:
        visit(datapoint_id)


def test_atomizer_calculation_fixture_package_manifest_is_complete_and_hashed():
    manifest = _load_json("manifest.json")
    checksum_lines = (FIXTURE_DIR / "MANIFEST.sha256").read_text(encoding="utf-8")

    manifest_files = {item["filename"]: item for item in manifest["files"]}
    assert set(manifest_files) == REQUIRED_PACKAGE_FILES
    assert manifest["contract_mode"] == "register_with_calculation_contract"
    assert manifest["policy"]["canonical_inputs"] == [
        "sds_dataset_register.csv",
        "sds_calculation_contract.json",
        "sds_values.csv",
    ]

    for filename, item in manifest_files.items():
        package_path = FIXTURE_DIR / filename
        assert package_path.exists(), filename
        assert item["path"] == filename
        assert HEX_64.match(item["sha256"])
        assert item["sha256"] == _sha256(package_path)
        assert f"{item['sha256']} *{filename}" in checksum_lines


def test_register_public_nodes_and_runtime_support_nodes_have_separate_exposure():
    register_rows = _load_csv("sds_dataset_register.csv")
    contract = _load_json("sds_calculation_contract.json")
    nodes = _nodes_by_datapoint(contract)
    register_identifiers = {row["identifier"] for row in register_rows}

    public_nodes = [
        node for node in nodes.values() if node["exposure"] == "public_register"
    ]
    runtime_support_nodes = [
        node for node in nodes.values() if node["exposure"] == "runtime_support"
    ]

    assert public_nodes
    assert runtime_support_nodes
    assert all(
        node["indicator_identifier"] in register_identifiers for node in public_nodes
    )
    assert all(node["indicator_identifier"] is None for node in runtime_support_nodes)
    assert all(
        node["canonical_datapoint_id"] not in register_identifiers
        for node in runtime_support_nodes
    )

    aggregate = nodes["fixture_scope1_total_emissions"]
    assert aggregate["runtime_status"] == "executable"
    assert aggregate["formula"]["kind"] == "sum"
    assert set(aggregate["formula"]["component_ids"]) == {
        "fixture_scope1_stationary_tco2e",
        "fixture_scope1_mobile_tco2e",
    }
    assert {
        component["component_scope"]
        for component in aggregate["formula"]["component_refs"]
    } == {"internal_node"}


def test_contract_fixture_declares_runtime_invariants_without_name_inference():
    contract = _load_json("sds_calculation_contract.json")
    nodes = _nodes_by_datapoint(contract)
    register_identifiers = {
        row["identifier"] for row in _load_csv("sds_dataset_register.csv")
    }
    contract_identifiers = {
        node["indicator_identifier"]
        for node in nodes.values()
        if node.get("indicator_identifier")
    }

    for node in nodes.values():
        formula = node["formula"]
        if formula["kind"] in {"sum", "formula", "ratio"}:
            assert formula["component_ids"], node["canonical_datapoint_id"]
        assert node["aggregation"]["missing_data"] in {
            "BLOCK",
            "WARN",
            "ALLOW_PARTIAL_WITH_SCORE",
            "EXCLUDE_NOT_APPLICABLE",
        }
        assert isinstance(node["aggregation"]["zero_is_value"], bool)
        assert node["trace_labels"]

    ratio = nodes["fixture_scope1_emissions_intensity"]
    assert ratio["formula"]["kind"] == "ratio"
    assert (
        ratio["formula"]["numerator_component_id"] == "fixture_scope1_total_emissions"
    )
    assert ratio["formula"]["denominator_component_id"] == "fixture_revenue_eur_m"
    assert ratio["aggregation"]["ratio"]["recompute_from_components"] is True

    average = nodes["fixture_average_headcount"]
    assert average["aggregation"]["temporal"] == "AVERAGE_OVER_PERIOD"
    assert "temporal:AVERAGE_OVER_PERIOD" in average["trace_labels"]

    last = nodes["fixture_year_end_employees"]
    assert last["aggregation"]["temporal"] == "LAST_VALUE"
    assert "temporal:LAST_VALUE" in last["trace_labels"]

    total = nodes["fixture_scope1_total_emissions"]
    assert total["aggregation"]["unit_normalization"] == {
        "target_unit": "tCO2e",
        "compatible_units": ["tCO2e", "kgCO2e"],
    }
    assert "unit:tCO2e" in total["trace_labels"]

    assert "urn:sds:reg:test:catalog_only_without_contract" in register_identifiers
    assert "urn:sds:reg:test:catalog_only_without_contract" not in contract_identifiers
    _assert_contract_graph_is_acyclic(nodes)


def test_values_fixture_preserves_zero_missing_and_mixed_unit_regressions():
    values = _load_csv("sds_values.csv")

    assert any(
        row["concept"] == "fixture_scope1_mobile_tco2e"
        and row["entity"] == "fixture_entity_zero"
        and row["value"] == "0"
        and row["metadata_json"].find('"observed": true') >= 0
        for row in values
    )
    assert not any(
        row["concept"] == "fixture_scope1_mobile_tco2e"
        and row["entity"] == "fixture_entity_missing"
        for row in values
    )

    mobile_units = {
        row["unit"] for row in values if row["concept"] == "fixture_scope1_mobile_tco2e"
    }
    assert {"kgCO2e", "tCO2e"}.issubset(mobile_units)

    assert all(row["value"] != "" for row in values)
    assert any(
        row["concept"] == "fixture_average_headcount" and row["period"] == "2024-02"
        for row in values
    )
    assert any(
        row["concept"] == "fixture_year_end_employees" and row["period"] == "2024-12-31"
        for row in values
    )
