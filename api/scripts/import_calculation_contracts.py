#!/usr/bin/env python3
"""Import Atomizer sds_calculation_contract.json into SDS."""

from __future__ import annotations

import argparse
import csv
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

from src.config.settings import settings
from src.database.init_db import init_db_for_engine
from src.services.calculation_contract_import import (
    CalculationContractImportError,
    import_calculation_contract_file,
    load_calculation_contract_payload,
    validate_calculation_contract_payload,
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


def load_known_register_identifiers(path: Path | None) -> set[str]:
    if path is None:
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "identifier" not in reader.fieldnames:
            return set()
        return {
            (row.get("identifier") or "").strip()
            for row in reader
            if (row.get("identifier") or "").strip()
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import Atomizer calculation semantics into SDS."
    )
    parser.add_argument(
        "--json",
        dest="contract_json",
        type=Path,
        required=True,
        help="Path to sds_calculation_contract.json",
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
        default="import_calculation_contracts.py",
        help="Audit value for created_by",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the contract without writing rows",
    )
    parser.add_argument(
        "--known-register-csv",
        type=Path,
        default=None,
        help=(
            "Register CSV whose identifiers should count as known during dry-run "
            "validation of a package import."
        ),
    )
    parser.add_argument(
        "--retirement-scope",
        choices=("incoming_keys", "incoming_models"),
        default="incoming_keys",
        help=(
            "Contract retirement boundary for real imports. incoming_keys preserves "
            "legacy behavior; incoming_models additionally retires active contracts "
            "from incoming model_id values that are absent from the replacement."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.dry_run and args.known_register_csv is not None:
            payload, _contract_sha256 = load_calculation_contract_payload(
                args.contract_json
            )
            validation = validate_calculation_contract_payload(
                payload,
                known_indicator_identifiers=load_known_register_identifiers(
                    args.known_register_csv
                ),
            )
            print(
                f"Validated {validation.node_count} calculation nodes "
                f"from {args.contract_json}"
            )
            print("PASS")
            return 0

        engine_kwargs = {"pool_pre_ping": True}
        if args.db_url.startswith("postgresql"):
            engine_kwargs["connect_args"] = {"connect_timeout": 10}
        engine = create_engine(args.db_url, **engine_kwargs)
        if not args.dry_run:
            init_db_for_engine(engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        try:
            report = import_calculation_contract_file(
                contract_path=args.contract_json,
                db=db,
                dry_run=args.dry_run,
                created_by=args.created_by,
                known_indicator_identifiers=load_known_register_identifiers(
                    args.known_register_csv
                ),
                retirement_scope=args.retirement_scope,
            )
        finally:
            db.close()
    except (FileNotFoundError, CalculationContractImportError) as error:
        print(f"FAIL: {error}")
        return 1
    except Exception as error:  # pragma: no cover - defensive CLI path
        print(f"FAIL: {error}")
        return 1

    if report.dry_run:
        print(
            f"Validated {report.counts.get('contract_nodes', 0)} calculation nodes "
            f"from {args.contract_json}"
        )
    elif report.status == "already_imported":
        print(f"Already imported package {report.package_hash}")
    else:
        print(
            f"Imported {report.counts.get('contracts_created', 0)} calculation "
            f"contracts from {args.contract_json}"
        )
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
