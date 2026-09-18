#!/usr/bin/env python3
"""Validate E12/R22 coverage of every planned E10 communication activity."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = (
    REPO_ROOT
    / "deliverables"
    / "E12-communications-impact"
    / "evidence"
    / "e12-plan-execution-traceability-v1-0.csv"
)
DEFAULT_REPORT = (
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "e12_r22_activity_coverage_report.json"
)
EXPECTED_PLANNED_ACTION_IDS = tuple(f"A{i:02d}" for i in range(1, 17))
EXPECTED_EXECUTED_EXTRA_IDS = ("X01",)
EXPECTED_ACTION_IDS = EXPECTED_PLANNED_ACTION_IDS + EXPECTED_EXECUTED_EXTRA_IDS
ALLOWED_STATUSES = {
    "executed_evidenced",
    "partially_evidenced",
    "not_evidenced",
}


def validate_matrix(matrix_path: Path, repo_root: Path = REPO_ROOT) -> tuple[dict, list[str]]:
    with matrix_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    failures: list[str] = []
    action_ids = tuple(row.get("action_id", "") for row in rows)
    if action_ids != EXPECTED_ACTION_IDS:
        failures.append(f"action IDs must be {list(EXPECTED_ACTION_IDS)}")
    documented = 0
    for row in rows:
        action_id = row.get("action_id", "<missing>")
        required = ("phase", "planned_action", "execution_status", "evidence_path", "observed_result", "limitation")
        missing = [field for field in required if not str(row.get(field, "")).strip()]
        if missing:
            failures.append(f"{action_id}: missing fields {missing}")
            continue
        documented += 1
        if row["execution_status"] not in ALLOWED_STATUSES:
            failures.append(f"{action_id}: unsupported execution_status {row['execution_status']}")
        for evidence in row["evidence_path"].split(";"):
            relative = evidence.strip()
            if relative and not (repo_root / relative).exists():
                failures.append(f"{action_id}: missing evidence path {relative}")
    total = len(rows)
    summary = {
        "planned_actions": len(EXPECTED_PLANNED_ACTION_IDS),
        "executed_activities_not_separate_in_plan": len(EXPECTED_EXECUTED_EXTRA_IDS),
        "required_activity_records": len(EXPECTED_ACTION_IDS),
        "matrix_rows": total,
        "documented_actions": documented,
        "documentation_coverage": documented / len(EXPECTED_ACTION_IDS),
        "status_counts": dict(sorted(Counter(row.get("execution_status", "") for row in rows).items())),
    }
    report = {
        "gate": "e12_r22_activity_coverage",
        "criterion": "document 100% of planned and executed communication activities",
        "matrix_path": "deliverables/E12-communications-impact/evidence/e12-plan-execution-traceability-v1-0.csv",
        "summary": summary,
        "passed": not failures and summary["documentation_coverage"] == 1.0,
        "failures": failures,
    }
    return report, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report, failures = validate_matrix(args.matrix)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = report["summary"]
    print("E12 R22 activity coverage gate")
    print(f"  documented: {summary['documented_actions']} / {summary['required_activity_records']}")
    print(f"  coverage: {summary['documentation_coverage']:.2%}")
    print(f"  statuses: {summary['status_counts']}")
    print("PASS" if report["passed"] else "FAIL")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
