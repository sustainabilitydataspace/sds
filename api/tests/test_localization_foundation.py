"""LOC-1 foundation tests — localization resolver + importer/validator.

Pure-resolver tests run anywhere; importer/repository tests use the disposable PG.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.services.localization import (
    TranslationRow,
    language_fallback_chain,
    normalize_language,
    resolve_display,
    text_hash,
)
from src.services.localization_service import (
    LocalizationError,
    LocalizationRepository,
    import_concept_translations,
)

# --- pure resolver --------------------------------------------------------------


def test_text_hash_is_deterministic_and_nfc():
    # NFC: composed vs decomposed accent hash identically.
    composed = "Emisión"  # ó as single codepoint
    decomposed = "Emisión"  # o + combining acute
    assert text_hash(composed) == text_hash(decomposed)
    assert len(text_hash("x")) == 64


def test_language_fallback_chain():
    assert language_fallback_chain("es-ES", "en") == ["es-es", "es", "en"]
    assert language_fallback_chain("es", "en") == ["es", "en"]
    assert language_fallback_chain(None, "en") == ["en"]
    assert normalize_language("ES_es") == "es-es"


def test_resolver_approved_current():
    src = {"label": "Hazardous waste", "description": "Waste to disposal"}
    rows = [
        TranslationRow(
            "label", "es", "Residuo peligroso", text_hash("Hazardous waste")
        ),
        TranslationRow(
            "description", "es", "Residuo a eliminación", text_hash("Waste to disposal")
        ),
    ]
    result = resolve_display(
        source_fields=src, rows=rows, requested_language="es", source_language="en"
    )
    assert result.display["label"] == "Residuo peligroso"
    assert result.resolved_language == "es"
    assert result.status == "approved_current"
    assert result.missing_fields == [] and result.stale_fields == []


def test_resolver_missing_falls_back_to_source():
    src = {"label": "Hazardous waste"}
    result = resolve_display(
        source_fields=src, rows=[], requested_language="es", source_language="en"
    )
    assert result.display["label"] == "Hazardous waste"  # canonical source fallback
    assert result.status == "source_fallback"
    assert result.missing_fields == ["label"]


def test_resolver_stale_source_hash_excluded():
    src = {"label": "Hazardous waste (updated)"}
    # approved row was hashed against the OLD source text -> stale -> excluded
    rows = [
        TranslationRow("label", "es", "Residuo peligroso", text_hash("Hazardous waste"))
    ]
    result = resolve_display(
        source_fields=src, rows=rows, requested_language="es", source_language="en"
    )
    assert result.display["label"] == "Hazardous waste (updated)"
    assert result.stale_fields == ["label"]
    assert result.missing_fields == []


def test_resolver_region_broadens_to_base_language():
    src = {"label": "Hazardous waste"}
    rows = [
        TranslationRow("label", "es", "Residuo peligroso", text_hash("Hazardous waste"))
    ]
    result = resolve_display(
        source_fields=src, rows=rows, requested_language="es-ES", source_language="en"
    )
    assert result.display["label"] == "Residuo peligroso"  # es-ES -> es
    assert result.resolved_language == "es"
    assert result.status == "language_fallback"
    assert "es" in result.fallback_chain


# --- importer / repository (disposable PG) --------------------------------------


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


def _approved_row(uri, field, lang, text, source_text):
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
        "source_ref": "pkg:translations:v1",
        "reviewer_ref": "reviewer:qa",
    }


def test_import_is_idempotent_and_repository_resolves():
    db = _disposable_session()
    try:
        rows = [
            _approved_row(
                "csrd:E5-5_09", "label", "es", "Residuo peligroso", "Hazardous waste"
            )
        ]
        r1 = import_concept_translations(db, rows, known_subject_uris={"csrd:E5-5_09"})
        db.commit()
        assert r1.created == 1
        # re-import identical -> no-op
        r2 = import_concept_translations(db, rows, known_subject_uris={"csrd:E5-5_09"})
        db.commit()
        assert r2.created == 0 and r2.unchanged == 1

        repo = LocalizationRepository(db)
        res = repo.localize(
            subject_kind="concept",
            subject_uri="csrd:E5-5_09",
            source_fields={"label": "Hazardous waste"},
            requested_language="es",
        )
        assert res.display["label"] == "Residuo peligroso"
        assert res.status == "approved_current"
    finally:
        db.close()


def test_import_supersedes_on_change():
    db = _disposable_session()
    try:
        import_concept_translations(
            db,
            [_approved_row("c:1", "label", "es", "Viejo", "Source")],
            known_subject_uris={"c:1"},
        )
        db.commit()
        report = import_concept_translations(
            db,
            [_approved_row("c:1", "label", "es", "Nuevo", "Source")],
            known_subject_uris={"c:1"},
        )
        db.commit()
        assert report.superseded == 1 and report.created == 1
        repo = LocalizationRepository(db)
        res = repo.localize(
            subject_kind="concept",
            subject_uri="c:1",
            source_fields={"label": "Source"},
            requested_language="es",
        )
        assert res.display["label"] == "Nuevo"
    finally:
        db.close()


def test_import_rejects_approved_without_evidence_and_duplicates():
    db = _disposable_session()
    try:
        bad = _approved_row("c:1", "label", "es", "X", "S")
        bad["reviewer_ref"] = None
        with pytest.raises(LocalizationError, match="reviewer_ref"):
            import_concept_translations(db, [bad], known_subject_uris={"c:1"})

        dup = [
            _approved_row("c:1", "label", "es", "A", "S"),
            _approved_row("c:1", "label", "es", "B", "S"),
        ]
        with pytest.raises(LocalizationError, match="duplicate active approved"):
            import_concept_translations(db, dup, known_subject_uris={"c:1"})

        with pytest.raises(LocalizationError, match="unknown concept"):
            import_concept_translations(
                db,
                [_approved_row("c:unknown", "label", "es", "A", "S")],
                known_subject_uris={"c:1"},
            )
    finally:
        db.close()


def test_coverage_reports_status_breakdown():
    db = _disposable_session()
    try:
        import_concept_translations(
            db,
            [_approved_row("c:1", "label", "es", "Uno", "One")],
            known_subject_uris={"c:1", "c:2"},
        )
        db.commit()
        repo = LocalizationRepository(db)
        cov = repo.coverage(
            subject_kind="concept",
            language="es",
            subjects={"c:1": {"label": "One"}, "c:2": {"label": "Two"}},
        )
        assert cov["total_subjects"] == 2
        assert cov["approved_current"] == 1
        assert cov["missing"] == 1
        assert cov["coverage_percent"] == 50.0
    finally:
        db.close()
