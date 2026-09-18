#!/usr/bin/env python3
"""Import a canonical Sygris mapping package into SDS shadow DB tables."""

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
from src.services.canonical_mapping_db_import import (
    import_canonical_mapping_package_to_db,
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
            "Import canonical Sygris mapping package rows into shadow DB tables. "
            "This does not populate legacy standard_mappings or "
            "materialized_pairwise_mappings; run materialization explicitly to "
            "update the public canonical mapping read-model."
        )
    )
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--db-url", default=get_default_database_url())
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and stage writes in a rollback-only transaction",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument(
        "--created-by",
        default="import_canonical_mapping_package.py",
        help="audit user/actor for created shadow rows",
    )
    parser.add_argument(
        "--allow-non-operational-relationships",
        action="store_true",
        help=(
            "for scratch DB/testing only: commit packages containing relationship "
            "types that are validator/inspection-only by default"
        ),
    )
    args = parser.parse_args()

    if not args.package_dir.exists():
        safe_print(f"Package directory not found: {args.package_dir}")
        return 1

    engine = create_engine(
        args.db_url,
        connect_args={"connect_timeout": 10},
        pool_pre_ping=True,
    )
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    try:
        report = import_canonical_mapping_package_to_db(
            package_dir=args.package_dir,
            db=db,
            dry_run=args.dry_run,
            created_by=args.created_by,
            allow_non_operational_relationships=(
                args.allow_non_operational_relationships
            ),
        )
    finally:
        db.close()

    payload = report.as_dict()
    if args.json:
        safe_print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        if report.blocked:
            status = "blocked"
            write_mode = "not committed"
        else:
            status = "valid" if report.valid else "invalid"
            write_mode = "dry-run rollback" if args.dry_run else "committed"
        safe_print(f"Canonical mapping package import: {status} ({write_mode})")
        safe_print(f"Package: {payload['package_dir']}")
        safe_print(f"Counts: {json.dumps(report.counts, sort_keys=True)}")
        if report.blockers:
            safe_print(f"Blockers: {json.dumps(report.blockers, sort_keys=True)}")
        safe_print(
            "Materialized pairwise mappings written: "
            f"{report.materialized_pairwise_mappings_written}"
        )

    return 0 if report.valid and not report.blocked else 2


if __name__ == "__main__":
    raise SystemExit(main())
