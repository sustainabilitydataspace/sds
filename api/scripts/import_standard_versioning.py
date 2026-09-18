#!/usr/bin/env python3
"""Import Atomizer standard-versioning metadata into SDS shadow tables."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.config.settings import settings  # noqa: E402
from src.database.init_db import init_db_for_engine  # noqa: E402
from src.services.standard_versioning_import import (  # noqa: E402
    StandardVersioningImportError,
    import_standard_versioning_package,
    validate_standard_versioning_package,
)


def get_default_database_url() -> str:
    if settings.database_url:
        return settings.database_url.get_secret_value()

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import Atomizer standard-versioning metadata into SDS."
    )
    parser.add_argument(
        "--package-dir",
        type=Path,
        required=True,
        help="Atomizer SDS package directory containing manifest.json",
    )
    parser.add_argument(
        "--register-csv",
        type=Path,
        required=True,
        help="SDS register CSV used to enrich standard datapoint labels.",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=get_default_database_url(),
        help="PostgreSQL URL",
    )
    parser.add_argument(
        "--created-by",
        type=str,
        default="import_standard_versioning.py",
        help="Audit value for created_by",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and plan standard-versioning import without writing rows.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.dry_run:
            report = validate_standard_versioning_package(
                package_dir=args.package_dir,
                register_csv=args.register_csv,
            )
        else:
            engine_kwargs = {"pool_pre_ping": True}
            if args.db_url.startswith("postgresql"):
                engine_kwargs["connect_args"] = {"connect_timeout": 10}
            engine = create_engine(args.db_url, **engine_kwargs)
            init_db_for_engine(engine)
            SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
            db = SessionLocal()
            try:
                report = import_standard_versioning_package(
                    package_dir=args.package_dir,
                    register_csv=args.register_csv,
                    db=db,
                    dry_run=False,
                    created_by=args.created_by,
                )
            finally:
                db.close()
    except (FileNotFoundError, StandardVersioningImportError, ValueError) as error:
        print(f"FAIL: {error}")
        return 1
    except Exception as error:  # pragma: no cover - defensive CLI path
        print(f"FAIL: {error}")
        return 1

    if not report.valid:
        print("FAIL: " + "; ".join(report.errors))
        return 1
    verb = "Validated" if report.dry_run else "Imported"
    print(
        f"{verb} standard versioning {report.standard_id} {report.release_id}: "
        f"{report.counts}"
    )
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
