from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTER_PATH = REPO_ROOT / "deliverables" / "deliverables-register.csv"
STATUS_SURFACE_PATH = (
    REPO_ROOT
    / "deliverables"
    / "evidence-public"
    / "dossier-traceability-status-sds-v2026-06-01.md"
)

STATUS_BLOCK_PATTERN = re.compile(
    r"<!--\s*subsidy-closure-status:v1\s*-->\s*```json\s*(?P<body>.*?)\s*```\s*"
    r"<!--\s*/subsidy-closure-status:v1\s*-->",
    re.DOTALL,
)

REQUIRED_WORK_PACKAGES = ("PT1", "PT2", "PT3", "PT4", "PT5", "PT6")
REQUIRED_ACTIVITY_IDS = (
    "A1.1",
    "A1.2",
    "A1.3",
    "A1.4",
    "A2.1",
    "A2.2",
    "A2.3",
    "A3.1",
    "A3.2",
    "A3.3",
    "A3.4",
    "A4.1",
    "A4.2",
    "A4.3",
    "A4.4",
    "A5.1",
    "A5.2",
    "A5.3",
    "A6.1",
    "A6.2",
    "A6.3",
)
REQUIRED_REQUIREMENT_IDS = tuple(f"R{i}" for i in range(1, 24))
REQUIRED_RESIDUAL_STATUSES = {
    "E02": "published-local",
    "E10": "published-local",
    "E11": "official-url-recorded",
}
REQUIRED_RESIDUAL_REQUIREMENTS = {
    "E02": ["R3"],
    "E10": ["R20"],
    "E11": ["R21"],
}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def load_status_surface(path: Path = STATUS_SURFACE_PATH) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = STATUS_BLOCK_PATTERN.search(text)
    if not match:
        raise ValueError(
            f"{path.relative_to(REPO_ROOT)} is missing subsidy-closure-status:v1 block"
        )
    loaded = json.loads(match.group("body"))
    if not isinstance(loaded, dict):
        raise ValueError("subsidy status block must be a JSON object")
    return loaded


def read_register(path: Path = REGISTER_PATH) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {row["deliverable_id"].strip(): row for row in rows}


