"""LOC-2 concept slice — /api/v1/concepts?lang=es display fields + localization metadata.

Disposable-PG integration: seed a public concept, import an approved Spanish translation, and
verify the concept service surfaces display_label/display_description/localization for ?lang=es,
stays backward-compatible without ?lang, and falls back (stale/missing) correctly.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from rdflib import Graph
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.database import models as m
from src.services.concept_service import ConceptService
from src.services.localization import text_hash
from src.services.localization_service import import_concept_translations


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


def test_concept_payload_localized_for_es():
    db = _disposable_session()
    try:
        _seed_concept(db, "csrd:E5-5_09", "Hazardous waste", "Waste to disposal")
        import_concept_translations(
            db,
            [
                _approved(
                    "csrd:E5-5_09",
                    "label",
                    "es",
                    "Residuo peligroso",
                    "Hazardous waste",
                ),
                _approved(
                    "csrd:E5-5_09",
                    "description",
                    "es",
                    "Residuo a eliminación",
                    "Waste to disposal",
                ),
            ],
            known_subject_uris={"csrd:E5-5_09"},
        )
        db.commit()

        svc = ConceptService(db=db)
        payload = svc.get_concept_by_uri("csrd:E5-5_09", public_catalog=True)

        # without lang -> backward compatible (no display fields)
        assert "display_label" not in payload or payload.get("display_label") is None
        assert payload["label"] == "Hazardous waste"

        svc.localize_concept_payloads([payload], "es")
        assert payload["display_label"] == "Residuo peligroso"
        assert payload["display_description"] == "Residuo a eliminación"
        assert payload["label"] == "Hazardous waste"  # canonical never replaced
        loc = payload["localization"]
        assert loc["resolved_language"] == "es"
        assert loc["status"] == "approved_current"
        assert loc["missing_fields"] == [] and loc["stale_fields"] == []
    finally:
        db.close()


def test_concept_localization_missing_and_stale_fallback():
    db = _disposable_session()
    try:
        _seed_concept(db, "c:1", "Energy", "Energy use")
        # only label translated, and against an OLD source -> stale
        import_concept_translations(
            db,
            [_approved("c:1", "label", "es", "Energía", "Energy OLD")],
            known_subject_uris={"c:1"},
        )
        db.commit()

        svc = ConceptService(db=db)
        payload = svc.get_concept_by_uri("c:1", public_catalog=True)
        svc.localize_concept_payloads([payload], "es")

        # label is stale (source_hash mismatch) -> source fallback; description missing
        assert payload["display_label"] == "Energy"
        assert payload["display_description"] == "Energy use"
        loc = payload["localization"]
        assert "label" in loc["stale_fields"]
        assert "description" in loc["missing_fields"]
        assert loc["status"] == "source_fallback"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_concept_detail_router_passes_lang_and_is_backcompat_without_it():
    import src.api.routers.ontology as ontology

    svc = MagicMock()
    svc.has_semantic_data.return_value = True
    # fresh dict per call so one call's localization mutation does not leak to the next
    svc.get_concept_by_uri.side_effect = lambda *a, **k: {
        "uri": "csrd:E5-5_09",
        "label": "Hazardous waste",
        "taxonomy": "CSRD",
        "concept_type": "Indicator",
    }

    def _localize(payloads, lang):
        if lang == "es":
            for p in payloads:
                p["display_label"] = "Residuo peligroso"
                p["localization"] = {
                    "resolved_language": "es",
                    "status": "approved_current",
                }
        return payloads

    svc.localize_concept_payloads.side_effect = _localize

    es = await ontology.get_concept_details(
        "csrd:E5-5_09",
        lang="es",
        graph=Graph(),
        concept_service=svc,
        current_user=MagicMock(),
    )
    assert es.display_label == "Residuo peligroso"
    assert es.localization["status"] == "approved_current"

    # default (no lang) -> display fields stay None (additive contract)
    plain = await ontology.get_concept_details(
        "csrd:E5-5_09", graph=Graph(), concept_service=svc, current_user=MagicMock()
    )
    assert plain.display_label is None and plain.localization is None


def test_translation_aware_search_and_readiness():
    db = _disposable_session()
    try:
        _seed_concept(db, "csrd:E5-5_09", "Hazardous waste", "Waste to disposal")
        _seed_concept(db, "csrd:E1-1", "Energy", "Energy use")
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
            known_subject_uris={"csrd:E5-5_09", "csrd:E1-1"},
        )
        db.commit()
        svc = ConceptService(db=db)

        # search by the SPANISH term finds the concept via its translation
        results = svc.search_concepts_localized("Residuo", limit=20, lang="es")
        match = next((r for r in results if r["uri"] == "csrd:E5-5_09"), None)
        assert match is not None
        assert match["matched_language"] == "es"
        assert match["matched_field"] == "label"
        assert match["display_label"] == "Residuo peligroso"
        assert match["label"] == "Hazardous waste"  # canonical still present

        # readiness: exactly one concept has an approved es label; the rest (incl. any
        # migration-seeded public concept) are missing.
        readiness = svc.localization_readiness("es", field="label")
        assert readiness["total_subjects"] >= 2
        assert readiness["approved_current"] == 1
        assert readiness["missing"] == readiness["total_subjects"] - 1
        assert readiness["coverage_percent"] == round(
            100.0 / readiness["total_subjects"], 2
        )
    finally:
        db.close()


def test_search_excludes_stale_translation_match():
    """A stale approved translation (source changed) must NOT surface/relabel a search hit."""
    db = _disposable_session()
    try:
        _seed_concept(db, "csrd:E5-5_09", "Hazardous waste", "Waste to disposal")
        # approved es label hashed against an OLD source -> now stale
        import_concept_translations(
            db,
            [
                _approved(
                    "csrd:E5-5_09", "label", "es", "Residuo peligroso", "OLD SOURCE"
                )
            ],
            known_subject_uris={"csrd:E5-5_09"},
        )
        db.commit()
        svc = ConceptService(db=db)

        # the Spanish term only exists in the STALE row; it must not create a search hit
        results = svc.search_concepts_localized(
            "Residuo peligroso", limit=20, lang="es"
        )
        match = next((r for r in results if r["uri"] == "csrd:E5-5_09"), None)
        assert match is None

        # and if the concept matches canonically, the stale row must not relabel it as es
        canonical = svc.search_concepts_localized("Hazardous", limit=20, lang="es")
        hit = next((r for r in canonical if r["uri"] == "csrd:E5-5_09"), None)
        assert hit is not None
        assert hit["matched_language"] == "en"  # not relabeled to the stale es row
    finally:
        db.close()
