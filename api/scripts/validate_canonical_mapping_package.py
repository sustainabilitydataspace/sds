#!/usr/bin/env python3
"""Validate a canonical Sygris mapping package without database writes."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.services.canonical_mapping_import import validate_canonical_mapping_package


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Shadow/report-only validation for canonical mapping packages. "
            "No database writes are performed."
        )
    )
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full validation report as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report = validate_canonical_mapping_package(args.package_dir)

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        status = "PASS" if report.valid else "FAIL"
        print(f"{status}: canonical mapping package validation")
        print(f"mode: {report.mode}")
        print(f"operational_eligible: {report.operational_eligible}")
        print(f"package_schema_version: {report.package_schema_version}")
        for filename, count in sorted(report.file_counts.items()):
            print(f"- {filename}: {count} rows")
        if report.relationship_type_counts:
            print(
                "relationship_type_counts: "
                f"{json.dumps(report.relationship_type_counts, sort_keys=True)}"
            )
        if report.operational_blockers:
            print("operational_blockers:")
            for blocker in report.operational_blockers:
                print(f"- {blocker}")
        if report.errors:
            print("errors:")
            for issue in report.errors[:20]:
                location = f":{issue.row_number}" if issue.row_number else ""
                print(f"- {issue.file}{location} [{issue.code}] {issue.message}")
        if report.warnings:
            print("warnings:")
            for issue in report.warnings[:20]:
                location = f":{issue.row_number}" if issue.row_number else ""
                print(f"- {issue.file}{location} [{issue.code}] {issue.message}")

    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
