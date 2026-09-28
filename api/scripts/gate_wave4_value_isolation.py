#!/usr/bin/env python3
"""Wave 4 gate: value imports must not mutate semantic/catalog/reference state."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from src.database.init_db import init_db_for_engine
from src.database.models import ESGValue
from src.database.semantic_surface import capture_semantic_surface
from src.services.value_csv_import import ValueCsvContractError, load_values_from_csv
from import_values_csv import import_values_csv
from value_import_gate_hierarchy import (
    VALUE_IMPORT_GATE_ENTITY,
    ensure_value_import_gate_hierarchy,
)

DEFAULT_REPORT = REPO_ROOT / "data" / "extracted" / "analysis" / "wave4_value_isolation_report.json"
GATE_MARKER = "gate_wave4_value_isolation"


def get_default_database_url(cli_url: str | None = None) -> str:
    from scripts.operational_db_url import resolve_operational_database_url

    if cli_url is not None:
        return resolve_operational_database_url(cli_url)

    from src.config.settings import settings

    configured = settings.database_url.get_secret_value() if settings.database_url else None
    return resolve_operational_database_url(cli_url, configured_url=configured)


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_gate_csv(path: Path) -> None:
    path.write_text(
        "concept,entity,period,value,unit\n"
        f"syg:Water_Cooling,{VALUE_IMPORT_GATE_ENTITY},2024-01-15,1.5,m³\n"
        f"syg:Water_Industrial,{VALUE_IMPORT_GATE_ENTITY},2024-01-15,500,L\n",
        encoding="utf-8",
    )


def run_gate(*, db_url: str, report_path: Path) -> tuple[dict, list[str]]:
    engine_kwargs = {"pool_pre_ping": True}
    if db_url.startswith("postgresql"):
        engine_kwargs["connect_args"] = {"connect_timeout": 10}
    engine = create_engine(db_url, **engine_kwargs)
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    failures: list[str] = []
    report: dict = {
        "gate": "wave4_value_isolation",
        "csv_contract_rejects_semantic_headers": False,
        "imported_values": 0,
    }

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        values_csv = temp_path / "wave4_values.csv"
        build_gate_csv(values_csv)

        semantic_like_csv = temp_path / "semantic_like.csv"
        semantic_like_csv.write_text(
            "identifier,title,dimension,unitName\n"
            "urn:sds:reg:test:1,Example disclosure,E,m³\n",
            encoding="utf-8",
        )

        try:
            load_values_from_csv(semantic_like_csv)
        except ValueCsvContractError:
            report["csv_contract_rejects_semantic_headers"] = True
        else:
            failures.append("Operational values CSV parser accepted semantic/catalog-style headers.")

        db = SessionLocal()
        try:
            ensure_value_import_gate_hierarchy(db, created_by=GATE_MARKER)
            before = capture_semantic_surface(db)
            imported = import_values_csv(
                csv_path=values_csv,
                db_url=db_url,
                default_entity=VALUE_IMPORT_GATE_ENTITY,
                created_by=GATE_MARKER,
            )
            db.expire_all()
            after = capture_semantic_surface(db)

            db.query(ESGValue).filter(ESGValue.created_by == GATE_MARKER).delete()
            db.commit()

            report["before"] = before
            report["after"] = after
            report["imported_values"] = imported

            if before["overall_digest"] != after["overall_digest"]:
                failures.append("Semantic/catalog/reference surface changed after value CSV import.")

            for table_name, before_summary in before["tables"].items():
                after_summary = after["tables"][table_name]
                if before_summary["digest"] != after_summary["digest"]:
                    failures.append(f"Protected table changed during value import: {table_name}")
        finally:
            db.close()

    write_report(report_path, report | {"failures": failures})
    return report, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Wave 4 value import isolation.")
    parser.add_argument("--db-url", type=str, default=None, help="PostgreSQL URL")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help=f"Summary JSON path (default: {DEFAULT_REPORT})")
    args = parser.parse_args()
    args.db_url = get_default_database_url(args.db_url)

    report, failures = run_gate(db_url=args.db_url, report_path=args.report)

    print("Wave 4 value isolation gate")
    print(f"  csv_contract_rejects_semantic_headers: {report['csv_contract_rejects_semantic_headers']}")
    print(f"  imported_values: {report['imported_values']}")
    print(f"  overall_digest_before: {report['before']['overall_digest']}")
    print(f"  overall_digest_after:  {report['after']['overall_digest']}")
    print(f"  report: {args.report}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
