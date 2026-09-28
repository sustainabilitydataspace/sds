#!/usr/bin/env python3
"""Project active indicator catalog rows into DB-backed semantic concepts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

DEFAULT_REPORT = (
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "semantic_catalog_projection_apply.json"
)


def _get_default_database_url(cli_url: str | None = None) -> str:
    # Lazy import so --help works without app env/secrets.
    from scripts.operational_db_url import resolve_operational_database_url

    if cli_url is not None:
        return resolve_operational_database_url(cli_url)
    from src.config.settings import settings  # noqa: E402

    configured = (
        settings.database_url.get_secret_value() if settings.database_url else None
    )
    return resolve_operational_database_url(cli_url, configured_url=configured)


def _engine_kwargs(db_url: str) -> dict[str, Any]:
    if db_url.startswith("postgresql"):
        return {"connect_args": {"connect_timeout": 10}, "pool_pre_ping": True}
    return {}


def run_projection(*, db_url: str, report_path: Path, dry_run: bool) -> dict[str, Any]:
    from sqlalchemy import create_engine  # noqa: E402
    from sqlalchemy.orm import sessionmaker  # noqa: E402

    from src.database.init_db import init_db_for_engine  # noqa: E402
    from src.services.semantic_concept_projector import (  # noqa: E402
        SemanticConceptProjector,
    )

    engine = create_engine(db_url, **_engine_kwargs(db_url))
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        result = SemanticConceptProjector(session).project(commit=not dry_run)
        payload = result.as_dict()
        # Capture would-be coverage before any rollback so dry-run reports are
        # consistent with the result block.
        coverage = SemanticConceptProjector(session).coverage_summary()
        if dry_run:
            session.rollback()
    finally:
        session.close()
        engine.dispose()

    report = {
        "operation": "semantic_catalog_projection",
        "dry_run": dry_run,
        "result": payload,
        "coverage": coverage,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize active indicators into semantic concepts."
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="PostgreSQL URL (default: from env/settings)",
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_url = _get_default_database_url(args.db_url)
    report = run_projection(
        db_url=db_url, report_path=args.report, dry_run=args.dry_run
    )
    result = report["result"]
    coverage = report["coverage"]
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(f"Semantic catalog projection ({mode})")
    print(f"  active_indicators: {result['active_indicators']}")
    print(f"  canonical_created: {result['canonical_created']}")
    print(f"  canonical_revised: {result['canonical_revised']}")
    print(f"  concepts_created: {result['concepts_created']}")
    print(f"  concepts_updated: {result['concepts_updated']}")
    print(
        "  coverage: "
        f"{coverage['projected_active_indicators']}/"
        f"{coverage['active_indicators']}"
    )
    print(f"  report: {args.report}")
    if not args.dry_run and not coverage["ready"]:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
