#!/usr/bin/env python3
"""Backfill the canonical semantic DB model from the frozen ontology."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.database.init_db import init_db_for_engine

DEFAULT_REPORT = REPO_ROOT / "data" / "extracted" / "analysis" / "wave1_semantic_backfill_summary.json"


def get_default_database_url(cli_url: str | None = None) -> str:
    from scripts.operational_db_settings import (
        resolve_operational_database_url_with_settings,
    )

    return resolve_operational_database_url_with_settings(cli_url)


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_backfill(*, ontology_path: Optional[Path], db_url: str, report_path: Path, dry_run: bool, purge: bool) -> tuple[dict, dict[str, int]]:
    from src.database.bootstrap_semantic_model import (
        build_indicator_lookup,
        match_indicator_id,
        summarize_semantic_records,
        upsert_semantic_records,
    )
    from src.ontology.semantic_backfill import load_semantic_records

    resolved_path, _graph, records = load_semantic_records(ontology_path)

    engine = create_engine(
        db_url,
        connect_args={"connect_timeout": 10},
        pool_pre_ping=True,
    )
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    session = SessionLocal()
    try:
        lookup = build_indicator_lookup(session)
        matched_indicator_ids = {record.curie: match_indicator_id(record, lookup) for record in records.values()}
        report = summarize_semantic_records(records, matched_indicator_ids, resolved_path)
        report["dry_run"] = dry_run
        report["purge_stale"] = purge

        if dry_run:
            write_report(report_path, report)
            return report, {"created": 0, "updated": 0, "refreshed_children": 0, "purged_stale": 0}

        stats = upsert_semantic_records(session, records, matched_indicator_ids, purge_stale=purge)
        session.commit()
        report["write_stats"] = stats
        write_report(report_path, report)
        return report, stats
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill the canonical semantic model from the frozen ontology.")
    parser.add_argument("--ontology", type=Path, default=None, help="Optional ontology file path override")
    parser.add_argument("--db-url", type=str, default=None, help="PostgreSQL URL")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help=f"Summary JSON path (default: {DEFAULT_REPORT})")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and report without mutating the DB")
    parser.add_argument("--purge", action="store_true", help="Delete stale ontology-backed concept rows missing from the current snapshot")
    args = parser.parse_args()
    args.db_url = get_default_database_url(args.db_url)

    report, stats = run_backfill(
        ontology_path=args.ontology,
        db_url=args.db_url,
        report_path=args.report,
        dry_run=args.dry_run,
        purge=args.purge,
    )

    print("Wave 1 semantic backfill")
    print(f"  ontology: {report['ontology_path']}")
    print(f"  ontology_sha256: {report['ontology_sha256']}")
    print(f"  concepts: {report['concept_count']}")
    print(f"  formulas: {report['formula_count']} (derived: {report['derived_formula_count']})")
    print(f"  variables: {report['variable_count']}")
    print(f"  equivalences: {report['equivalence_count']}")
    print(f"  matched indicators: {report['matched_indicator_count']}")
    print(f"  unmatched concepts: {report['unmatched_indicator_count']}")
    print(f"  report: {args.report}")

    if args.dry_run:
        print("PASS")
        return 0

    print(f"  created: {stats['created']}")
    print(f"  updated: {stats['updated']}")
    print(f"  refreshed_children: {stats['refreshed_children']}")
    print(f"  purged_stale: {stats['purged_stale']}")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
