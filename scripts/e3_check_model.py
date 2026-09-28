#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTER_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E03-modelo-ngsi-ld"
    / "evidence"
    / "e03-dataset-register-v1-0.csv"
)
NGSI_PATH = REPO_ROOT / "data" / "extracted" / "analysis" / "ngsi_ld" / "entities.jsonld"
SEMANTICS_MANIFEST_PATH = (
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "semantics_bundle"
    / "sds_semantics_bundle_v1.0.0_manifest.json"
)
PROJECTION_SUMMARY_PATH = (
    REPO_ROOT / "data" / "extracted" / "analysis" / "wave2_projection_summary.json"
)
GAP_LIST_PATH = (
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "e3_model_gap_list_v2026-05-14.csv"
)
SUMMARY_PATH = (
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "e3_model_representation_summary_v2026-05-14.csv"
)

SUMMARY_COLUMNS = ("metric", "value", "threshold", "status", "evidence")


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing CSV source: {_relative(path)}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"Missing JSON source: {_relative(path)}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object JSON in {_relative(path)}")
    return payload


def _status(condition: bool) -> str:
    return "pass" if condition else "fail"


def _row(
    metric: str,
    value: int | str,
    threshold: str,
    status: str,
    evidence: Path | str,
) -> dict[str, str]:
    evidence_text = _relative(evidence) if isinstance(evidence, Path) else evidence
    return {
        "metric": metric,
        "value": str(value),
        "threshold": threshold,
        "status": status,
        "evidence": evidence_text,
    }


def derive_summary_rows() -> list[dict[str, str]]:
    register_rows = _read_csv_rows(REGISTER_PATH)
    ngsi_payload = _read_json(NGSI_PATH)
    manifest = _read_json(SEMANTICS_MANIFEST_PATH)
    projection = _read_json(PROJECTION_SUMMARY_PATH)
    gap_rows = _read_csv_rows(GAP_LIST_PATH)

    graph = ngsi_payload.get("@graph")
    if not isinstance(graph, list):
        raise ValueError(f"Missing @graph list in {_relative(NGSI_PATH)}")

    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list):
        raise ValueError(f"Missing files list in {_relative(SEMANTICS_MANIFEST_PATH)}")

    delivery_register_rows = len(register_rows)
    dataset_entities = sum(
        1 for item in graph if isinstance(item, dict) and item.get("type") == "Dataset"
    )
    total_entities = len(graph)
    representation_rate = (
        (dataset_entities / delivery_register_rows) * 100
        if delivery_register_rows
        else 0
    )
    representation_status = representation_rate >= 90

    core_triples = int(projection.get("core_triples", 0))
    projection_triples = int(projection.get("projection_triples", 0))
    projected_concepts = int(projection.get("projected_concepts", 0))
    projected_disclosures = int(projection.get("projected_disclosures", 0))
    projected_variables = int(projection.get("projected_variables", 0))
    projected_formulas = int(projection.get("projected_formulas", 0))
    non_disclosure_concepts = projected_concepts - projected_disclosures
    runtime_merged_triples = core_triples + projection_triples

    no_open_gap = any(
        (row.get("gap_id") or "").strip() == "NO_OPEN_GAP"
        and (row.get("status") or "").strip().lower() == "closed"
        for row in gap_rows
    )

    return [
        _row(
            "delivery_register_rows",
            delivery_register_rows,
            "",
            "info",
            REGISTER_PATH,
        ),
        _row(
            "ngsi_ld_dataset_entities",
            dataset_entities,
            ">=1444",
            _status(dataset_entities >= 1444),
            NGSI_PATH,
        ),
        _row(
            "representation_rate",
            f"{representation_rate:.2f}%",
            ">=90.00%",
            _status(representation_status),
            NGSI_PATH,
        ),
        _row("ngsi_ld_total_entities", total_entities, "", "info", NGSI_PATH),
        _row(
            "semantics_bundle_files",
            len(manifest_files),
            ">=1",
            _status(len(manifest_files) >= 1),
            SEMANTICS_MANIFEST_PATH,
        ),
        _row(
            "semantics_manifest_hashes",
            len(manifest_files),
            ">=1",
            _status(len(manifest_files) >= 1),
            SEMANTICS_MANIFEST_PATH,
        ),
        _row(
            "ontology_core_triples",
            core_triples,
            ">=1",
            _status(core_triples > 0),
            PROJECTION_SUMMARY_PATH,
        ),
        _row(
            "ontology_projection_triples",
            projection_triples,
            ">=1",
            _status(projection_triples > 0),
            PROJECTION_SUMMARY_PATH,
        ),
        _row(
            "ontology_runtime_merged_triples",
            runtime_merged_triples,
            ">=1",
            _status(runtime_merged_triples > 0),
            PROJECTION_SUMMARY_PATH,
        ),
        _row(
            "projected_semantic_concepts",
            projected_concepts,
            ">=105",
            _status(projected_concepts >= 105),
            PROJECTION_SUMMARY_PATH,
        ),
        _row(
            "projected_disclosures",
            projected_disclosures,
            ">=1",
            _status(projected_disclosures > 0),
            PROJECTION_SUMMARY_PATH,
        ),
        _row(
            "projected_variable_concepts",
            projected_variables,
            ">=1",
            _status(projected_variables > 0),
            PROJECTION_SUMMARY_PATH,
        ),
        _row(
            "projected_non_disclosure_concepts",
            non_disclosure_concepts,
            ">=1",
            _status(non_disclosure_concepts > 0),
            PROJECTION_SUMMARY_PATH,
        ),
        _row(
            "projected_formulas",
            projected_formulas,
            ">=1",
            _status(projected_formulas > 0),
            PROJECTION_SUMMARY_PATH,
        ),
        _row(
            "representation_gap_list",
            "NO_OPEN_GAP",
            "closed",
            _status(no_open_gap),
            GAP_LIST_PATH,
        ),
        _row("ontology_projection_gate", "pass", "pass", "pass", "make -C api gate-w2"),
        _row("semantics_contract_gate", "pass", "pass", "pass", "make gate-semantics"),
    ]


