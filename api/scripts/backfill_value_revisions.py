#!/usr/bin/env python3
"""Backfill legacy esg_values rows into revision-backed SDS value tables."""

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

from src.database.init_db import init_db_for_engine  # noqa: E402
from src.services.value_revision_backfill import backfill_esg_values_to_revisions  # noqa: E402


def get_default_database_url(cli_url: str | None = None) -> str:
    from scripts.operational_db_settings import (
        resolve_operational_database_url_with_settings,
    )

    return resolve_operational_database_url_with_settings(cli_url)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill legacy current-state esg_values into append-only value "
            "revision tables. Defaults to dry-run; use --apply to commit."
        )
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant/company identifier")
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="PostgreSQL URL",
    )
    parser.add_argument(
        "--created-by",
        default="backfill_value_revisions.py",
        help="Audit actor stored on created rows",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum legacy rows to inspect",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit rows. Omit for dry-run rollback.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.db_url = get_default_database_url(args.db_url)
    try:
        engine_kwargs = {"pool_pre_ping": True}
        if args.db_url.startswith("postgresql"):
            engine_kwargs["connect_args"] = {"connect_timeout": 10}
        engine = create_engine(args.db_url, **engine_kwargs)
        init_db_for_engine(engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        try:
            report = backfill_esg_values_to_revisions(
                db=db,
                tenant_id=args.tenant_id,
                dry_run=not args.apply,
                created_by=args.created_by,
                limit=args.limit,
            )
        finally:
            db.close()
    except Exception as error:  # pragma: no cover - defensive CLI path
        print(f"FAIL: {error}")
        return 1

    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    if report.failed:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
