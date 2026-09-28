"""VARCH-8d contract tests — SemanticCatalogRepository (disposable-PG).

The DB-facing half of /api/v1/semantic-dimensions: published-axis listing for a bitemporal slice.
No app/auth import (so it runs in this env, unlike the TestClient tests). Gated on the disposable
PG env vars.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.services.semantic_catalog import SemanticCatalogRepository

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
T_READ = datetime(2026, 1, 1, tzinfo=timezone.utc)
T_BEFORE = datetime(2023, 1, 1, tzinfo=timezone.utc)


def _disposable_engine():
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
    init_db_for_engine(engine)  # migrations to head, incl 039 genesis decision commit
    return engine


def _genesis_commit(conn) -> int:
    return conn.execute(
        text(
            "SELECT commit_id FROM decision_commit_sequence ORDER BY commit_id LIMIT 1"
        )
    ).scalar_one()


def _insert_axis(
    conn, axis_id, axis_key, commit_id, valid_from, valid_to=None, status="published"
):
    conn.execute(
        text(
            "INSERT INTO semantic_axes (id, axis_key, label, axis_version, valid_from, "
            "valid_to, status, decision_commit_id) VALUES "
            "(:id, :key, :label, 1, :vf, :vt, :st, :cid)"
        ),
        {
            "id": axis_id,
            "key": axis_key,
            "label": f"Axis {axis_key}",
            "vf": valid_from,
            "vt": valid_to,
            "st": status,
            "cid": commit_id,
        },
    )


def test_catalog_lists_published_axes_for_slice() -> None:
    engine = _disposable_engine()
    try:
        with engine.begin() as conn:
            cid = _genesis_commit(conn)
            _insert_axis(conn, "ax-fuel", "fuel", cid, T0)
            _insert_axis(conn, "ax-scope", "scope", cid, T0)
            # a superseded axis must NOT appear; a future-valid axis must NOT appear at T_READ.
            _insert_axis(
                conn,
                "ax-old",
                "zzz_old",
                cid,
                T0,
                valid_to=T0 + timedelta(days=1),
                status="superseded",
            )

        with Session(engine) as session:
            repo = SemanticCatalogRepository(session)
            # the migration-039 seed may add its own axes, so assert RELATIVE to my inserts.
            items = repo.list_published_axes(
                valid_as_of=T_READ, decision_commit_id=cid, limit=1000, offset=0
            )
            keys = [i["axis_key"] for i in items]
            assert "fuel" in keys and "scope" in keys
            assert "zzz_old" not in keys  # superseded excluded
            assert keys == sorted(keys)  # ordered by axis_key
            assert repo.count_published_axes(
                valid_as_of=T_READ, decision_commit_id=cid
            ) == len(items)

            # a read BEFORE my axes were valid does not see THEM (valid_from = 2024).
            before_keys = [
                i["axis_key"]
                for i in repo.list_published_axes(
                    valid_as_of=T_BEFORE, decision_commit_id=cid, limit=1000, offset=0
                )
            ]
            assert "fuel" not in before_keys and "scope" not in before_keys
    finally:
        engine.dispose()


def test_catalog_pagination() -> None:
    engine = _disposable_engine()
    try:
        with engine.begin() as conn:
            cid = _genesis_commit(conn)
            for i in range(5):
                _insert_axis(conn, f"ax-{i}", f"k{i}", cid, T0)
        with Session(engine) as session:
            repo = SemanticCatalogRepository(session)
            # seed-robust: offset/limit must slice the full ordered list consistently.
            full = [
                i["axis_key"]
                for i in repo.list_published_axes(
                    valid_as_of=T_READ, decision_commit_id=cid, limit=1000, offset=0
                )
            ]
            page1 = [
                i["axis_key"]
                for i in repo.list_published_axes(
                    valid_as_of=T_READ, decision_commit_id=cid, limit=2, offset=0
                )
            ]
            page2 = [
                i["axis_key"]
                for i in repo.list_published_axes(
                    valid_as_of=T_READ, decision_commit_id=cid, limit=2, offset=2
                )
            ]
            assert page1 == full[:2]
            assert page2 == full[2:4]
            assert set(page1).isdisjoint(page2)
    finally:
        engine.dispose()


def test_catalog_filters_axes_by_evs_allowed_axis_keys() -> None:
    engine = _disposable_engine()
    try:
        with engine.begin() as conn:
            cid = _genesis_commit(conn)
            _insert_axis(conn, "ax-outside", "outside_axis", cid, T0)
            # Migration 039 seeds waste_treatment_type; use a test-local key so
            # this fixture remains valid against the real migration head.
            _insert_axis(conn, "ax-treatment", "test_treatment_type", cid, T0)

        with Session(engine) as session:
            repo = SemanticCatalogRepository(session)
            items = repo.list_published_axes(
                valid_as_of=T_READ,
                decision_commit_id=cid,
                axis_keys={"test_treatment_type"},
                limit=1000,
                offset=0,
            )

            assert [item["axis_key"] for item in items] == ["test_treatment_type"]
            assert (
                repo.count_published_axes(
                    valid_as_of=T_READ,
                    decision_commit_id=cid,
                    axis_keys={"test_treatment_type"},
                )
                == 1
            )
    finally:
        engine.dispose()
