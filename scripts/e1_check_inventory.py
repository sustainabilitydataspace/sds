from __future__ import annotations

import argparse
import csv
from decimal import Decimal
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = (
    REPO_ROOT
    / "deliverables"
    / "E01-mapeo-datos-normativas"
    / "evidence"
    / "dimension-expanded-value-coordinate-calculation-v2026-06-05.csv"
)
REQUIRED_FILES = [
    REPO_ROOT
    / "deliverables"
    / "E01-mapeo-datos-normativas"
    / "final"
    / "e01-inventario-datos-clave-variables-brutas-v2026-06-09.md",
    REPO_ROOT
    / "deliverables"
    / "E01-mapeo-datos-normativas"
    / "evidence"
    / "dimension-expanded-value-coordinate-annex-v2026-06-05.md",
    REPO_ROOT
    / "deliverables"
    / "E01-mapeo-datos-normativas"
    / "evidence"
    / "e01-inventario-variables-clave-v2026-06-12.csv",
    DEFAULT_LEDGER,
]
RETIRED_LEGACY_GLOBS = [
    "data/extracted/analysis/e1_dataset_register_V*.csv",
    "data/extracted/analysis/e1_coverage_V*.csv",
    "data/extracted/analysis/e1_normative_coverage_reviewer_V*.csv",
]
EXPECTED_REPORTABLE_BY_STANDARD = {
    "ESRS": 82_858,
    "GRI": 4_399,
    "GHG Protocol": 290,
}
EXPECTED_TECHNICAL_BY_STANDARD = {
    "ESRS": 82_858,
    "GRI": 4_985,
    "GHG Protocol": 554,
}
EXPECTED_REPORTABLE_TOTAL = 87_547
EXPECTED_TECHNICAL_TOTAL = 88_397


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the current E1 dimension-expanded evidence pack."
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--coverage", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--threshold", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def _parse_int(value: str) -> int:
    cleaned = (value or "0").strip()
    if not cleaned:
        return 0
    return int(Decimal(cleaned))


def _sum_coordinates(path: Path) -> tuple[dict[str, int], dict[str, int], int]:
    reportable: dict[str, int] = {}
    technical: dict[str, int] = {}
    row_count = 0

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required_columns = {
            "standard",
            "reportable_coordinates",
            "technical_coordinates",
        }
        missing_columns = required_columns - set(reader.fieldnames or [])
        if missing_columns:
            raise ValueError(f"missing ledger columns: {sorted(missing_columns)}")

        for row in reader:
            row_count += 1
            standard = (row.get("standard") or "").strip()
            if not standard:
                raise ValueError(f"missing standard at ledger row {row_count}")
            reportable[standard] = reportable.get(standard, 0) + _parse_int(
                row.get("reportable_coordinates", "")
            )
            technical[standard] = technical.get(standard, 0) + _parse_int(
                row.get("technical_coordinates", "")
            )

    return reportable, technical, row_count


def _print_totals(title: str, totals: dict[str, int]) -> None:
    print(title)
    for standard in ("ESRS", "GRI", "GHG Protocol"):
        print(f"  - {standard}: {totals.get(standard, 0):,}")


def _compare_totals(label: str, actual: dict[str, int], expected: dict[str, int]) -> bool:
    ok = True
    for standard, expected_value in expected.items():
        actual_value = actual.get(standard, 0)
        if actual_value != expected_value:
            print(
                f"[MISMATCH] {label} {standard}: "
                f"{actual_value:,} != {expected_value:,}"
            )
            ok = False
    extra = sorted(set(actual) - set(expected))
    if extra:
        print(f"[MISMATCH] unexpected {label} standards: {extra}")
        ok = False
    return ok


def main() -> int:
    args = parse_args()

    ok = True

    for path in REQUIRED_FILES:
        if path.exists():
            print(f"[OK] required file: {path.relative_to(REPO_ROOT)}")
        else:
            print(f"[MISSING] required file: {path.relative_to(REPO_ROOT)}")
            ok = False

    for pattern in RETIRED_LEGACY_GLOBS:
        for path in REPO_ROOT.glob(pattern):
            if not path.is_file():
                continue
            print(
                "[RETIRED] legacy E1 coverage file must not be active: "
                f"{path.relative_to(REPO_ROOT)}"
            )
            ok = False

    if not args.ledger.exists():
        print(f"[MISSING] dimension ledger: {args.ledger.relative_to(REPO_ROOT)}")
        return 1

    try:
        reportable, technical, row_count = _sum_coordinates(args.ledger)
    except ValueError as exc:
        print(f"[INVALID] dimension ledger: {exc}")
        return 1

    print("")
    print(f"E1 dimension ledger rows: {row_count:,}")
    _print_totals("E1 reportable closed-dimension value coordinates:", reportable)
    _print_totals("E1 broader technical-capacity value coordinates:", technical)

    print("")
    reportable_total = sum(reportable.values())
    technical_total = sum(technical.values())
    print(f"E1 reportable total: {reportable_total:,}")
    print(f"E1 technical total: {technical_total:,}")

    if not _compare_totals("reportable", reportable, EXPECTED_REPORTABLE_BY_STANDARD):
        ok = False
    if not _compare_totals("technical", technical, EXPECTED_TECHNICAL_BY_STANDARD):
        ok = False
    if reportable_total != EXPECTED_REPORTABLE_TOTAL:
        print(
            f"[MISMATCH] reportable total: "
            f"{reportable_total:,} != {EXPECTED_REPORTABLE_TOTAL:,}"
        )
        ok = False
    if technical_total != EXPECTED_TECHNICAL_TOTAL:
        print(
            f"[MISMATCH] technical total: "
            f"{technical_total:,} != {EXPECTED_TECHNICAL_TOTAL:,}"
        )
        ok = False

    if args.strict and not ok:
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
