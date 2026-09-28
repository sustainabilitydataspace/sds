#!/usr/bin/env python3
"""Evidence gate for E3 requirements R5 (coverage/hierarchy) and R7 (pilot)."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTER = (
    REPO_ROOT
    / "deliverables"
    / "E03-modelo-ngsi-ld"
    / "evidence"
    / "e03-dataset-register-v1-0.csv"
)
DEFAULT_GRAPH = REPO_ROOT / "data" / "extracted" / "analysis" / "ngsi_ld" / "entities.jsonld"
E3_EVIDENCE_DIR = REPO_ROOT / "deliverables" / "E03-modelo-ngsi-ld" / "evidence"
DEFAULT_R5_MATRIX = E3_EVIDENCE_DIR / "e3-r5-hierarchy-matrix-v1-0.csv"
DEFAULT_R5_REPORT = E3_EVIDENCE_DIR / "e3-r5-hierarchy-report-v1-0.json"
DEFAULT_R7_MATRIX = E3_EVIDENCE_DIR / "e3-r7-functional-pilot-matrix-v1-0.csv"
DEFAULT_R7_REPORT = E3_EVIDENCE_DIR / "e3-r7-functional-pilot-report-v1-0.json"
R5_MIN_COVERAGE = 0.80
R7_MIN_ACCURACY = 0.90
DEFAULT_PILOT_SIZE = 100


def _property(entity: dict, name: str):
    value = entity.get(name)
    return value.get("value") if isinstance(value, dict) else None


def _relationship(entity: dict, name: str):
    value = entity.get(name)
    if not isinstance(value, dict) or value.get("type") != "Relationship":
        return None
    return value.get("object")


def _indexes(graph_entities: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    datasets: dict[str, dict] = {}
    indicators: dict[str, dict] = {}
    for entity in graph_entities:
        if entity.get("type") == "Dataset":
            identifier = _property(entity, "identifier")
            if identifier:
                datasets[str(identifier)] = entity
        elif entity.get("type") == "Indicator" and entity.get("id"):
            indicators[str(entity["id"])] = entity
    return datasets, indicators


def evaluate_hierarchy(
    register_rows: list[dict[str, str]], graph_entities: list[dict]
) -> tuple[list[dict[str, str]], dict[str, int | float]]:
    datasets, indicators = _indexes(graph_entities)
    details: list[dict[str, str]] = []
    represented = 0
    hierarchy_valid = 0
    for row in register_rows:
        identifier = row["identifier"]
        dataset = datasets.get(identifier)
        errors: list[str] = []
        indicator_id = ""
        policy_count = 0
        if dataset is None:
            errors.append("dataset missing")
        else:
            represented += 1
            indicator_object = _relationship(dataset, "hasIndicator")
            if not isinstance(indicator_object, str):
                errors.append("hasIndicator relationship missing")
            else:
                indicator_id = indicator_object
                if indicator_object not in indicators:
                    errors.append("hasIndicator target missing")
            policies = _relationship(dataset, "hasPolicy")
            if isinstance(policies, list):
                policy_count = len(policies)
            elif isinstance(policies, str):
                policy_count = 1
            if policy_count < 1:
                errors.append("hasPolicy relationship missing")
            if not str(_property(dataset, "dimension") or "").strip():
                errors.append("dimension missing")
        status = "success" if not errors else "error"
        if status == "success":
            hierarchy_valid += 1
        details.append(
            {
                "identifier": identifier,
                "source_ref": row.get("sourceRef", ""),
                "dimension": row.get("dimension", ""),
                "dataset_id": str(dataset.get("id", "")) if dataset else "",
                "indicator_id": indicator_id,
                "policy_count": str(policy_count),
                "status": status,
                "error": "; ".join(errors),
            }
        )
    total = len(register_rows)
    return details, {
        "total_variables": total,
        "represented_variables": represented,
        "hierarchy_valid_variables": hierarchy_valid,
        "representation_rate": represented / total if total else 0.0,
        "hierarchy_rate": hierarchy_valid / total if total else 0.0,
    }


def select_pilot_rows(
    register_rows: list[dict[str, str]], pilot_size: int
) -> list[dict[str, str]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in register_rows:
        groups[row.get("sourceRef", "unknown")].append(row)
    for rows in groups.values():
        rows.sort(key=lambda row: row["identifier"])
    selected: list[dict[str, str]] = []
    keys = sorted(groups)
    offset = 0
    while len(selected) < min(pilot_size, len(register_rows)):
        progressed = False
        for key in keys:
            rows = groups[key]
            if offset < len(rows) and len(selected) < pilot_size:
                selected.append(rows[offset])
                progressed = True
        if not progressed:
            break
        offset += 1
    return selected


def evaluate_functional_pilot(
    pilot_rows: list[dict[str, str]], graph_entities: list[dict]
) -> tuple[list[dict[str, str]], dict[str, int | float]]:
    datasets, indicators = _indexes(graph_entities)
    matrix: list[dict[str, str]] = []
    success = 0
    for row in pilot_rows:
        identifier = row["identifier"]
        dataset = datasets.get(identifier)
        errors: list[str] = []
        indicator_id = ""
        observed_indicator = ""
        observed_title = ""
        observed_dimension = ""
        if dataset is None:
            errors.append("dataset missing")
        else:
            comparisons = {
                "title": (row.get("title", ""), _property(dataset, "title")),
                "description": (
                    row.get("description", ""),
                    _property(dataset, "description"),
                ),
                "dimension": (
                    row.get("dimension", ""),
                    _property(dataset, "dimension"),
                ),
                "codeESRS": (row.get("codeESRS", ""), _property(dataset, "codeESRS")),
                "codeGRI": (row.get("codeGRI", ""), _property(dataset, "codeGRI")),
                "owner": (row.get("owner", ""), _property(dataset, "owner")),
                "accessRights": (
                    row.get("accessRights", ""),
                    _property(dataset, "accessRights"),
                ),
            }
            for field, (expected, observed) in comparisons.items():
                if str(observed or "") != str(expected or ""):
                    errors.append(f"{field} mismatch")
            observed_title = str(_property(dataset, "title") or "")
            observed_dimension = str(_property(dataset, "dimension") or "")
            indicator_object = _relationship(dataset, "hasIndicator")
            if isinstance(indicator_object, str):
                indicator_id = indicator_object
                indicator = indicators.get(indicator_object)
                observed_indicator = str(_property(indicator or {}, "title") or "")
                if indicator is None:
                    errors.append("indicator target missing")
                elif observed_indicator != row.get("indicator", ""):
                    errors.append("indicator title mismatch")
            else:
                errors.append("hasIndicator relationship missing")
        status = "success" if not errors else "error"
        if status == "success":
            success += 1
        matrix.append(
            {
                "identifier": identifier,
                "source_ref": row.get("sourceRef", ""),
                "expected_title": row.get("title", ""),
                "observed_title": observed_title,
                "expected_dimension": row.get("dimension", ""),
                "observed_dimension": observed_dimension,
                "expected_indicator": row.get("indicator", ""),
                "observed_indicator": observed_indicator,
                "indicator_id": indicator_id,
                "status": status,
                "error": "; ".join(errors),
            }
        )
    total = len(pilot_rows)
    return matrix, {
        "pilot_rows": total,
        "success": success,
        "error": total - success,
        "accuracy": success / total if total else 0.0,
    }


def _read_register(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_graph(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    graph = payload.get("@graph")
    if not isinstance(graph, list):
        raise ValueError(f"{path}: @graph must be a list")
    return graph


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"cannot write empty evidence matrix: {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_gate(
    *,
    register_path: Path,
    graph_path: Path,
    pilot_size: int,
    r5_matrix_path: Path,
    r5_report_path: Path,
    r7_matrix_path: Path,
    r7_report_path: Path,
) -> tuple[dict, dict, list[str]]:
    register_rows = _read_register(register_path)
    graph_entities = _read_graph(graph_path)
    hierarchy_matrix, hierarchy_summary = evaluate_hierarchy(
        register_rows, graph_entities
    )
    pilot_rows = select_pilot_rows(register_rows, pilot_size)
    pilot_matrix, pilot_summary = evaluate_functional_pilot(pilot_rows, graph_entities)
    failures: list[str] = []
    if hierarchy_summary["representation_rate"] < R5_MIN_COVERAGE:
        failures.append("R5 representation rate below 80%")
    if hierarchy_summary["hierarchy_rate"] < R5_MIN_COVERAGE:
        failures.append("R5 hierarchy rate below 80%")
    if pilot_summary["accuracy"] < R7_MIN_ACCURACY:
        failures.append("R7 functional pilot accuracy below 90%")
    r5_report = {
        "gate": "e3_r5_hierarchy_coverage",
        "threshold": R5_MIN_COVERAGE,
        "summary": hierarchy_summary,
        "matrix_path": "deliverables/E03-modelo-ngsi-ld/evidence/e3-r5-hierarchy-matrix-v1-0.csv",
        "passed": not any(item.startswith("R5") for item in failures),
        "failures": [item for item in failures if item.startswith("R5")],
    }
    r7_report = {
        "gate": "e3_r7_functional_pilot",
        "threshold": R7_MIN_ACCURACY,
        "selection": "deterministic round-robin by sourceRef, identifier order",
        "summary": pilot_summary,
        "matrix_path": "deliverables/E03-modelo-ngsi-ld/evidence/e3-r7-functional-pilot-matrix-v1-0.csv",
        "passed": not any(item.startswith("R7") for item in failures),
        "failures": [item for item in failures if item.startswith("R7")],
    }
    _write_csv(r5_matrix_path, hierarchy_matrix)
    _write_json(r5_report_path, r5_report)
    _write_csv(r7_matrix_path, pilot_matrix)
    _write_json(r7_report_path, r7_report)
    return r5_report, r7_report, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", type=Path, default=DEFAULT_REGISTER)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--pilot-size", type=int, default=DEFAULT_PILOT_SIZE)
    parser.add_argument("--r5-matrix", type=Path, default=DEFAULT_R5_MATRIX)
    parser.add_argument("--r5-report", type=Path, default=DEFAULT_R5_REPORT)
    parser.add_argument("--r7-matrix", type=Path, default=DEFAULT_R7_MATRIX)
    parser.add_argument("--r7-report", type=Path, default=DEFAULT_R7_REPORT)
    args = parser.parse_args()
    r5, r7, failures = run_gate(
        register_path=args.register,
        graph_path=args.graph,
        pilot_size=args.pilot_size,
        r5_matrix_path=args.r5_matrix,
        r5_report_path=args.r5_report,
        r7_matrix_path=args.r7_matrix,
        r7_report_path=args.r7_report,
    )
    print("E3 R5/R7 evidence gate")
    print(
        f"  R5 represented: {r5['summary']['represented_variables']} / "
        f"{r5['summary']['total_variables']}"
    )
    print(f"  R5 hierarchy rate: {r5['summary']['hierarchy_rate']:.2%}")
    print(
        f"  R7 pilot: {r7['summary']['success']} / {r7['summary']['pilot_rows']}"
    )
    print(f"  R7 accuracy: {r7['summary']['accuracy']:.2%}")
    print("PASS" if not failures else "FAIL")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
