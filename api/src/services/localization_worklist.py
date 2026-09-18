"""Translation worklist exporter — the SDS->Atomizer handoff (LOC-4).

The converged design assigns translation PRODUCTION + PACKAGING to Atomizer
(candidate-v2.md §Package Contract), while SDS owns the import validator + serve path (already
built) and must tell Atomizer *exactly what to translate*. This module emits, per public subject
and human-facing field, the canonical source text plus the LOCKED ``source_hash`` (SHA-256 over
NFC-normalized UTF-8) that SDS will later validate the translation against. Atomizer fills in the
target text + evidence and emits ``sds_translations.csv``; SDS imports it via
``localization_service.import_translations`` (which re-checks every hash and gate).

No identifiers/codes/units/formulas are emitted for translation — only the human-facing fields
registered per subject_kind below.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.database.models import Concept, Indicator, LocalizedText
from src.services.localization import DEFAULT_LANGUAGE, normalize_language, text_hash

#: Human-facing source fields exported for translation, per subject_kind. Identifiers, codes, unit
#: symbols and formulas are intentionally excluded.
WORKLIST_FIELDS: Dict[str, Tuple[str, ...]] = {
    "concept": ("label", "description"),
    "indicator": ("title", "indicator_name", "description"),
}


def _iter_concept_sources(db: Session) -> Iterable[Tuple[str, Dict[str, str]]]:
    from src.concept_catalog_policy import public_catalog_concept_conditions
    from src.ontology.curie import DEFAULT_NAMESPACES

    rows = db.query(Concept).filter(*public_catalog_concept_conditions(Concept)).all()
    for concept in rows:
        subject_uri = DEFAULT_NAMESPACES.compact(concept.uri)
        yield subject_uri, {
            "label": concept.label or "",
            "description": concept.description or "",
        }


def _iter_indicator_sources(db: Session) -> Iterable[Tuple[str, Dict[str, str]]]:
    rows = db.query(Indicator).filter(Indicator.is_active.is_(True)).all()
    for indicator in rows:
        # The PUBLIC identifier is the subject_uri (never the DB id) — matches the read path.
        yield indicator.identifier, {
            "title": indicator.title or "",
            "indicator_name": indicator.indicator_name or "",
            "description": indicator.description or "",
        }


_SOURCE_ITERATORS = {
    "concept": _iter_concept_sources,
    "indicator": _iter_indicator_sources,
}


def public_subject_uris(db: Session, subject_kind: str) -> set:
    """The set of public subject_uris the API serves for ``subject_kind``.

    This is the canonical import-gate allowlist: a translation may only be stored for a subject
    the read path would actually serve. Same scoping as the worklist (concepts via the public
    catalog predicate, indicators via ``is_active``).
    """
    if subject_kind not in _SOURCE_ITERATORS:
        raise ValueError(f"unsupported subject_kind {subject_kind!r}")
    return {subject_uri for subject_uri, _ in _SOURCE_ITERATORS[subject_kind](db)}


def _classify(rows: List[LocalizedText], source_text: str) -> str:
    """Same status vocabulary as readiness coverage (approved_current/stale/draft_only/missing)."""
    approved = [r for r in rows if r.status == "approved" and r.effective_to is None]
    if any((r.source_hash or "").lower() == text_hash(source_text) for r in approved):
        return "approved_current"
    if approved:
        return "stale"
    if any(r.status in ("draft", "machine_draft", "reviewed") for r in rows):
        return "draft_only"
    return "missing"


def build_translation_worklist(
    db: Session,
    *,
    subject_kind: str,
    source_language: str = DEFAULT_LANGUAGE,
    target_language: Optional[str] = None,
    include_empty: bool = False,
) -> List[Dict[str, Any]]:
    """Build the per-(subject, field) translation worklist for one subject_kind.

    Each row carries subject_kind, subject_uri, field, source_language, source_text and the locked
    ``source_hash``. When ``target_language`` is given, each row is annotated with its current
    status against the existing translation store so Atomizer can skip approved_current rows and
    prioritize missing/stale ones. Empty source fields are skipped unless ``include_empty``.
    """
    if subject_kind not in _SOURCE_ITERATORS:
        raise ValueError(f"unsupported subject_kind {subject_kind!r}")
    fields = WORKLIST_FIELDS[subject_kind]
    src_lang = normalize_language(source_language)

    existing: Dict[Tuple[str, str], List[LocalizedText]] = {}
    tgt_lang: Optional[str] = None
    if target_language:
        tgt_lang = normalize_language(target_language)
        rows = (
            db.query(LocalizedText)
            .filter(
                LocalizedText.subject_kind == subject_kind,
                LocalizedText.language == tgt_lang,
                LocalizedText.tenant_id.is_(None),
            )
            .all()
        )
        for row in rows:
            existing.setdefault((row.subject_uri, row.field), []).append(row)

    worklist: List[Dict[str, Any]] = []
    for subject_uri, sources in _SOURCE_ITERATORS[subject_kind](db):
        for field in fields:
            source_text = sources.get(field, "")
            if not source_text and not include_empty:
                continue
            entry: Dict[str, Any] = {
                "subject_kind": subject_kind,
                "subject_uri": subject_uri,
                "field": field,
                "source_language": src_lang,
                "source_text": source_text,
                "source_hash": text_hash(source_text),
            }
            if tgt_lang is not None:
                entry["target_language"] = tgt_lang
                entry["status"] = _classify(
                    existing.get((subject_uri, field), []), source_text
                )
            worklist.append(entry)
    return worklist
