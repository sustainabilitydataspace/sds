"""F09 reconverge — RDF projection snapshot is gated to the public catalog (codex M1).

load_db_semantic_snapshots(public_catalog=True) must exclude internal/future concepts
so the generated public ontology projection (generated_projection.owl) never leaks
non-public concepts or their formulas. Gated on the disposable PG env vars.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.calculation.db_semantic_snapshot import load_db_semantic_snapshots
from src.database import models as m


def _disposable_session() -> Session:
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    from src.database.init_db import init_db_for_engine

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    init_db_for_engine(engine)
    return Session(bind=engine)


def _concept(uri, taxonomy, projection_source):
    return m.Concept(
        uri=uri,
        label=uri,
        taxonomy=taxonomy,
        concept_type="Indicator",
        projection_source=projection_source,
    )


def test_public_snapshot_excludes_internal_and_future_concepts():
    db = _disposable_session()
    try:
        db.add_all(
            [
                _concept("csrd:PUBLIC", "CSRD", "indicator_catalog"),  # public
                _concept("csrd:INTERNAL", "CSRD", None),  # non-catalog source
                _concept("issb:FUTURE", "ISSB", "indicator_catalog"),  # future taxonomy
            ]
        )
        db.commit()

        public = load_db_semantic_snapshots(db, public_catalog=True)
        assert "csrd:PUBLIC" in public
        assert "csrd:INTERNAL" not in public
        assert "issb:FUTURE" not in public

        # default (unfiltered) still loads everything (internal/admin use)
        all_snaps = load_db_semantic_snapshots(db)
        assert {"csrd:PUBLIC", "csrd:INTERNAL", "issb:FUTURE"} <= set(all_snaps)
    finally:
        db.close()
