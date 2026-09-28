#!/usr/bin/env python3
"""Wave 1 parity gate: frozen ontology versus canonical semantic DB model."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, Tuple

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.calculation.db_semantic_snapshot import (  # noqa: E402
    build_seeded_variable_values,
    evaluate_db_semantic_formula,
    load_db_semantic_snapshots,
)
from src.calculation.engine import CalculationContext, CalculationEngine, TemporalGranularity  # noqa: E402
from src.calculation.semantic_formula import canonical_formula_for_compare  # noqa: E402
from src.database.init_db import init_db_for_engine  # noqa: E402
from src.ontology.semantic_backfill import load_semantic_records, ontology_sha256  # noqa: E402

DEFAULT_BACKFILL_REPORT = REPO_ROOT / "data" / "extracted" / "analysis" / "wave1_semantic_backfill_summary.json"
DEFAULT_PARITY_REPORT = REPO_ROOT / "data" / "extracted" / "analysis" / "wave1_parity_report.json"


def get_default_database_url(cli_url: str | None = None) -> str:
    from scripts.operational_db_url import resolve_operational_database_url

    if cli_url is not None:
        return resolve_operational_database_url(cli_url)

    from src.config.settings import settings

    configured = settings.database_url.get_secret_value() if settings.database_url else None
    return resolve_operational_database_url(cli_url, configured_url=configured)


def _dummy_context() -> CalculationContext:
    return CalculationContext(
        entity_id="wave1_parity_entity",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        temporal_granularity=TemporalGranularity.ANNUAL,
        organizational_level=0,
        metadata={"gate": "wave1"},
    )


def compare_parity(
    ontology_records,
    db_snapshots,
    ontology_graph,
    *,
    ontology_path: Path,
    expected_hash: str | None = None,
) -> tuple[dict, list[str]]:
    engine = CalculationEngine()
    context = _dummy_context()
    failures: list[str] = []
    concept_reports: dict[str, dict] = {}

    ontology_hash = ontology_sha256(ontology_path)
    if expected_hash and expected_hash != ontology_hash:
        failures.append(
            f"Backfill report ontology hash mismatch: expected {expected_hash}, current {ontology_hash}"
        )

    ontology_counts = Counter(record.concept_type for record in ontology_records.values())
    db_counts = Counter(snapshot.concept_type for snapshot in db_snapshots.values())
    if ontology_counts != db_counts:
        failures.append(f"Concept type counts differ: ontology={dict(ontology_counts)} db={dict(db_counts)}")

    extra_db_uris = sorted(set(db_snapshots) - {record.uri for record in ontology_records.values()})
    if extra_db_uris:
        failures.append(f"DB contains {len(extra_db_uris)} extra semantic concepts not present in frozen ontology")

    for record in ontology_records.values():
        snapshot = db_snapshots.get(record.uri)
        report_entry = {
            "uri": record.uri,
            "curie": record.curie,
            "concept_type_match": False,
            "state_match": False,
            "label_match": False,
            "unit_match": False,
            "temporal_match": False,
            "dependency_set_match": False,
            "dependency_order_match": False,
            "formula_match": False,
            "equivalence_match": False,
            "evaluation_match": None,
        }

        if snapshot is None:
            failures.append(f"Missing DB concept for {record.curie}")
            concept_reports[record.uri] = report_entry
            continue

        report_entry["concept_type_match"] = snapshot.concept_type == record.concept_type
        report_entry["state_match"] = snapshot.concept_state == record.concept_state
        report_entry["label_match"] = snapshot.label == record.label
        report_entry["temporal_match"] = snapshot.temporal_granularity == record.temporal_granularity

        db_dependency_uris = [variable.uri for variable in snapshot.variables]
        ontology_dependency_uris = [variable.uri for variable in record.variables]
        report_entry["dependency_set_match"] = set(db_dependency_uris) == set(ontology_dependency_uris)
        report_entry["dependency_order_match"] = db_dependency_uris == ontology_dependency_uris

        ontology_formula = canonical_formula_for_compare(record.formula.expression if record.formula else None)
        db_formula = snapshot.comparable_formula
        report_entry["formula_match"] = ontology_formula == db_formula

        ontology_equivalences = sorted((equivalence.relationship_type, equivalence.target_uri) for equivalence in record.equivalences)
        db_equivalences = sorted((equivalence.relationship_type, equivalence.target_uri) for equivalence in snapshot.equivalences)
        report_entry["equivalence_match"] = ontology_equivalences == db_equivalences

        try:
            ontology_unit = engine._get_indicator_unit(record.curie, ontology_graph)
        except Exception:
            ontology_unit = None
        report_entry["unit_match"] = snapshot.display_unit == ontology_unit

        if record.concept_state == "calculable":
            ontology_dependencies = engine.resolve_dependencies(record.curie, context, ontology_graph)
            ontology_formula_runtime = engine._get_indicator_formula(record.curie, ontology_graph, ontology_dependencies)
            variable_values = build_seeded_variable_values(dep.variable_uri for dep in ontology_dependencies)
            ontology_value = engine._evaluate_formula(ontology_formula_runtime, variable_values)
            if not isinstance(ontology_value, Decimal):
                ontology_value = Decimal(str(ontology_value))

            db_value, db_unit, db_formula_runtime = evaluate_db_semantic_formula(snapshot, variable_values)
            report_entry["evaluation_match"] = (
                ontology_value == db_value
                and ontology_unit == db_unit
                and canonical_formula_for_compare(ontology_formula_runtime) == canonical_formula_for_compare(db_formula_runtime)
                and [dep.variable_uri for dep in ontology_dependencies] == db_dependency_uris
            )

        checks = [
            ("concept_type_match", report_entry["concept_type_match"]),
            ("state_match", report_entry["state_match"]),
            ("label_match", report_entry["label_match"]),
            ("unit_match", report_entry["unit_match"]),
            ("temporal_match", report_entry["temporal_match"]),
            ("dependency_set_match", report_entry["dependency_set_match"]),
            ("dependency_order_match", report_entry["dependency_order_match"]),
            ("formula_match", report_entry["formula_match"]),
            ("equivalence_match", report_entry["equivalence_match"]),
        ]
        if record.concept_state == "calculable":
            checks.append(("evaluation_match", bool(report_entry["evaluation_match"])))

        for name, passed in checks:
            if not passed:
                failures.append(f"{record.curie}: {name} failed")

        concept_reports[record.uri] = report_entry

    report = {
        "ontology_hash": ontology_hash,
        "concept_count": len(ontology_records),
        "ontology_counts": dict(ontology_counts),
        "db_counts": dict(db_counts),
        "extra_db_uris": extra_db_uris,
        "concepts": concept_reports,
    }
    return report, failures


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_gate(*, ontology_path: Path | None, db_url: str, backfill_report_path: Path, parity_report_path: Path) -> tuple[dict, list[str]]:
    resolved_path, ontology_graph, ontology_records = load_semantic_records(ontology_path)

    expected_hash = None
    if backfill_report_path.exists():
        try:
            expected_hash = json.loads(backfill_report_path.read_text(encoding="utf-8")).get("ontology_sha256")
        except Exception:
            expected_hash = None

    if expected_hash is None:
        failures = [f"Backfill report missing or invalid: {backfill_report_path}"]
        report = {
            "ontology_hash": ontology_sha256(resolved_path),
            "concept_count": len(ontology_records),
            "concepts": {},
            "extra_db_uris": [],
        }
        write_report(parity_report_path, report)
        return report, failures

    engine = create_engine(
        db_url,
        connect_args={"connect_timeout": 10},
        pool_pre_ping=True,
    )
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    session = SessionLocal()
    try:
        db_snapshots = load_db_semantic_snapshots(session)
        report, failures = compare_parity(
            ontology_records,
            db_snapshots,
            ontology_graph,
            ontology_path=resolved_path,
            expected_hash=expected_hash,
        )
        write_report(parity_report_path, report)
        return report, failures
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Wave 1 semantic parity between ontology and DB.")
    parser.add_argument("--ontology", type=Path, default=None, help="Optional ontology file path override")
    parser.add_argument("--db-url", type=str, default=None, help="PostgreSQL URL")
    parser.add_argument("--backfill-report", type=Path, default=DEFAULT_BACKFILL_REPORT, help="Backfill summary JSON path")
    parser.add_argument("--report", type=Path, default=DEFAULT_PARITY_REPORT, help="Parity report JSON path")
    args = parser.parse_args()
    args.db_url = get_default_database_url(args.db_url)

    report, failures = run_gate(
        ontology_path=args.ontology,
        db_url=args.db_url,
        backfill_report_path=args.backfill_report,
        parity_report_path=args.report,
    )

    print("Wave 1 parity gate")
    print(f"  ontology_hash: {report['ontology_hash']}")
    print(f"  concept_count: {report['concept_count']}")
    print(f"  report: {args.report}")
    if report.get("extra_db_uris"):
        print(f"  extra_db_uris: {len(report['extra_db_uris'])}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
