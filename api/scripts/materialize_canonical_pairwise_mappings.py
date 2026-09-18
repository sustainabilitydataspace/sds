#!/usr/bin/env python3
"""Materialize pairwise mappings from canonical Sygris assertions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.database.init_db import init_db_for_engine
from src.services.canonical_pairwise_materialization import (
    DEFAULT_APPROVAL_STATUSES,
    DEFAULT_MAPPING_PROFILE,
    materialize_pairwise_mappings,
)


def get_default_database_url() -> str:
    env_database_url = os.getenv("DATABASE_URL")
    if env_database_url:
        return env_database_url

    postgres_user = os.getenv("POSTGRES_USER", "sds")
    postgres_password = os.getenv("POSTGRES_PASSWORD", "password")
    postgres_host = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port = os.getenv("POSTGRES_PORT", "5432")
    postgres_db = os.getenv("POSTGRES_DB", "sds")
    return (
        f"postgresql://{postgres_user}:{postgres_password}@"
        f"{postgres_host}:{postgres_port}/{postgres_db}"
    )


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
            "Generate shadow materialized_pairwise_mappings rows from canonical "
            "Sygris assertion groups. Committed runs update the canonical read-model "
            "served by /api/v1/mappings."
        )
    )
    parser.add_argument("--db-url", default=get_default_database_url())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
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
    args = parser.parse_args()

    engine = create_engine(
        args.db_url,
        connect_args={"connect_timeout": 10},
        pool_pre_ping=True,
    )
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    try:
        report = materialize_pairwise_mappings(
            db=db,
            dry_run=args.dry_run,
            mapping_profile=args.mapping_profile,
            approval_statuses=tuple(
                args.approval_statuses or DEFAULT_APPROVAL_STATUSES
            ),
        )
    finally:
        db.close()

    payload = report.as_dict()
    if args.json:
        safe_print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        if report.blocked:
            write_mode = "blocked"
        else:
            write_mode = "dry-run rollback" if args.dry_run else "committed"
        safe_print(f"Canonical pairwise materialization: {write_mode}")
        safe_print(f"Candidates: {report.candidate_count}")
        safe_print(f"Materialization hash: {report.materialization_hash}")
        if report.blockers:
            safe_print(f"Blockers: {json.dumps(report.blockers, sort_keys=True)}")
        safe_print(f"Counts: {json.dumps(report.counts, sort_keys=True)}")

    return 0 if not report.blocked else 2


if __name__ == "__main__":
    raise SystemExit(main())
