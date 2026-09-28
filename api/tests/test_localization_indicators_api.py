"""LOC-3 indicator slice — indicators ?lang=es display fields + localization metadata.

Disposable-PG integration: seed a public indicator, import an approved Spanish translation keyed by
the indicator's public identifier, and verify the indicators router surfaces
display_title/display_indicator_name/display_description + localization for ?lang=es, stays
backward-compatible without ?lang, and that the generalized importer rejects cross-kind rows.
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
    import_indicator_translations,
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


def _seed_indicator(db, ident, title, indicator_name, description):
    db.add(
        m.Indicator(
            id=ident,
            identifier=ident,
            title=title,
            indicator_name=indicator_name,
            description=description,
            dimension="E",
        )
    )
    db.commit()


def _approved(kind, uri, field, lang, text, source_text):
    return {
        "subject_kind": kind,
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


@pytest.mark.asyncio
async def test_get_indicator_localized_for_es():
    import src.api.routers.indicators as indicators

    db = _disposable_session()
    try:
        _seed_indicator(
            db,
            "E1-1",
            "GHG emissions",
            "Gross Scope 1",
            "Total gross Scope 1 emissions",
        )
        import_indicator_translations(
            db,
            [
                _approved(
                    "indicator",
                    "E1-1",
                    "title",
                    "es",
                    "Emisiones de GEI",
                    "GHG emissions",
                ),
                _approved(
                    "indicator",
                    "E1-1",
                    "indicator_name",
                    "es",
                    "Alcance 1 bruto",
                    "Gross Scope 1",
                ),
            ],
            known_subject_uris={"E1-1"},
        )
        db.commit()

        # without lang -> backward compatible
        plain = await indicators.get_indicator("E1-1", lang=None, db=db, user=object())
        assert plain.title == "GHG emissions"
        assert plain.display_title is None and plain.localization is None

        es = await indicators.get_indicator("E1-1", lang="es", db=db, user=object())
        assert es.title == "GHG emissions"  # canonical never replaced
        assert es.display_title == "Emisiones de GEI"
        assert es.display_indicator_name == "Alcance 1 bruto"
        # description had no translation -> source fallback + recorded missing
        assert es.display_description == "Total gross Scope 1 emissions"
        assert es.localization["status"] == "source_fallback"
        assert "description" in es.localization["missing_fields"]
    finally:
        db.close()


def test_importer_rejects_cross_kind_rows():
    db = _disposable_session()
    try:
        # concept importer must reject an indicator-kind row, and vice versa
        with pytest.raises(LocalizationError, match="unsupported subject_kind"):
            import_concept_translations(
                db,
                [_approved("indicator", "E1-1", "title", "es", "X", "Y")],
                known_subject_uris={"E1-1"},
            )
        with pytest.raises(LocalizationError, match="unsupported subject_kind"):
            import_indicator_translations(
                db,
                [_approved("concept", "c:1", "label", "es", "X", "Y")],
                known_subject_uris={"c:1"},
            )
    finally:
        db.close()


def test_indicator_response_omits_localization_when_unset():
    """HTTP contract: the response-only localization keys are ABSENT without ?lang, present with it."""
    from src.api.routers.indicators import IndicatorResponse

    r = IndicatorResponse(id="E1-1", identifier="E1-1", title="GHG", dimension="E")
    data = r.model_dump(mode="json")
    for key in (
        "display_title",
        "display_indicator_name",
        "display_description",
        "localization",
    ):
        assert key not in data  # byte-identical to pre-localization contract

    # when localization populates them, they appear; still-unset ones stay omitted
    r.display_title = "Emisiones de GEI"
    r.localization = {"status": "approved_current"}
    data2 = r.model_dump(mode="json")
    assert data2["display_title"] == "Emisiones de GEI"
    assert data2["localization"]["status"] == "approved_current"
    assert "display_indicator_name" not in data2


def test_indicator_exports_exclude_localization_fields():
    """CSV headers + JSON rows stay on the canonical export field set (codex LOC-3 M2)."""
    import json as _json

    from src.api.routers.indicators import (
        IndicatorResponse,
        _stream_indicator_csv,
        _stream_indicator_json,
    )

    r = IndicatorResponse(id="E1-1", identifier="E1-1", title="GHG", dimension="E")
    r.display_title = (
        "Emisiones de GEI"  # even when localized, exports must not include it
    )
    r.localization = {"status": "approved_current"}

    header = "".join(_stream_indicator_csv([r])).splitlines()[0]
    rows = _json.loads("".join(_stream_indicator_json([r])))
    for key in (
        "display_title",
        "display_indicator_name",
        "display_description",
        "localization",
    ):
        assert key not in header
        assert key not in rows[0]
    # canonical fields are still exported
    assert "identifier" in header and rows[0]["identifier"] == "E1-1"
