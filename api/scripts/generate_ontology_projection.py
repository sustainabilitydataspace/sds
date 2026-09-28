#!/usr/bin/env python3
"""Generate Wave 2 split ontology artifacts from the canonical semantic DB model."""

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
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.database.init_db import init_db_for_engine  # noqa: E402

DEFAULT_BASE = API_ROOT / "ontologies" / "base.owl"
DEFAULT_CORE = API_ROOT / "ontologies" / "core_tbox.owl"
DEFAULT_PROJECTION = API_ROOT / "ontologies" / "generated_projection.owl"
DEFAULT_REPORT = (
    REPO_ROOT / "data" / "extracted" / "analysis" / "wave2_projection_summary.json"
)


def get_default_database_url(cli_url: str | None = None) -> str:
    from scripts.operational_db_settings import (
        resolve_operational_database_url_with_settings,
    )

    return resolve_operational_database_url_with_settings(cli_url)


def run_generation(
    *,
    db_url: str,
    base_path: Path,
    core_path: Path,
    projection_path: Path,
    report_path: Path,
) -> dict:
    from src.calculation.db_semantic_snapshot import load_db_semantic_snapshots
    from src.ontology.projection_generator import (
        build_core_tbox_graph,
        build_generated_projection_graph,
        load_base_graph,
        write_graph,
    )

    engine = create_engine(
        db_url, connect_args={"connect_timeout": 10}, pool_pre_ping=True
    )
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    base_graph = load_base_graph(base_path)
    core_graph = build_core_tbox_graph(base_graph)

    session = SessionLocal()
    try:
        # The generated projection is a PUBLIC RDF artifact: load only public-catalog
        # concepts so internal/future concepts + their formulas never leak (F09 M1).
        snapshots = load_db_semantic_snapshots(session, public_catalog=True)
    finally:
        session.close()

    projection_graph = build_generated_projection_graph(snapshots)
    write_graph(core_graph, core_path)
    write_graph(projection_graph, projection_path)

    report = {
        "base_path": str(base_path),
        "core_path": str(core_path),
        "projection_path": str(projection_path),
        "core_triples": len(core_graph),
        "projection_triples": len(projection_graph),
        "projected_concepts": len(snapshots),
        "projected_disclosures": sum(
            1
            for snapshot in snapshots.values()
            if snapshot.concept_type == "Disclosure"
        ),
        "projected_variables": sum(
            1 for snapshot in snapshots.values() if snapshot.concept_type == "Variable"
        ),
        "projected_formulas": sum(
            1 for snapshot in snapshots.values() if snapshot.formula_expression
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate core_tbox.owl and generated_projection.owl from DB semantics."
    )
    parser.add_argument("--db-url", type=str, default=None)
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--core", type=Path, default=DEFAULT_CORE)
    parser.add_argument("--projection", type=Path, default=DEFAULT_PROJECTION)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    args.db_url = get_default_database_url(args.db_url)

    report = run_generation(
        db_url=args.db_url,
        base_path=args.base,
        core_path=args.core,
        projection_path=args.projection,
        report_path=args.report,
    )

    print("Wave 2 ontology projection")
    print(f"  core_triples: {report['core_triples']}")
    print(f"  projection_triples: {report['projection_triples']}")
    print(f"  projected_concepts: {report['projected_concepts']}")
    print(f"  report: {args.report}")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
