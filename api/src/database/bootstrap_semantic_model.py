"""Bootstrap canonical semantic concepts from the bundled ontology."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

import structlog
from src.concept_runtime_aliases import (
    canonical_disclosure_uri,
    legacy_disclosure_aliases_by_canonical,
)
from src.database.models import (
    Concept,
    ConceptEquivalence,
    ConceptFormula,
    ConceptVariable,
    Indicator,
)
from src.ontology.semantic_backfill import (
    OntologyConceptRecord,
    load_semantic_records,
    normalize_concept_code,
    normalize_indicator_code,
    ontology_sha256,
)

logger = structlog.get_logger(__name__)

SEMANTIC_BOOTSTRAP_LOCK_KEY = 640_104_216


def _with_postgres_transaction_lock(db: Session) -> bool:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return False

    db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": SEMANTIC_BOOTSTRAP_LOCK_KEY}
    )
    return True


def build_indicator_lookup(db: Session) -> dict[str, dict[str, list[Indicator]]]:
    """Build deterministic exact-match lookup tables for semantic concepts."""

    lookup: dict[str, dict[str, list[Indicator]]] = {
        "CSRD": defaultdict(list),
        "GRI": defaultdict(list),
    }

    for indicator in db.query(Indicator).all():
        esrs_key = normalize_indicator_code(indicator.code_esrs)
        if esrs_key:
            lookup["CSRD"][esrs_key].append(indicator)

        gri_key = normalize_indicator_code(indicator.code_gri)
        if gri_key:
            lookup["GRI"][gri_key].append(indicator)

    return lookup


def match_indicator_id(
    record: OntologyConceptRecord,
    lookup: dict[str, dict[str, list[Indicator]]],
) -> Optional[str]:
    """Return the unique matching catalog indicator id for a semantic record."""

    code = normalize_concept_code(record.curie)
    if not code:
        return None

    candidates = lookup.get(record.taxonomy, {}).get(code, [])
    if len(candidates) == 1:
        return candidates[0].id

    return None


def summarize_semantic_records(
    records: dict[str, OntologyConceptRecord],
    matched_indicator_ids: dict[str, Optional[str]],
    ontology_path: Path,
) -> dict:
    """Return a compact deterministic summary of ontology semantic records."""

    by_type = Counter(record.concept_type for record in records.values())
    by_state = Counter(record.concept_state for record in records.values())
    formulas = sum(1 for record in records.values() if record.formula is not None)
    derived_formulas = sum(
        1
        for record in records.values()
        if record.formula is not None and record.formula.is_derived
    )
    variables = sum(len(record.variables) for record in records.values())
    equivalences = sum(len(record.equivalences) for record in records.values())
    matched = sum(1 for indicator_id in matched_indicator_ids.values() if indicator_id)
    unmatched = sorted(
        curie
        for curie, indicator_id in matched_indicator_ids.items()
        if not indicator_id
    )

    return {
        "ontology_path": str(ontology_path),
        "ontology_sha256": ontology_sha256(ontology_path),
        "concept_count": len(records),
        "concepts_by_type": dict(by_type),
        "concepts_by_state": dict(by_state),
        "formula_count": formulas,
        "derived_formula_count": derived_formulas,
        "variable_count": variables,
        "equivalence_count": equivalences,
        "matched_indicator_count": matched,
        "unmatched_indicator_count": len(unmatched),
        "unmatched_concepts": unmatched,
    }


def _purge_stale_concepts(db: Session, active_uris: set[str]) -> int:
    stale_concepts = (
        db.query(Concept)
        .filter(
            Concept.concept_type.in_(["Disclosure", "Variable"]),
            Concept.uri.notin_(active_uris),
        )
        .all()
    )
    count = len(stale_concepts)
    for concept in stale_concepts:
        db.delete(concept)
    return count


def _missing_record_uris(
    db: Session, records: dict[str, OntologyConceptRecord]
) -> list[str]:
    """Return bundled semantic record URIs that are absent from the DB."""
    active_uris = {_runtime_record_uri(record) for record in records.values()}
    if not active_uris:
        return []

    existing_uris = {
        concept.uri
        for concept in db.query(Concept).filter(Concept.uri.in_(active_uris)).all()
    }
    return sorted(active_uris - existing_uris)


def _runtime_record_uri(record: OntologyConceptRecord) -> str:
    return (
        canonical_disclosure_uri(record.uri)
        or canonical_disclosure_uri(record.curie)
        or record.uri
    )


def _runtime_target_uri(uri: str) -> str:
    return canonical_disclosure_uri(uri) or uri


def _purge_migrated_legacy_concepts(db: Session, active_uris: set[str]) -> int:
    stale_aliases = _migrated_legacy_alias_uris(active_uris)
    if not stale_aliases:
        return 0

    stale_concepts = db.query(Concept).filter(Concept.uri.in_(stale_aliases)).all()
    count = len(stale_concepts)
    for concept in stale_concepts:
        db.delete(concept)
    return count


def _migrated_legacy_alias_uris(active_uris: set[str]) -> set[str]:
    legacy_aliases = legacy_disclosure_aliases_by_canonical()
    return {
        alias
        for canonical_uri, aliases in legacy_aliases.items()
        if canonical_uri in active_uris
        for alias in aliases
    }


def _existing_migrated_legacy_alias_count(
    db: Session, records: dict[str, OntologyConceptRecord]
) -> int:
    active_uris = {_runtime_record_uri(record) for record in records.values()}
    stale_aliases = _migrated_legacy_alias_uris(active_uris)
    if not stale_aliases:
        return 0
    return db.query(Concept).filter(Concept.uri.in_(stale_aliases)).count()


def upsert_semantic_records(
    db: Session,
    records: dict[str, OntologyConceptRecord],
    matched_indicator_ids: dict[str, Optional[str]],
    *,
    purge_stale: bool = False,
) -> dict[str, int]:
    """Upsert ontology records into canonical semantic tables without committing."""

    runtime_records = {
        key: (record, _runtime_record_uri(record)) for key, record in records.items()
    }
    active_runtime_uris = {uri for _record, uri in runtime_records.values()}

    existing_by_uri = {
        concept.uri: concept
        for concept in db.query(Concept)
        .filter(Concept.uri.in_(active_runtime_uris))
        .all()
    }
    lookup_by_indicator_id = {
        indicator.id: indicator
        for indicator in db.query(Indicator)
        .filter(Indicator.id.in_([i for i in matched_indicator_ids.values() if i]))
        .all()
    }

    created = 0
    updated = 0
    refreshed_children = 0

    if purge_stale:
        stale_count = _purge_stale_concepts(db, active_runtime_uris)
    else:
        stale_count = 0
    purged_legacy_aliases = _purge_migrated_legacy_concepts(db, active_runtime_uris)

    concept_rows: list[Concept] = []
    for record, record_uri in runtime_records.values():
        concept = existing_by_uri.get(record_uri)
        if concept is None:
            concept = Concept(uri=record_uri)
            db.add(concept)
            created += 1
        else:
            updated += 1

        concept.indicator_id = matched_indicator_ids.get(record.curie)
        concept.label = record.label
        concept.description = record.description
        concept.taxonomy = record.taxonomy
        concept.concept_type = record.concept_type
        concept.unit = record.unit
        concept.temporal_granularity = record.temporal_granularity
        concept.hierarchy_level = None
        concept.concept_state = record.concept_state
        concept_rows.append(concept)

        if concept.indicator_id:
            lookup_by_indicator_id[concept.indicator_id].concept_state = (
                record.concept_state
            )

    db.flush()

    target_ids = [concept.id for concept in concept_rows if concept.id]
    if target_ids:
        db.query(ConceptFormula).filter(
            ConceptFormula.concept_id.in_(target_ids)
        ).delete(synchronize_session=False)
        db.query(ConceptVariable).filter(
            ConceptVariable.concept_id.in_(target_ids)
        ).delete(synchronize_session=False)
        db.query(ConceptEquivalence).filter(
            ConceptEquivalence.source_concept_id.in_(target_ids)
        ).delete(synchronize_session=False)
        refreshed_children = len(target_ids)

    by_uri = {concept.uri: concept for concept in concept_rows}
    for record, record_uri in runtime_records.values():
        concept = by_uri[record_uri]

        if record.formula is not None:
            db.add(
                ConceptFormula(
                    concept_id=concept.id,
                    expression=record.formula.expression,
                    expression_language=record.formula.expression_language,
                    version=1,
                    is_active=True,
                )
            )

        for variable in record.variables:
            db.add(
                ConceptVariable(
                    concept_id=concept.id,
                    variable_uri=variable.uri,
                    variable_label=variable.label,
                    ordering=variable.ordering,
                    aggregation_method=variable.aggregation_method,
                    temporal_granularity=variable.temporal_granularity,
                )
            )

        for equivalence in record.equivalences:
            db.add(
                ConceptEquivalence(
                    source_concept_id=concept.id,
                    target_uri=_runtime_target_uri(equivalence.target_uri),
                    target_taxonomy=equivalence.target_taxonomy,
                    relationship_type=equivalence.relationship_type,
                )
            )

    return {
        "created": created,
        "updated": updated,
        "refreshed_children": refreshed_children,
        "purged_stale": stale_count,
        "purged_legacy_aliases": purged_legacy_aliases,
    }


def bootstrap_semantic_model_if_empty(
    db: Session,
    *,
    ontology_path: Optional[Path] = None,
    purge_stale: bool = False,
) -> dict[str, object]:
    """Seed or repair canonical semantic tables from the bundled ontology.

    Startup must not leave a partially seeded ``concepts`` table in place. Older
    deployments can contain only a subset of the bundled semantic records, and a
    pure "if empty" guard would keep serving that stale subset forever.
    """

    concepts_before = db.query(Concept).count()
    resolved_path, _graph, records = load_semantic_records(ontology_path)
    missing_before = _missing_record_uris(db, records)
    legacy_aliases_before = _existing_migrated_legacy_alias_count(db, records)

    if (
        concepts_before > 0
        and not missing_before
        and legacy_aliases_before == 0
        and not purge_stale
    ):
        return {
            "status": "skipped",
            "concepts_before": concepts_before,
            "concepts_total": concepts_before,
            "concept_count": len(records),
            "missing_concepts": 0,
            "created": 0,
            "updated": 0,
            "refreshed_children": 0,
            "purged_stale": 0,
            "purged_legacy_aliases": 0,
        }

    _with_postgres_transaction_lock(db)
    concepts_before = db.query(Concept).count()
    missing_after_lock = _missing_record_uris(db, records)
    legacy_aliases_after_lock = _existing_migrated_legacy_alias_count(db, records)
    if (
        concepts_before > 0
        and not missing_after_lock
        and legacy_aliases_after_lock == 0
        and not purge_stale
    ):
        return {
            "status": "skipped_after_lock",
            "concepts_before": concepts_before,
            "concepts_total": concepts_before,
            "concept_count": len(records),
            "missing_concepts": 0,
            "created": 0,
            "updated": 0,
            "refreshed_children": 0,
            "purged_stale": 0,
            "purged_legacy_aliases": 0,
        }

    lookup = build_indicator_lookup(db)
    matched_indicator_ids = {
        record.curie: match_indicator_id(record, lookup) for record in records.values()
    }
    record_summary = summarize_semantic_records(
        records, matched_indicator_ids, resolved_path
    )

    try:
        stats = upsert_semantic_records(
            db, records, matched_indicator_ids, purge_stale=purge_stale
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    concepts_total = db.query(Concept).count()
    summary = {
        "status": "seeded" if concepts_before == 0 else "refreshed",
        "concepts_before": concepts_before,
        "concepts_total": concepts_total,
        "ontology_path": record_summary["ontology_path"],
        "ontology_sha256": record_summary["ontology_sha256"],
        "concept_count": record_summary["concept_count"],
        "missing_concepts": len(missing_after_lock),
        "legacy_alias_concepts": legacy_aliases_after_lock,
        "formula_count": record_summary["formula_count"],
        "variable_count": record_summary["variable_count"],
        "equivalence_count": record_summary["equivalence_count"],
        "matched_indicator_count": record_summary["matched_indicator_count"],
        "unmatched_indicator_count": record_summary["unmatched_indicator_count"],
        **stats,
    }
    logger.info("Seeded canonical semantic model", **summary)
    return summary
