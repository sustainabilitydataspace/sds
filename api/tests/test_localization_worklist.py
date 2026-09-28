"""LOC-4 worklist exporter — SDS->Atomizer translation handoff.

Disposable-PG: seed public concepts/indicators, build the worklist, and verify each row carries the
locked source_hash and the correct per-(subject,field) status against the translation store.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.database import models as m
from src.services.localization import text_hash
from src.services.localization_service import (
    LocalizationError,
    import_concept_translations,
)
from src.services.localization_worklist import (
    build_translation_worklist,
    public_subject_uris,
)


def _disposable_session() -> Session:
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    from sqlalchemy import text as sqltext

    from src.database.init_db import init_db_for_engine

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sqltext("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(sqltext("CREATE SCHEMA public"))
    init_db_for_engine(engine)
    return Session(bind=engine)


def _seed_concept(db, uri, label, description):
    db.add(
        m.Concept(
            uri=uri,
            label=label,
            description=description,
            taxonomy="CSRD",
            concept_type="Indicator",
            projection_source="indicator_catalog",
        )
    )
    db.commit()


def _seed_indicator(db, ident, title):
    db.add(
        m.Indicator(
            id=ident, identifier=ident, title=title, dimension="E", is_active=True
        )
    )
    db.commit()


def _approved(uri, field, lang, text, source_text):
    return {
        "subject_kind": "concept",
        "subject_uri": uri,
        "field": field,
        "language": lang,
        "text": text,
        "status": "approved",
        "source_language": "en",
        "source_hash": text_hash(source_text),
        "translation_hash": text_hash(text),
        "source_type": "human_reviewed",
        "source_ref": "pkg:v1",
        "reviewer_ref": "rev:qa",
    }


def test_concept_worklist_emits_source_hash_and_status():
    db = _disposable_session()
    try:
        _seed_concept(db, "csrd:E5-5_09", "Hazardous waste", "")  # empty description
        # csrd:E5-5_09 label already approved+current -> should classify approved_current
        import_concept_translations(
            db,
            [
                _approved(
                    "csrd:E5-5_09",
                    "label",
                    "es",
                    "Residuo peligroso",
                    "Hazardous waste",
                )
            ],
            known_subject_uris={"csrd:E5-5_09"},
        )
        db.commit()

        worklist = build_translation_worklist(
            db, subject_kind="concept", target_language="es"
        )
        rows = {(r["subject_uri"], r["field"]): r for r in worklist}
        # empty description is skipped (no include_empty); label row present
        assert ("csrd:E5-5_09", "label") in rows
        assert ("csrd:E5-5_09", "description") not in rows

        label_row = rows[("csrd:E5-5_09", "label")]
        assert label_row["source_hash"] == text_hash("Hazardous waste")
        assert label_row["source_language"] == "en"
        assert label_row["target_language"] == "es"
        assert label_row["status"] == "approved_current"
    finally:
        db.close()


def test_worklist_status_missing_and_indicator_kind():
    db = _disposable_session()
    try:
        _seed_indicator(db, "E1-1", "GHG emissions")
        worklist = build_translation_worklist(
            db, subject_kind="indicator", target_language="es"
        )
        title_row = next(
            r for r in worklist if r["subject_uri"] == "E1-1" and r["field"] == "title"
        )
        assert title_row["source_hash"] == text_hash("GHG emissions")
        assert title_row["status"] == "missing"  # no translation yet
        # identifiers/codes are never in the worklist field set
        assert {r["field"] for r in worklist} <= {
            "title",
            "indicator_name",
            "description",
        }
    finally:
        db.close()


def test_worklist_rejects_unknown_kind():
    db = _disposable_session()
    try:
        with pytest.raises(ValueError, match="unsupported subject_kind"):
            build_translation_worklist(db, subject_kind="unit")
    finally:
        db.close()


def test_public_subject_uris_is_the_default_import_allowlist():
    """M2: with no explicit allowlist, the importer rejects a subject the API does not serve."""
    db = _disposable_session()
    try:
        _seed_concept(db, "csrd:E5-5_09", "Hazardous waste", "")
        # the seeded public concept is in the served set; a fabricated URI is not
        served = public_subject_uris(db, "concept")
        assert "csrd:E5-5_09" in served
        assert "csrd:GHOST_999" not in served

        # default import (no known_subject_uris) fails closed on the unserved subject
        with pytest.raises(LocalizationError, match="unknown concept"):
            import_concept_translations(
                db,
                [_approved("csrd:GHOST_999", "label", "es", "Fantasma", "Ghost")],
            )
    finally:
        db.close()


def test_import_machine_draft_with_empty_evidence_columns():
    """Live-run regression: a machine_draft row from a CSV carries empty evidence columns;
    they must be stored as NULL (not '') or the DB CHECK constraint rejects the insert.
    """
    db = _disposable_session()
    try:
        _seed_concept(db, "csrd:E5-5_09", "Hazardous waste", "")
        row = {
            "subject_kind": "concept",
            "subject_uri": "csrd:E5-5_09",
            "field": "label",
            "language": "es",
            "text": "Residuo peligroso",
            "status": "machine_draft",
            "source_language": "en",
            "source_hash": text_hash("Hazardous waste"),
            "translation_hash": text_hash("Residuo peligroso"),
            "source_type": "",  # empty CSV columns, as the Atomizer package emits
            "source_ref": "",
            "reviewer_ref": "",
        }
        report = import_concept_translations(
            db, [row], known_subject_uris={"csrd:E5-5_09"}
        )
        db.commit()
        assert report.created == 1
        stored = (
            db.query(m.LocalizedText)
            .filter(
                m.LocalizedText.subject_uri == "csrd:E5-5_09",
                m.LocalizedText.language == "es",
            )
            .one()
        )
        assert stored.status == "machine_draft"
        assert stored.source_type is None
        assert stored.source_ref is None
        assert stored.reviewer_ref is None
    finally:
        db.close()


def test_import_rejects_cross_kind_field():
    """M3: a concept package row carrying an indicator-only field is rejected, not stored."""
    db = _disposable_session()
    try:
        _seed_concept(db, "csrd:E5-5_09", "Hazardous waste", "")
        with pytest.raises(
            LocalizationError, match="unsupported field 'title' for concept"
        ):
            import_concept_translations(
                db,
                [_approved("csrd:E5-5_09", "title", "es", "Título", "Hazardous waste")],
                known_subject_uris={"csrd:E5-5_09"},
            )
    finally:
        db.close()
