#!/usr/bin/env python3
"""Wave 2 gate for generated ontology projection coverage."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import RDF
from sqlalchemy import create_engine
from sqlalchemy.orm import joinedload, sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.calculation.db_semantic_snapshot import (  # noqa: E402
    load_db_semantic_snapshots,
)
from src.database.init_db import init_db_for_engine  # noqa: E402
from src.database.models import Concept  # noqa: E402
from src.ontology.curie import DEFAULT_NAMESPACES  # noqa: E402
from src.ontology.local_graph import load_ontology_graph  # noqa: E402
from src.ontology.projection_generator import (  # noqa: E402
    build_generated_projection_graph,
)

DEFAULT_CORE = API_ROOT / "ontologies" / "core_tbox.owl"
DEFAULT_PROJECTION = API_ROOT / "ontologies" / "generated_projection.owl"
DEFAULT_REPORT = (
    REPO_ROOT / "data" / "extracted" / "analysis" / "wave2_projection_summary.json"
)

SDS_DISCLOSURE = URIRef(DEFAULT_NAMESPACES.expand("sds:Disclosure"))
SDS_VARIABLE = URIRef(DEFAULT_NAMESPACES.expand("sds:Variable"))
SDS_FORMULA = URIRef(DEFAULT_NAMESPACES.expand("sds:CalculationFormula"))


def get_default_database_url(cli_url: str | None = None) -> str:
    from scripts.operational_db_url import resolve_operational_database_url

    if cli_url is not None:
        return resolve_operational_database_url(cli_url)

    from src.config.settings import settings

    configured = settings.database_url.get_secret_value() if settings.database_url else None
    return resolve_operational_database_url(cli_url, configured_url=configured)


def _count_type(graph: Graph, type_uri: URIRef) -> int:
    return sum(1 for _ in graph.subjects(RDF.type, type_uri))


def compare_exact_projection(expected: Graph, actual: Graph) -> list[str]:
    """Require exact generated triples, including predicates and stale-extra absence."""
    expected_triples = set(expected)
    actual_triples = set(actual)
    missing = expected_triples - actual_triples
    stale_or_extra = actual_triples - expected_triples
    if not missing and not stale_or_extra:
        return []
    return [
        "Generated projection does not exactly match the canonical DB semantics "
        f"(missing={len(missing)}, stale_or_extra={len(stale_or_extra)})"
    ]


def compare_projection(
    report: dict, db_concepts: list[Concept], merged_graph: Graph, graph_path: str
) -> tuple[dict, list[str]]:
    failures: list[str] = []

    db_disclosures = sum(
        1 for concept in db_concepts if concept.concept_type == "Disclosure"
    )
    db_variables = sum(
        1 for concept in db_concepts if concept.concept_type == "Variable"
    )
    db_formulas = sum(
        1
        for concept in db_concepts
        if any(formula.is_active for formula in concept.formulas)
    )
    merged_disclosures = _count_type(merged_graph, SDS_DISCLOSURE)
    merged_variables = _count_type(merged_graph, SDS_VARIABLE)
    merged_formulas = _count_type(merged_graph, SDS_FORMULA)

    if (
        "core_tbox.owl" not in graph_path
        or "generated_projection.owl" not in graph_path
    ):
        failures.append(f"Runtime is not loading the split ontology pair: {graph_path}")

    if report.get("projected_concepts") != len(db_concepts):
        failures.append(
            f"Projection report concept count mismatch: report={report.get('projected_concepts')} db={len(db_concepts)}"
        )
    if report.get("projected_disclosures") != db_disclosures:
        failures.append(
            f"Projection report disclosure count mismatch: report={report.get('projected_disclosures')} db={db_disclosures}"
        )
    if report.get("projected_variables") != db_variables:
        failures.append(
            f"Projection report variable count mismatch: report={report.get('projected_variables')} db={db_variables}"
        )
    if report.get("projected_formulas") != db_formulas:
        failures.append(
            f"Projection report formula count mismatch: report={report.get('projected_formulas')} db={db_formulas}"
        )

    if merged_disclosures < db_disclosures:
        failures.append("Merged ontology does not contain all DB disclosure concepts")
    if merged_variables < db_variables:
        failures.append("Merged ontology does not contain all DB variable concepts")
    if merged_formulas < db_formulas:
        failures.append("Merged ontology does not contain all DB formula individuals")

    for concept in db_concepts:
        concept_ref = URIRef(DEFAULT_NAMESPACES.expand(concept.uri))
        if not any(merged_graph.triples((concept_ref, None, None))):
            failures.append(
                f"Projected ontology is missing triples for concept {concept.uri}"
            )

    gate_report = {
        "runtime_graph": graph_path,
        "db_concepts": len(db_concepts),
        "db_disclosures": db_disclosures,
        "db_variables": db_variables,
        "db_formulas": db_formulas,
        "merged_disclosures": merged_disclosures,
        "merged_variables": merged_variables,
        "merged_formulas": merged_formulas,
        "projection_report": report,
    }
    return gate_report, failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Wave 2 generated ontology projection."
    )
    parser.add_argument("--db-url", type=str, default=None)
    parser.add_argument("--core", type=Path, default=DEFAULT_CORE)
    parser.add_argument("--projection", type=Path, default=DEFAULT_PROJECTION)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    args.db_url = get_default_database_url(args.db_url)

    failures: list[str] = []

    if not args.core.exists():
        failures.append(f"Missing core ontology file: {args.core}")
    if not args.projection.exists():
        failures.append(f"Missing generated projection file: {args.projection}")
    if not args.report.exists():
        failures.append(f"Missing projection summary report: {args.report}")

    if failures:
        print("Wave 2 projection gate")
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    report = json.loads(args.report.read_text(encoding="utf-8"))
    merged_graph, graph_path = load_ontology_graph()

    engine = create_engine(
        args.db_url, connect_args={"connect_timeout": 10}, pool_pre_ping=True
    )
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        # Validate the PUBLIC projection contract: compare against public-catalog
        # concepts only, matching what the generator emits (codex F09 M1).
        from src.concept_catalog_policy import public_catalog_concept_conditions

        db_concepts = (
            session.query(Concept)
            .options(joinedload(Concept.formulas))
            .filter(*public_catalog_concept_conditions(Concept))
            .order_by(Concept.uri.asc())
            .all()
        )
        semantic_snapshots = load_db_semantic_snapshots(
            session, public_catalog=True
        )
    finally:
        session.close()

    gate_report, failures = compare_projection(
        report, db_concepts, merged_graph, graph_path
    )
    expected_projection = build_generated_projection_graph(semantic_snapshots)
    actual_projection = Graph()
    actual_projection.parse(args.projection)
    failures.extend(compare_exact_projection(expected_projection, actual_projection))
    gate_report["expected_projection_triples"] = len(expected_projection)
    gate_report["actual_projection_triples"] = len(actual_projection)

    print("Wave 2 projection gate")
    print(f"  runtime_graph: {gate_report['runtime_graph']}")
    print(f"  db_concepts: {gate_report['db_concepts']}")
    print(f"  merged_disclosures: {gate_report['merged_disclosures']}")
    print(f"  merged_variables: {gate_report['merged_variables']}")
    print(f"  merged_formulas: {gate_report['merged_formulas']}")
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
