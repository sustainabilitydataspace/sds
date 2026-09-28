#!/usr/bin/env python3
"""Run the canonical Sygris mapping shadow workflow end to end."""

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
    write_mapping_parity_worklist_csv,
)
from src.services.canonical_mapping_shadow_workflow import (
    run_canonical_mapping_shadow_workflow,
)
from src.services.canonical_pairwise_materialization import (
    DEFAULT_APPROVAL_STATUSES,
    DEFAULT_MAPPING_PROFILE,
)


def get_default_database_url(cli_url: str | None = None) -> str:
    from scripts.operational_db_settings import (
        resolve_operational_database_url_with_settings,
    )

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


def _engine_options(db_url: str) -> dict:
    options = {"pool_pre_ping": True}
    if db_url.startswith(("postgresql://", "postgresql+")):
        options["connect_args"] = {"connect_timeout": 10}
    return options


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate/import a canonical Sygris mapping package, materialize the "
            "pairwise canonical read-model, and compare it with legacy "
            "standard_mappings. Committed materialization updates the canonical "
            "read-model served by /api/v1/mappings."
        )
    )
    parser.add_argument("--package-dir", type=Path, default=None)
    parser.add_argument("--db-url", default=None)
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="reuse already imported canonical shadow tables",
    )
    parser.add_argument(
        "--skip-materialize",
        action="store_true",
        help="reuse existing materialized_pairwise_mappings rows",
    )
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
        help="write parity differences as a classification CSV",
    )
    parser.add_argument(
        "--mapping-profile",
        default=DEFAULT_MAPPING_PROFILE,
        help=f"mapping profile to materialize (default: {DEFAULT_MAPPING_PROFILE})",
    )
    parser.add_argument(
        "--approval-status",
        action="append",
        dest="approval_statuses",
        help=(
            "approval status to include; repeat for multiple values "
            f"(default: {', '.join(DEFAULT_APPROVAL_STATUSES)})"
        ),
    )
    parser.add_argument(
        "--allow-operational-subset",
        action="store_true",
        help=(
            "Materialize only operational relationship types when non-operational "
            "assertion groups are present. Without this explicit flag, the "
            "workflow blocks before committing the read-model."
        ),
    )
    parser.add_argument(
        "--created-by",
        default="run_canonical_mapping_shadow_workflow.py",
        help="audit user/actor for created shadow rows",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument(
        "--allow-drift",
        action="store_true",
        help="return exit code 0 even when parity differences are found",
    )
    args = parser.parse_args()
    args.db_url = get_default_database_url(args.db_url)

    if not args.skip_import:
        if args.package_dir is None:
            safe_print("Use --package-dir unless --skip-import is set.")
            return 2
        if not args.package_dir.exists():
            safe_print(f"Package directory not found: {args.package_dir}")
            return 2

    engine = create_engine(args.db_url, **_engine_options(args.db_url))
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    try:
        report = run_canonical_mapping_shadow_workflow(
            db=db,
            package_dir=args.package_dir,
            skip_import=args.skip_import,
            skip_materialize=args.skip_materialize,
            created_by=args.created_by,
            mapping_profile=args.mapping_profile,
            approval_statuses=tuple(
                args.approval_statuses or DEFAULT_APPROVAL_STATUSES
            ),
            allow_operational_subset=args.allow_operational_subset,
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
    if args.worklist_csv is not None and report.parity is not None:
        write_mapping_parity_worklist_csv(args.worklist_csv, report.parity.worklist())

    if args.json:
        safe_print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        _print_summary(args, payload)

    if report.status in {
        "invalid_package",
        "blocked_non_operational_package",
        "blocked_non_operational_materialization",
    }:
        return 2
    if report.passed or args.allow_drift:
        return 0
    return 1


def _print_summary(args, payload: dict) -> None:
    safe_print("Canonical mapping shadow workflow")
    safe_print(f"  status: {payload['status']}")
    safe_print(f"  source_standard: {args.source_standard or '*'}")
    safe_print(f"  target_standard: {args.target_standard or '*'}")
    if payload["import"] is not None:
        safe_print(f"  import_valid: {payload['import']['valid']}")
        safe_print(f"  import_blocked: {payload['import']['blocked']}")
        safe_print(f"  import_committed: {payload['import']['committed']}")
        if payload["import"].get("blockers"):
            safe_print(
                "  import_blockers: "
                f"{json.dumps(payload['import']['blockers'], sort_keys=True)}"
            )
    else:
        safe_print("  import: skipped")
    if payload["materialization"] is not None:
        safe_print(
            f"  materialization_blocked: {payload['materialization']['blocked']}"
        )
        if payload["materialization"].get("blockers"):
            safe_print(
                "  materialization_blockers: "
                f"{json.dumps(payload['materialization']['blockers'], sort_keys=True)}"
            )
        safe_print(
            "  materialized_candidates: "
            f"{payload['materialization']['candidate_count']}"
        )
        safe_print(
            "  materialization_hash: "
            f"{payload['materialization']['materialization_hash']}"
        )
    else:
        safe_print("  materialization: skipped")
    if payload["parity"] is not None:
        parity = payload["parity"]
        safe_print(f"  legacy_count: {parity['legacy_count']}")
        safe_print(f"  canonical_count: {parity['canonical_count']}")
        safe_print(f"  matched_count: {parity['matched_count']}")
        safe_print(
            f"  normalized_code_match_count: "
            f"{parity['normalized_code_match_count']}"
        )
        safe_print(f"  worklist_count: {parity['worklist_count']}")
    if args.report is not None:
        safe_print(f"  report: {args.report}")
    if args.worklist_csv is not None:
        safe_print(f"  worklist_csv: {args.worklist_csv}")
    safe_print("PASS" if payload["passed"] else "FAIL")


if __name__ == "__main__":
    raise SystemExit(main())