def collect_issues(repo_root: Path = REPO_ROOT) -> list[str]:
    issues: list[str] = []
    status_path = (
        repo_root
        / "deliverables"
        / "evidence-public"
        / "dossier-traceability-status-sds-v2026-06-01.md"
    )
    register_path = repo_root / "deliverables" / "deliverables-register.csv"

    if not status_path.exists():
        return [f"missing {status_path.relative_to(repo_root)}"]
    if not register_path.exists():
        return [f"missing {register_path.relative_to(repo_root)}"]

    try:
        status = load_status_surface(status_path)
    except (json.JSONDecodeError, ValueError) as exc:
        return [str(exc)]

    register = read_register(register_path)

    if status.get("source_ref") != "SOURCE_DOSSIER_1":
        issues.append("status surface must use sanitized source_ref SOURCE_DOSSIER_1")
    if status.get("repo_publication_closure") != "pass":
        issues.append("repo_publication_closure must be pass")
    if status.get("full_subsidy_dossier_closure") != "not_achieved_conditional":
        issues.append(
            "full_subsidy_dossier_closure must be not_achieved_conditional"
        )

    activity_note = status.get("activity_count_note", {})
    if activity_note.get("source_summary_count") != 23:
        issues.append("activity_count_note.source_summary_count must be 23")
    if activity_note.get("explicit_activity_identifiers_recorded") != len(
        REQUIRED_ACTIVITY_IDS
    ):
        issues.append(
            "activity_count_note.explicit_activity_identifiers_recorded must be "
            f"{len(REQUIRED_ACTIVITY_IDS)}"
        )
    if "count discrepancy" not in str(activity_note.get("note", "")):
        issues.append("activity count discrepancy must be explicit")

    work_packages = _as_list(status.get("work_packages"))
    work_package_ids = tuple(item.get("id") for item in work_packages)
    if work_package_ids != REQUIRED_WORK_PACKAGES:
        issues.append(f"work_packages must be {list(REQUIRED_WORK_PACKAGES)}")

    activity_ids = {
        activity_id
        for wp in work_packages
        for activity_id in _as_list(wp.get("activity_ids"))
    }
    if tuple(sorted(activity_ids, key=lambda item: (item[1], int(item[3:])))) != (
        REQUIRED_ACTIVITY_IDS
    ):
        issues.append("activity identifiers must cover A1.1-A6.3 exactly")

    requirements = _as_list(status.get("requirements"))
    requirement_ids = tuple(item.get("id") for item in requirements)
    if requirement_ids != REQUIRED_REQUIREMENT_IDS:
        issues.append("requirements must cover R1-R23 in order")

    for requirement in requirements:
        deliverable_id = str(requirement.get("deliverable_id", ""))
        register_row = register.get(deliverable_id)
        if not register_row:
            issues.append(f"{requirement.get('id')}: unknown deliverable {deliverable_id}")
            continue
        if requirement.get("status") != register_row["status"]:
            issues.append(
                f"{requirement.get('id')}: status {requirement.get('status')} "
                f"does not match register {register_row['status']}"
            )
        evidence_path = repo_root / str(requirement.get("evidence_path", ""))
        if not evidence_path.exists():
            issues.append(
                f"{requirement.get('id')}: missing evidence_path "
                f"{requirement.get('evidence_path')}"
            )

    residuals = {
        str(item.get("deliverable_id")): item for item in _as_list(status.get("residuals"))
    }
    for deliverable_id, expected_status in REQUIRED_RESIDUAL_STATUSES.items():
        residual = residuals.get(deliverable_id)
        if not residual:
            issues.append(f"missing residual ledger entry for {deliverable_id}")
            continue
        register_row = register.get(deliverable_id)
        if not register_row:
            issues.append(f"{deliverable_id}: missing register row")
            continue
        if residual.get("status") != expected_status:
            issues.append(
                f"{deliverable_id}: residual status {residual.get('status')} "
                f"must be {expected_status}"
            )
        if residual.get("status") != register_row["status"]:
            issues.append(
                f"{deliverable_id}: residual status {residual.get('status')} "
                f"does not match register {register_row['status']}"
            )
        if residual.get("requirement_ids") != REQUIRED_RESIDUAL_REQUIREMENTS[deliverable_id]:
            issues.append(
                f"{deliverable_id}: residual requirement_ids "
                f"{residual.get('requirement_ids')} must be "
                f"{REQUIRED_RESIDUAL_REQUIREMENTS[deliverable_id]}"
            )
        if residual.get("repo_gate_status") != "pass":
            issues.append(f"{deliverable_id}: repo_gate_status must be pass")
        if residual.get("full_closure_status") != "conditional":
            issues.append(f"{deliverable_id}: full_closure_status must be conditional")
        if not str(residual.get("why_repo_gates_pass", "")).strip():
            issues.append(f"{deliverable_id}: why_repo_gates_pass is required")
        if not str(residual.get("decision_needed", "")).strip():
            issues.append(f"{deliverable_id}: decision_needed is required")

    return issues


def main() -> int:
    print("Subsidy closure boundary check")
    print("==============================")

    issues = collect_issues()
    if issues:
        for issue in issues:
            print(f"[FAIL] {issue}")
        print("")
        print("FAIL")
        return 1

    status = load_status_surface()
    residual_ids = ", ".join(REQUIRED_RESIDUAL_STATUSES)

    print(
        "[OK] status surface: "
        f"{STATUS_SURFACE_PATH.relative_to(REPO_ROOT)}"
    )
    print(f"[OK] work packages: {len(REQUIRED_WORK_PACKAGES)}")
    print(
        "[OK] activity identifiers: "
        f"{len(REQUIRED_ACTIVITY_IDS)} explicit identifiers recorded; "
        "source summary count discrepancy is explicit"
    )
    print(f"[OK] requirements: {len(REQUIRED_REQUIREMENT_IDS)}")
    print(f"[OK] residual ledger: {residual_ids}")
    print("")
    print("repo publication closure: PASS")
    print(
        "full subsidy/dossier closure: NOT_FULL_SUBSIDY_CLOSED "
        f"({status['full_subsidy_dossier_closure']})"
    )
    print("")
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
