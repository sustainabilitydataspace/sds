from __future__ import annotations

import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTER_PATH = REPO_ROOT / "deliverables" / "deliverables-register.csv"
E7_CANONICAL_PATH = (
    "deliverables/E07-workshops/final/e07-actas-conclusiones-talleres-v1-0.md"
)
E7_README_PATH = "deliverables/E07-workshops/README.md"
E7_FEEDBACK_PATH = (
    "deliverables/E07-workshops/evidence/e07-feedback-traceability-v1-0.csv"
)
E7_README_REQUIRED_FRAGMENTS = (
    "# E07 - Workshops",
    "Status: `published-local`",
    E7_CANONICAL_PATH,
    E7_FEEDBACK_PATH,
)


def _read_register_rows(register_path: Path) -> list[dict[str, str]]:
    with register_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def collect_issues(repo_root: Path = REPO_ROOT) -> list[str]:
    issues: list[str] = []
    register_path = repo_root / "deliverables" / "deliverables-register.csv"
    if not register_path.exists():
        return ["missing deliverables/deliverables-register.csv"]

    rows = _read_register_rows(register_path)
    e7_rows = [row for row in rows if row.get("deliverable_id", "").strip() == "E07"]
    if len(e7_rows) != 1:
        return [f"expected exactly one E07 register row, found {len(e7_rows)}"]

    row = e7_rows[0]
    expected = {
        "status": "published-local",
        "canonical_path": E7_CANONICAL_PATH,
        "version": "V1.0",
        "date": "2026-06-18",
        "privacy_classification": "public",
        "private_source_ref": "SOURCE_E07_CONTROLLED_DOCX_20260710",
    }
    for field, expected_value in expected.items():
        actual = row.get(field, "").strip()
        if actual != expected_value:
            issues.append(
                f"E07 {field} must be {expected_value!r}, found {actual!r}"
            )

    for relative_path in (E7_CANONICAL_PATH, E7_README_PATH, E7_FEEDBACK_PATH):
        path = repo_root / relative_path
        if not path.exists():
            issues.append(f"missing {relative_path}")

    readme_path = repo_root / E7_README_PATH
    if readme_path.exists():
        readme_text = readme_path.read_text(encoding="utf-8", errors="ignore")
        for fragment in E7_README_REQUIRED_FRAGMENTS:
            if fragment not in readme_text:
                issues.append(f"E07 README missing required fragment: {fragment}")
        if "external-not-imported" in readme_text:
            issues.append("E07 README retains stale external-not-imported status")

    canonical_path = repo_root / E7_CANONICAL_PATH
    if canonical_path.exists():
        canonical_text = canonical_path.read_text(encoding="utf-8", errors="ignore")
        for forbidden in ("sharepoint.com", "external-not-imported"):
            if forbidden.casefold() in canonical_text.casefold():
                issues.append(f"E07 canonical artifact contains forbidden marker: {forbidden}")
        for required in ("F01", "F10", "2025-11-25", "2026-06-18"):
            if required not in canonical_text:
                issues.append(f"E07 canonical artifact missing required marker: {required}")

    feedback_path = repo_root / E7_FEEDBACK_PATH
    if feedback_path.exists():
        with feedback_path.open(newline="", encoding="utf-8") as handle:
            feedback_rows = list(csv.DictReader(handle))
        feedback_ids = [item.get("feedback_id", "").strip() for item in feedback_rows]
        expected_ids = [f"F{index:02d}" for index in range(1, 11)]
        if feedback_ids != expected_ids:
            issues.append(
                "E07 feedback traceability must contain ordered IDs F01-F10"
            )
        for item in feedback_rows:
            if not all(
                (item.get(field) or "").strip()
                for field in (
                    "workshop_id",
                    "workshop_date",
                    "stakeholder_category",
                    "observation",
                    "e8_case",
                    "e9_adjustment",
                    "evidence_boundary",
                )
            ):
                issues.append(
                    f"E07 feedback row {item.get('feedback_id', '')!r} is incomplete"
                )

    return issues


def main() -> int:
    issues = collect_issues()
    if issues:
        for issue in issues:
            print(f"[FAIL] {issue}")
        print("")
        print("FAIL")
        return 1

    print("E7 workshops status gate")
    print("========================")
    print("[OK] E07 register row marks the identified evidence artifact as published-local")
    print(f"[OK] E07 canonical path is {E7_CANONICAL_PATH}")
    print("[OK] E07 public README points to the canonical minutes and feedback matrix")
    print("[OK] E07 feedback traceability contains complete F01-F10 rows")
    print("")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
