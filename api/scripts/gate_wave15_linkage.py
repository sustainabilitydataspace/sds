#!/usr/bin/env python3
"""Wave 1.5 gate for deterministic concept-indicator linkage coverage."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.database.init_db import init_db_for_engine  # noqa: E402
from src.database.models import Concept, ConceptIndicatorLink  # noqa: E402

DEFAULT_REPORT = REPO_ROOT / "data" / "extracted" / "analysis" / "wave15_linkage_summary.json"


def get_default_database_url(cli_url: str | None = None) -> str:
    from scripts.operational_db_settings import (
        resolve_operational_database_url_with_settings,
    )

    return resolve_operational_database_url_with_settings(cli_url)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Wave 1.5 concept-indicator linkage coverage.")
    parser.add_argument("--db-url", type=str, default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    args.db_url = get_default_database_url(args.db_url)

    failures: list[str] = []
    if not args.report.exists():
        failures.append(f"Missing linkage summary report: {args.report}")
        report = {}
    else:
        report = json.loads(args.report.read_text(encoding="utf-8"))

    engine = create_engine(args.db_url, connect_args={"connect_timeout": 10}, pool_pre_ping=True)
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        if "concept_indicator_links" not in tables:
            failures.append("concept_indicator_links table is missing")

    session = SessionLocal()
    try:
        disclosure_count = session.query(Concept).filter(Concept.concept_type == "Disclosure").count()
        linked_disclosures = (
            session.query(Concept.id)
            .join(ConceptIndicatorLink, ConceptIndicatorLink.concept_id == Concept.id)
            .filter(Concept.concept_type == "Disclosure")
            .distinct()
            .count()
        )
        exact_or_title_links = (
            session.query(ConceptIndicatorLink)
            .filter(ConceptIndicatorLink.link_type.in_(["exact_code_same_taxonomy", "exact_unique_title_same_taxonomy"]))
            .count()
        )

        if disclosure_count == 0:
            failures.append("No disclosure concepts found in canonical semantic model")
        if linked_disclosures < disclosure_count:
            failures.append(
                f"Only {linked_disclosures}/{disclosure_count} disclosure concepts are linked to indicators"
            )
        if exact_or_title_links == 0:
            failures.append("No high-confidence exact/title concept-indicator links were created")

        if report:
            if report.get("linked_disclosure_count") != linked_disclosures:
                failures.append("Linkage summary report is out of sync with DB linked disclosure count")
            if report.get("disclosure_count") != disclosure_count:
                failures.append("Linkage summary report disclosure count is out of sync with DB")
    finally:
        session.close()

    print("Wave 1.5 linkage gate")
    print(f"  report: {args.report}")
    print(f"  disclosure_count: {disclosure_count if 'disclosure_count' in locals() else 'n/a'}")
    print(f"  linked_disclosures: {linked_disclosures if 'linked_disclosures' in locals() else 'n/a'}")
    print(f"  exact_or_title_links: {exact_or_title_links if 'exact_or_title_links' in locals() else 'n/a'}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
