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
    / "dossier-closure-status-sds-v2026-09-18.md"
)
STATUS_BLOCK_PATTERN = re.compile(
    r"<!--\s*subsidy-closure-status:v2\s*-->\s*```json\s*(?P<body>.*?)\s*```\s*"
    r"<!--\s*/subsidy-closure-status:v2\s*-->",
    re.DOTALL,
)
REQUIRED_WORK_PACKAGES = ("PT1", "PT2", "PT3", "PT4", "PT5", "PT6")
REQUIRED_REQUIREMENT_IDS = tuple(f"R{i}" for i in range(1, 24))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def load_status_surface(path: Path = STATUS_SURFACE_PATH) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = STATUS_BLOCK_PATTERN.search(text)
    if not match:
        raise ValueError(
            f"{path.relative_to(REPO_ROOT)} is missing subsidy-closure-status:v2 block"
        )
    loaded = json.loads(match.group("body"))
    if not isinstance(loaded, dict):
        raise ValueError("subsidy status block must be a JSON object")
    return loaded


def read_register(path: Path = REGISTER_PATH) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["deliverable_id"].strip(): row for row in csv.DictReader(handle)}


def collect_issues(repo_root: Path = REPO_ROOT) -> list[str]:
    issues: list[str] = []
    status_path = repo_root / "deliverables" / "evidence-public" / STATUS_SURFACE_PATH.name
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

    if status.get("schema_version") != 2:
        issues.append("schema_version must be 2")
    if status.get("status_as_of") != "2026-09-18":
        issues.append("status_as_of must be 2026-09-18")
    if status.get("repo_publication_closure") != "pass":
        issues.append("repo_publication_closure must be pass")
    if status.get("full_subsidy_dossier_closure") != "achieved":
        issues.append("full_subsidy_dossier_closure must be achieved")
    decision = status.get("closure_decision", {})
    if decision.get("authority") != "project_owner" or decision.get("decision") != "accepted_complete":
        issues.append("closure_decision must record project-owner accepted completion")

    work_packages = _as_list(status.get("work_packages"))
    if tuple(item.get("id") for item in work_packages) != REQUIRED_WORK_PACKAGES:
        issues.append(f"work_packages must be {list(REQUIRED_WORK_PACKAGES)}")
    for work_package in work_packages:
        if work_package.get("closure_state") != "closed":
            issues.append(f"{work_package.get('id')}: closure_state must be closed")

    requirements = _as_list(status.get("requirements"))
    if tuple(item.get("id") for item in requirements) != REQUIRED_REQUIREMENT_IDS:
        issues.append("requirements must cover R1-R23 in order")
    for requirement in requirements:
        deliverable_id = str(requirement.get("deliverable_id", ""))
        if deliverable_id not in register:
            issues.append(f"{requirement.get('id')}: unknown deliverable {deliverable_id}")
        if requirement.get("closure_state") != "closed":
            issues.append(f"{requirement.get('id')}: closure_state must be closed")

    if _as_list(status.get("residuals")):
        issues.append("residuals must be empty after accepted complete closure")
    if register.get("E11", {}).get("status") != "published-local":
        issues.append("E11 register status must be published-local")
    return issues


def main() -> int:
    print("Subsidy closure check")
    print("====================")
    issues = collect_issues()
    if issues:
        for issue in issues:
            print(f"[FAIL] {issue}")
        print("\nFAIL")
        return 1

    status = load_status_surface()
    print(f"[OK] status surface: {STATUS_SURFACE_PATH.relative_to(REPO_ROOT)}")
    print("[OK] work packages: 6 closed")
    print("[OK] requirements: R1-R23 closed")
    print("[OK] residual ledger: empty")
    print("\nrepo publication closure: PASS")
    print(f"full subsidy/dossier closure: CLOSED ({status['full_subsidy_dossier_closure']})")
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
