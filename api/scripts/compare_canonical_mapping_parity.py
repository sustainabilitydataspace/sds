#!/usr/bin/env python3
"""Compare legacy standard mappings with canonical materialized pairwise mappings."""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.database.init_db import init_db_for_engine
from src.services.canonical_mapping_parity import (
    DEFAULT_CONFIDENCE_TOLERANCE,
    compare_legacy_to_canonical_mapping_parity,
    write_mapping_parity_worklist_csv,
)


def get_default_database_url(cli_url: str | None = None) -> str:
    from scripts.operational_db_settings import resolve_operational_database_url_with_settings

    return resolve_operational_database_url_with_settings(cli_url)


def safe_print(*args, sep: str = " ", end: str = "\n", flush: bool = False) -> None:
    message = sep.join(str(arg) for arg in args)
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        print(message, end=end, flush=flush)
    except UnicodeEncodeError:
        safe_message = message.encode(encoding, errors="replace").decode(
            encoding, errors="replace"
        )
        stream.write(safe_message + end)
        if flush:
            stream.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare current legacy standard_mappings with canonical "
            "materialized_pairwise_mappings. This is a cutover/parity gate only; "
            "it does not change /api/v1/mappings."
        )
    )
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--source-standard", default=None)
    parser.add_argument("--target-standard", default=None)
    parser.add_argument(
        "--confidence-tolerance",
        default=str(DEFAULT_CONFIDENCE_TOLERANCE),
        help=f"allowed confidence delta (default: {DEFAULT_CONFIDENCE_TOLERANCE})",
    )
    parser.add_argument("--sample-limit", type=int, default=100)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--worklist-csv",
        type=Path,
        default=None,
        help="write every parity difference as a classification CSV",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument(
        "--allow-drift",
        action="store_true",
        help="return exit code 0 even when parity differences are found",
    )
    args = parser.parse_args()
    args.db_url = get_default_database_url(args.db_url)

    engine = create_engine(
        args.db_url,
        connect_args={"connect_timeout": 10},
        pool_pre_ping=True,
    )
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    try:
        report = compare_legacy_to_canonical_mapping_parity(
            db=db,
            source_standard=args.source_standard,
            target_standard=args.target_standard,
            confidence_tolerance=Decimal(args.confidence_tolerance),
            sample_limit=args.sample_limit,
        )
    finally:
        db.close()

    payload = report.as_dict()
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    if args.worklist_csv is not None:
        write_mapping_parity_worklist_csv(args.worklist_csv, report.worklist())

    if args.json:
        safe_print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        safe_print("Canonical mapping parity gate")
        safe_print(f"  source_standard: {args.source_standard or '*'}")
        safe_print(f"  target_standard: {args.target_standard or '*'}")
        safe_print(f"  legacy_count: {payload['legacy_count']}")
        safe_print(f"  canonical_count: {payload['canonical_count']}")
        safe_print(f"  matched_count: {payload['matched_count']}")
        safe_print(
            "  drift_counts: "
            f"missing={payload['missing_in_canonical_count']} "
            f"extra={payload['extra_in_canonical_count']} "
            f"code={payload['code_mismatch_count']} "
            f"relationship={payload['relationship_mismatch_count']} "
            f"confidence={payload['confidence_mismatch_count']} "
            f"duplicates="
            f"{payload['duplicate_legacy_key_count'] + payload['duplicate_canonical_key_count']}"
        )
        if args.report is not None:
            safe_print(f"  report: {args.report}")
        if args.worklist_csv is not None:
            safe_print(f"  worklist_csv: {args.worklist_csv}")
        safe_print("PASS" if report.passed else "FAIL")

    if report.passed or args.allow_drift:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
