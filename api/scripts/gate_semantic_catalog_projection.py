#!/usr/bin/env python3
"""Gate: active indicators must have deterministic semantic concepts."""

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
    / "semantic_catalog_projection_gate.json"
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


def evaluate_projection_coverage(coverage: dict[str, Any]) -> list[str]:
    """Return gate failures for an already captured projection coverage summary."""
    if coverage.get("ready") is True:
        return []

    active = int(coverage.get("active_indicators") or 0)
    projected = int(coverage.get("projected_active_indicators") or 0)
    missing = list(coverage.get("missing_active_indicator_identifiers") or [])
    stale = list(coverage.get("stale_active_indicator_identifiers") or [])
    active_disclosures = int(coverage.get("active_disclosures") or 0)
    projected_disclosures = int(coverage.get("projected_disclosures") or 0)
    missing_disclosures = list(coverage.get("missing_disclosure_uris") or [])
    stale_disclosures = list(coverage.get("stale_disclosure_uris") or [])
    extra_disclosures = list(coverage.get("extra_disclosure_uris") or [])
    active_source = int(coverage.get("active_source_datapoints") or 0)
    projected_source = int(coverage.get("projected_source_datapoints") or 0)
    missing_source = list(coverage.get("missing_source_datapoint_uris") or [])
    stale_source = list(coverage.get("stale_source_datapoint_uris") or [])
    extra_source = list(coverage.get("extra_source_datapoint_uris") or [])
    unsupported_legacy_disclosures = list(
        coverage.get("unsupported_legacy_disclosure_uris") or []
    )
    unsupported_standards = list(coverage.get("unsupported_active_standard_ids") or [])
    details = [
        "Semantic catalog projection incomplete: "
        f"{projected}/{active} active indicators projected; "
        f"missing examples: "
        f"{', '.join(str(item) for item in missing[:10]) or 'none reported'}"
    ]
    if stale:
        details.append(
            f"Stale projection detected for {len(stale)} indicator(s): "
            f"{', '.join(str(item) for item in stale[:10])}"
        )
    if active_disclosures or missing_disclosures or stale_disclosures:
        details.append(
            "Disclosure catalog projection incomplete: "
            f"{projected_disclosures}/{active_disclosures} expected disclosure "
            "groups projected; missing examples: "
            f"{', '.join(str(item) for item in missing_disclosures[:10]) or 'none reported'}"
        )
    if stale_disclosures:
        details.append(
            f"Stale disclosure projection detected for {len(stale_disclosures)} "
            f"row(s): {', '.join(str(item) for item in stale_disclosures[:10])}"
        )
    if extra_disclosures:
        details.append(
            f"Extra disclosure projection rows detected: "
            f"{', '.join(str(item) for item in extra_disclosures[:10])}"
        )
    if active_source or missing_source or stale_source:
        details.append(
            "Source-standard catalog projection incomplete: "
            f"{projected_source}/{active_source} active unlinked source datapoints "
            "projected; missing examples: "
            f"{', '.join(str(item) for item in missing_source[:10]) or 'none reported'}"
        )
    if stale_source:
        details.append(
            f"Stale source-standard projection detected for {len(stale_source)} "
            f"datapoint(s): {', '.join(str(item) for item in stale_source[:10])}"
        )
    if extra_source:
        details.append(
            f"Extra source-standard projection rows detected: "
            f"{', '.join(str(item) for item in extra_source[:10])}"
        )
    if unsupported_legacy_disclosures:
        details.append(
            "Unsupported legacy disclosure rows detected: "
            f"{', '.join(str(item) for item in unsupported_legacy_disclosures[:10])}"
        )
    if unsupported_standards:
        details.append(
            "Unsupported active source standard(s) detected: "
            f"{', '.join(str(item) for item in unsupported_standards[:10])}"
        )
    return details


def _engine_kwargs(db_url: str) -> dict[str, Any]:
    if db_url.startswith("postgresql"):
        return {"connect_args": {"connect_timeout": 10}, "pool_pre_ping": True}
    return {}


def run_gate(*, db_url: str, report_path: Path) -> tuple[dict[str, Any], list[str]]:
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
        coverage = SemanticConceptProjector(session).coverage_summary()
    finally:
        session.close()
        engine.dispose()

    failures = evaluate_projection_coverage(coverage)
    report = {
        "gate": "semantic_catalog_projection",
        "coverage": coverage,
        "failures": failures,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return report, failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate active-indicator semantic concept projection coverage."
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="PostgreSQL URL (default: from env/settings)",
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    db_url = _get_default_database_url(args.db_url)
    report, failures = run_gate(db_url=db_url, report_path=args.report)
    coverage = report["coverage"]

    print("Semantic catalog projection gate")
    print(f"  active_indicators: {coverage['active_indicators']}")
    print(
        "  projected_active_indicators: " f"{coverage['projected_active_indicators']}"
    )
    print(f"  active_source_datapoints: {coverage.get('active_source_datapoints', 0)}")
    print(
        "  projected_source_datapoints: "
        f"{coverage.get('projected_source_datapoints', 0)}"
    )
    print(f"  active_disclosures: {coverage.get('active_disclosures', 0)}")
    print(f"  projected_disclosures: {coverage.get('projected_disclosures', 0)}")
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
