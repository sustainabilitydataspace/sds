"""F04 reconverge — public single-indicator lookup must be active-aware (codex M1).

GET /api/v1/indicators/{id} previously used a primary-key lookup with no is_active
predicate, so a retired indicator stayed directly retrievable by id even though
list/search filter is_active. The repository now exposes
get_active_by_id_or_identifier (active-only, by id OR URN) which the public endpoint
uses. Gated on the disposable PG env vars.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.database import models as m
from src.database.repositories.indicator_repository import IndicatorRepository


def _disposable_session() -> Session:
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    from sqlalchemy import text

    from src.database.init_db import init_db_for_engine

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    init_db_for_engine(engine)
    return Session(bind=engine)


def _seed(db: Session, *, ind_id: str, identifier: str, active: bool) -> None:
    db.add(
        m.Indicator(
            id=ind_id,
            identifier=identifier,
            title=f"Indicator {ind_id}",
            dimension="ENVIRONMENTAL",
            is_active=active,
        )
    )


def test_active_lookup_returns_active_by_id_and_identifier():
    db = _disposable_session()
    try:
        _seed(db, ind_id="urn:sds:reg:1", identifier="urn:sds:reg:1", active=True)
        db.commit()
        repo = IndicatorRepository(db)
        assert repo.get_active_by_id_or_identifier("urn:sds:reg:1") is not None
    finally:
        db.close()


def test_active_lookup_hides_retired_indicator_by_id_and_identifier():
    db = _disposable_session()
    try:
        _seed(db, ind_id="urn:sds:reg:9", identifier="urn:sds:reg:9", active=False)
        db.commit()
        repo = IndicatorRepository(db)
        # retired indicator must not be retrievable on the public path by id OR urn
        assert repo.get_active_by_id_or_identifier("urn:sds:reg:9") is None
        # but the any-state internal lookup still finds it
        assert repo.get_by_identifier_any_state("urn:sds:reg:9") is not None
        assert repo.get_by_id("urn:sds:reg:9") is not None
    finally:
        db.close()
