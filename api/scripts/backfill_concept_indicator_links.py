#!/usr/bin/env python3
"""Wave 1.5 deterministic linkage between semantic concepts and E1 indicators."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.database.init_db import init_db_for_engine  # noqa: E402
from src.database.models import Concept, ConceptIndicatorLink, Indicator  # noqa: E402

DEFAULT_REPORT = REPO_ROOT / "data" / "extracted" / "analysis" / "wave15_linkage_summary.json"


@dataclass(frozen=True)
class LinkCandidate:
    indicator_id: str
    link_type: str
    confidence: Decimal
    rationale: str


def get_default_database_url(cli_url: str | None = None) -> str:
    from scripts.operational_db_settings import (
        resolve_operational_database_url_with_settings,
    )

    return resolve_operational_database_url_with_settings(cli_url)


def _canonical_taxonomy_name(value: str | None) -> str:
    mapping = {"CSRD": "CSRD", "GRI": "GRI", "Sygris": "SYGRIS", "SDS": "SDS"}
    return mapping.get((value or "").strip(), (value or "").strip().upper())


def _normalized_indicator_code(indicator: Indicator, taxonomy: str) -> Optional[str]:
    from src.ontology.semantic_backfill import normalize_indicator_code

    if taxonomy == "CSRD":
        return normalize_indicator_code(indicator.code_esrs)
    if taxonomy == "GRI":
        code = normalize_indicator_code(indicator.code_gri)
        if code and code.startswith("GRI"):
            return code[3:]
        return code
    return None


def _family_prefix_for_concept(concept: Concept) -> Optional[str]:
    from src.ontology.semantic_backfill import normalize_concept_code

    code = normalize_concept_code(concept.uri)
    if not code:
        return None
    if _canonical_taxonomy_name(concept.taxonomy) == "CSRD":
        return code
    if _canonical_taxonomy_name(concept.taxonomy) == "GRI":
        return code
    return None


def _normalized_title(value: str | None) -> Optional[str]:
    if not value:
        return None
    title = " ".join(str(value).strip().casefold().split())
    return title or None


def build_candidates(concept: Concept, indicators: list[Indicator]) -> list[LinkCandidate]:
    from src.ontology.semantic_backfill import normalize_concept_code

    taxonomy = _canonical_taxonomy_name(concept.taxonomy)
    if taxonomy not in {"CSRD", "GRI"}:
        return []

    exact_code = normalize_concept_code(concept.uri)
    family_prefix = _family_prefix_for_concept(concept)
    title_key = _normalized_title(concept.label)

    by_title: dict[str, list[Indicator]] = defaultdict(list)
    for indicator in indicators:
        by_title[_normalized_title(indicator.title)].append(indicator)

    candidates: dict[str, LinkCandidate] = {}
    for indicator in indicators:
        indicator_code = _normalized_indicator_code(indicator, taxonomy)
        if exact_code and indicator_code and indicator_code == exact_code:
            candidates[indicator.id] = LinkCandidate(
                indicator_id=indicator.id,
                link_type="exact_code_same_taxonomy",
                confidence=Decimal("1.00"),
                rationale=f"{concept.uri} exact code match",
            )

    if title_key and len(by_title.get(title_key, [])) == 1:
        indicator = by_title[title_key][0]
        existing = candidates.get(indicator.id)
        if existing is None or existing.confidence < Decimal("0.95"):
            candidates[indicator.id] = LinkCandidate(
                indicator_id=indicator.id,
                link_type="exact_unique_title_same_taxonomy",
                confidence=Decimal("0.95"),
                rationale=f"{concept.label} unique exact title match",
            )

    if family_prefix:
        for indicator in indicators:
            indicator_code = _normalized_indicator_code(indicator, taxonomy)
            if indicator_code and indicator_code.startswith(family_prefix):
                existing = candidates.get(indicator.id)
                if existing is None or existing.confidence < Decimal("0.85"):
                    candidates[indicator.id] = LinkCandidate(
                        indicator_id=indicator.id,
                        link_type="family_prefix_same_taxonomy",
                        confidence=Decimal("0.85"),
                        rationale=f"{concept.uri} family prefix match",
                    )

    return sorted(candidates.values(), key=lambda item: (-item.confidence, item.link_type, item.indicator_id))


def summarize(concepts: list[Concept], links: list[tuple[str, LinkCandidate]]) -> dict:
    by_type = Counter(candidate.link_type for _, candidate in links)
    linked_concepts = Counter(uri for uri, _ in links)
    disclosure_count = sum(1 for concept in concepts if concept.concept_type == "Disclosure")
    linked_disclosures = sum(1 for concept in concepts if concept.concept_type == "Disclosure" and concept.uri in linked_concepts)
    return {
        "concept_count": len(concepts),
        "disclosure_count": disclosure_count,
        "linked_disclosure_count": linked_disclosures,
        "linked_concept_count": len(linked_concepts),
        "link_count": len(links),
        "links_by_type": dict(by_type),
        "unlinked_disclosures": [
            concept.uri
            for concept in concepts
            if concept.concept_type == "Disclosure" and concept.uri not in linked_concepts
        ],
    }


def run_linkage(*, db_url: str, report_path: Path, dry_run: bool) -> tuple[dict, int]:
    engine = create_engine(db_url, connect_args={"connect_timeout": 10}, pool_pre_ping=True)
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        concepts = session.query(Concept).order_by(Concept.uri.asc()).all()
        indicators = session.query(Indicator).filter(Indicator.is_active.is_(True)).all()

        indicators_by_taxonomy: dict[str, list[Indicator]] = defaultdict(list)
        for indicator in indicators:
            if indicator.code_esrs:
                indicators_by_taxonomy["CSRD"].append(indicator)
            if indicator.code_gri:
                indicators_by_taxonomy["GRI"].append(indicator)

        all_links: list[tuple[str, LinkCandidate]] = []
        for concept in concepts:
            taxonomy = _canonical_taxonomy_name(concept.taxonomy)
            for candidate in build_candidates(concept, indicators_by_taxonomy.get(taxonomy, [])):
                all_links.append((concept.uri, candidate))

        report = summarize(concepts, all_links)
        report["dry_run"] = dry_run

        if not dry_run:
            session.query(ConceptIndicatorLink).delete(synchronize_session=False)
            uri_to_id = {concept.uri: concept.id for concept in concepts}
            for concept_uri, candidate in all_links:
                session.add(
                    ConceptIndicatorLink(
                        concept_id=uri_to_id[concept_uri],
                        indicator_id=candidate.indicator_id,
                        link_type=candidate.link_type,
                        confidence=candidate.confidence,
                        rationale=candidate.rationale,
                    )
                )
            session.commit()

        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return report, len(all_links)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill deterministic concept-indicator links.")
    parser.add_argument("--db-url", type=str, default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.db_url = get_default_database_url(args.db_url)

    report, count = run_linkage(db_url=args.db_url, report_path=args.report, dry_run=args.dry_run)
    print("Wave 1.5 concept-indicator linkage")
    print(f"  concepts: {report['concept_count']}")
    print(f"  disclosures: {report['disclosure_count']}")
    print(f"  linked disclosures: {report['linked_disclosure_count']}")
    print(f"  links: {count}")
    print(f"  report: {args.report}")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
