#!/usr/bin/env python3
"""Inspect canonical mapping package assertions without database writes."""

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

from src.services.canonical_mapping_inspection import (
    inspect_canonical_mapping_package,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect canonical mapping package assertion candidates. "
            "No database writes are performed."
        )
    )
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument(
        "--include-operational",
        action="store_true",
        help="include operationally eligible assertions as well as review-only ones",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report = inspect_canonical_mapping_package(
        args.package_dir,
        include_operational=args.include_operational,
        limit=args.limit,
    )
    payload = report.as_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        print("Canonical mapping package inspection")
        print(f"  valid: {payload['valid']}")
        print(f"  operational_eligible: {payload['operational_eligible']}")
        print(f"  candidate_count: {payload['candidate_count']}")
        print(
            "  non_operational_candidate_count: "
            f"{payload['non_operational_candidate_count']}"
        )
        print(
            "  relationship_type_counts: "
            f"{json.dumps(payload['relationship_type_counts'], sort_keys=True)}"
        )
        print(
            "  target_identity_status_counts: "
            f"{json.dumps(payload['target_identity_status_counts'], sort_keys=True)}"
        )
        for candidate in payload["candidates"][:10]:
            print(
                "  - "
                f"{candidate['source_standard_id']} {candidate['source_code']} "
                f"{candidate['relationship_type']} "
                f"target_identity={candidate['target_identity_status']}"
            )
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