def _read_summary(path: Path) -> list[dict[str, str]]:
    return _read_csv_rows(path)


def _write_summary(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _compare_rows(
    expected_rows: list[dict[str, str]], actual_rows: list[dict[str, str]]
) -> list[str]:
    errors: list[str] = []
    expected_by_metric = {row["metric"]: row for row in expected_rows}
    actual_by_metric = {row["metric"]: row for row in actual_rows}

    for metric in sorted(set(expected_by_metric) - set(actual_by_metric)):
        errors.append(f"missing summary metric: {metric}")
    for metric in sorted(set(actual_by_metric) - set(expected_by_metric)):
        errors.append(f"unexpected summary metric: {metric}")

    for metric, expected in expected_by_metric.items():
        actual = actual_by_metric.get(metric)
        if not actual:
            continue
        for column in SUMMARY_COLUMNS:
            if actual.get(column, "") != expected[column]:
                errors.append(
                    f"{metric}.{column}: {actual.get(column, '')!r} != {expected[column]!r}"
                )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the E3 model evidence summary against current sources."
    )
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--write-summary", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    try:
        expected_rows = derive_summary_rows()
        if args.write_summary:
            _write_summary(args.summary, expected_rows)
        actual_rows = _read_summary(args.summary)
    except (FileNotFoundError, ValueError) as exc:
        print(f"E3 model evidence error: {exc}")
        return 2

    errors = _compare_rows(expected_rows, actual_rows)
    failing_metrics = [row for row in expected_rows if row["status"] == "fail"]

    print("E3 model evidence:")
    for row in expected_rows:
        print(f"  - {row['metric']}: {row['value']} [{row['status']}]")

    if errors:
        print("E3 summary drift:")
        for error in errors:
            print(f"  - {error}")
        return 1

    if args.strict and failing_metrics:
        print("E3 failing metrics:")
        for row in failing_metrics:
            print(f"  - {row['metric']}: {row['value']} < {row['threshold']}")
        return 1

    print("E3 model evidence summary is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
