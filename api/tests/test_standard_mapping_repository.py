"""Tests for standard mapping repository behaviors."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import column, create_engine, select, table, text
from sqlalchemy.dialects.postgresql import JSONB, dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from src.database.models import MaterializedPairwiseMapping, StandardMapping
from src.database.repositories import standard_mapping_repository as repo_module
from src.database.repositories.standard_mapping_repository import (
    CanonicalPairwiseMappingRepository,
    MappingCandidateLimitExceeded,
    StandardMappingRepository,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(element, compiler, **kwargs):
    return "JSON"


def test_standard_sql_strip_characters_cover_python_whitespace():
    python_whitespace = {
        chr(codepoint) for codepoint in range(0x110000) if chr(codepoint).isspace()
    }
    assert (
        set(getattr(repo_module, "_STANDARD_STRIP_WHITESPACE", "")) == python_whitespace
    )


def _setup_query(db: MagicMock) -> MagicMock:
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    return query


def test_get_all_builds_query():
    db = MagicMock()
    query = _setup_query(db)
    query.all.return_value = []

    repo = StandardMappingRepository(db)
    result = repo.get_all(limit=25, offset=5)

    assert result == []
    query.filter.assert_called()
    query.offset.assert_called_with(5)
    query.limit.assert_called_with(25)


def test_get_all_accepts_changed_since_filter():
    db = MagicMock()
    query = _setup_query(db)
    query.all.return_value = []
    changed_since = datetime.now(timezone.utc)

    repo = StandardMappingRepository(db)
    assert repo.get_all(changed_since=changed_since) == []
    assert query.filter.call_count >= 2


def test_get_all_inactive_included():
    db = MagicMock()
    query = _setup_query(db)
    query.all.return_value = []

    repo = StandardMappingRepository(db)
    repo.get_all(active_only=False)

    query.filter.assert_not_called()


def test_count_active_only():
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.count.return_value = 42

    repo = StandardMappingRepository(db)
    result = repo.count(active_only=True)

    assert result == 42
    query.filter.assert_called()


def test_find_by_source():
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = ["m1", "m2"]

    repo = StandardMappingRepository(db)
    result = repo.find_by_source("ESRS", "E1", limit=10)

    assert result == ["m1", "m2"]
    query.limit.assert_called_with(10)


def test_find_by_target():
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = ["m1"]

    repo = StandardMappingRepository(db)
    result = repo.find_by_target("GRI", "305", limit=5)

    assert result == ["m1"]
    query.limit.assert_called_with(5)


def test_find_between_standards():
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = ["m1"]

    repo = StandardMappingRepository(db)
    result = repo.find_between_standards("ESRS", "GRI", limit=20)

    assert result == ["m1"]
    query.limit.assert_called_with(20)


def test_search_filters():
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = []

    repo = StandardMappingRepository(db)
    result = repo.search(
        source_standard="ESRS",
        source_code="E1",
        target_standard="GRI",
        target_code="305",
        dimension="E",
        min_confidence=0.8,
        limit=15,
    )

    assert result == []
    assert query.filter.call_count >= 1
    query.limit.assert_called_with(15)


def test_search_accepts_changed_since_filter():
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = []

    repo = StandardMappingRepository(db)
    repo.search(changed_since=datetime.now(timezone.utc))

    query.filter.assert_called()


def test_get_supported_standards_unions_sources_and_targets():
    db = MagicMock()
    source_query = MagicMock()
    target_query = MagicMock()
    db.query.side_effect = [source_query, target_query]

    source_query.distinct.return_value = source_query
    target_query.distinct.return_value = target_query
    source_query.all.return_value = [("ESRS",), ("GRI",)]
    target_query.all.return_value = [("GRI",), ("ISSB",)]

    repo = StandardMappingRepository(db)
    result = repo.get_supported_standards()

    assert result == ["ESRS", "GRI", "ISSB"]


def test_bulk_upsert_can_deactivate_rows_missing_from_authoritative_snapshot():
    engine = create_engine("sqlite:///:memory:")
    StandardMapping.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    repo = StandardMappingRepository(db)

    repo.bulk_upsert(
        [
            {
                "source_standard": "ESRS",
                "source_code": "S1-7_08",
                "target_standard": "GRI",
                "target_code": "GRI 2-8.b-ii",
                "relationship_type": "equivalent",
                "confidence": 1.0,
                "is_active": True,
            }
        ]
    )
    repo.bulk_upsert(
        [
            {
                "source_standard": "ESRS",
                "source_code": "S1-7_08",
                "target_standard": "GRI",
                "target_code": "GRI 2-8.b.ii",
                "relationship_type": "equivalent",
                "confidence": 1.0,
                "is_active": True,
            }
        ],
        deactivate_missing=True,
    )

    active_rows = db.query(StandardMapping).filter_by(is_active=True).all()
    inactive_rows = db.query(StandardMapping).filter_by(is_active=False).all()

    assert [row.target_code for row in active_rows] == ["GRI 2-8.b.ii"]
    assert [row.target_code for row in inactive_rows] == ["GRI 2-8.b-ii"]


def test_legacy_mapping_code_lookup_matches_prefixed_public_codes():
    engine = create_engine("sqlite:///:memory:")
    StandardMapping.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    repo = StandardMappingRepository(db)

    repo.bulk_upsert(
        [
            {
                "source_standard": "ESRS",
                "source_code": "E3-4_05",
                "target_standard": "GRI",
                "target_code": "GRI 303-5.c",
                "relationship_type": "equivalent",
                "confidence": 1.0,
                "is_active": True,
            }
        ]
    )

    rows = repo.search(
        source_standard="ESRS",
        source_code="E3-4_05",
        target_standard="GRI",
        target_code="303-5.c",
    )

    assert len(rows) == 1
    assert rows[0].target_code == "GRI 303-5.c"
    assert rows[0].relationship_type == "equivalent"


def test_standard_mapping_repository_changed_since_create_delete_and_rollback():
    db = MagicMock()
    query = _setup_query(db)
    query.count.return_value = 3
    query.first.side_effect = [SimpleNamespace(id=1, is_active=True), None]
    repo = StandardMappingRepository(db)
    changed_since = datetime.now(timezone.utc)

    assert repo.count(changed_since=changed_since) == 3
    assert repo.get_by_id(1).id == 1

    created = repo.create(
        {
            "source_standard": "ESRS",
            "source_code": "E1",
            "target_standard": "GRI",
            "target_code": "305",
        }
    )
    assert created.source_standard == "ESRS"
    db.add.assert_called_with(created)

    assert repo.delete(1) is False

    db.query.side_effect = RuntimeError("db down")
    with pytest.raises(RuntimeError):
        repo.bulk_upsert(
            [
                {
                    "source_standard": "ESRS",
                    "source_code": "E1",
                    "target_standard": "GRI",
                }
            ]
        )
    db.rollback.assert_called()


def test_canonical_pairwise_mapping_repository_maps_and_filters_rows():
    source_dp = SimpleNamespace(label="ESRS label")
    target_dp = SimpleNamespace(label="GRI label")
    generated = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = SimpleNamespace(
        id=10,
        source_standard="ESRS",
        source_code="E1-1",
        target_standard="GRI",
        target_code="305-1",
        relationship_type="equivalent",
        match_strength=0.92,
        metadata_json={"dimension": "E"},
        generated_at=generated,
        source_datapoint=source_dp,
        target_datapoint=target_dp,
    )
    query = MagicMock()
    query.options.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = [row]
    query.count.return_value = 1
    query.with_entities.return_value = query
    query.distinct.return_value = query
    db = MagicMock()
    db.query.return_value = query
    repo = CanonicalPairwiseMappingRepository(db)

    mapped = repo.get_all(limit=1, offset=0, changed_since=generated)
    assert mapped[0].source_label == "ESRS label"
    assert mapped[0].target_label == "GRI label"
    assert mapped[0].esg_dimension == "E"
    assert mapped[0].confidence == 0.92
    assert mapped[0].dataset == "canonical_pairwise_mappings"

    assert repo.count(changed_since=generated) == 1
    assert repo.find_by_source("ESRS", "E1")[0].id == 10
    assert repo.find_by_target("GRI", "305")[0].id == 10
    assert repo.find_between_standards("ESRS", "GRI")[0].id == 10
    assert (
        repo.search(
            source_standard="ESRS",
            source_code="E1",
            target_standard="GRI",
            target_code="305",
            dimension="E",
            min_confidence=0.8,
            changed_since=generated,
        )[0].id
        == 10
    )

    query.all.side_effect = [[("ESRS",), ("GRI",)], [("GRI",), ("ISSB",)]]
    assert repo.get_supported_standards() == ["ESRS", "GRI", "ISSB"]


def test_canonical_pairwise_search_filters_dimension_before_limit():
    query = MagicMock()
    events: list[str] = []

    def _record(event: str):
        def _inner(*_args, **_kwargs):
            events.append(event)
            return query

        return _inner

    query.filter.side_effect = _record("filter")
    query.order_by.side_effect = _record("order_by")
    query.limit.side_effect = _record("limit")
    query.all.return_value = []

    repo = CanonicalPairwiseMappingRepository(MagicMock())
    repo._current_query = MagicMock(return_value=query)

    assert repo.search(dimension="E", limit=1) == []
    assert events[:3] == ["filter", "order_by", "limit"]


def test_canonical_pairwise_candidate_routes_use_one_bounded_query():
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = []

    repo = CanonicalPairwiseMappingRepository(MagicMock())
    repo._current_query = MagicMock(return_value=query)

    assert (
        repo.find_candidate_routes(
            source_standards=["ESRS"],
            source_codes=["E3_5"],
            target_standards=["GRI"],
            target_codes=["303_3"],
            limit=17,
        )
        == []
    )

    query.filter.assert_called_once()
    query.limit.assert_called_once_with(18)


def test_canonical_pairwise_candidate_filter_matches_separator_aliases_in_sql():
    expression = CanonicalPairwiseMappingRepository._code_prefix_filter(
        MaterializedPairwiseMapping.target_code,
        ["302-1.e:fossil_component"],
    )
    sql = str(
        expression.compile(dialect=dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "regexp_replace" in sql
    assert "3021efossilcomponent%" in sql
    assert "302-1.e:fossil_component%" not in sql


def test_canonical_pairwise_candidate_filter_executes_separator_aliases_in_postgres():
    """The production SQL must not discard contradictory authority before review."""
    url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not url or os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("requires explicitly disposable PostgreSQL")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or not parsed.database.startswith(
        "sds_disposable_"
    ):
        pytest.skip("requires a dedicated disposable PostgreSQL database")

    mapping_probe = table(
        "mapping_alias_probe", column("target_standard"), column("target_code")
    )
    expression = CanonicalPairwiseMappingRepository._code_prefix_filter(
        mapping_probe.c.target_code,
        ["302-1.e:fossil_component"],
    )
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TEMP TABLE mapping_alias_probe (target_standard text, target_code text)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO mapping_alias_probe (target_standard, target_code) VALUES (:standard, :code)"
                ),
                [
                    {"standard": "GRI", "code": "302-1.e:fossil_component"},
                    {"standard": "gri", "code": "302 1.e:fossil_component"},
                    {"standard": "gri\t", "code": "302 1.e:fossil_component"},
                    {"standard": "\u00a0gri\u00a0", "code": "302 1.e:fossil_component"},
                    {"standard": "GRI", "code": "302-1.e:other_component"},
                ],
            )
            complete_side = CanonicalPairwiseMappingRepository._side_filter(
                standard_column=mapping_probe.c.target_standard,
                code_column=mapping_probe.c.target_code,
                standards=["GRI"],
                codes=["302-1.e:fossil_component"],
            )
            matches = (
                connection.execute(
                    select(mapping_probe.c.target_code)
                    .where(expression, complete_side)
                    .order_by(mapping_probe.c.target_code)
                )
                .scalars()
                .all()
            )
            assert matches == [
                "302 1.e:fossil_component",
                "302 1.e:fossil_component",
                "302 1.e:fossil_component",
                "302-1.e:fossil_component",
            ]
            connection.execute(
                text(
                    "INSERT INTO mapping_alias_probe (target_standard, target_code) "
                    "VALUES (:standard, :code)"
                ),
                [
                    {"standard": "G-R-I", "code": "302-1.e:fossil_component"}
                    for _ in range(500)
                ],
            )
            bounded = (
                connection.execute(
                    select(mapping_probe.c.target_standard)
                    .where(expression, complete_side)
                    .limit(501)
                )
                .scalars()
                .all()
            )
            assert sorted(bounded) == ["GRI", "gri", "gri\t", "\u00a0gri\u00a0"]
    finally:
        engine.dispose()


def test_canonical_pairwise_candidate_routes_fail_closed_on_truncation():
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = [SimpleNamespace(id=index) for index in range(18)]

    repo = CanonicalPairwiseMappingRepository(MagicMock())
    repo._current_query = MagicMock(return_value=query)

    with pytest.raises(MappingCandidateLimitExceeded, match="17"):
        repo.find_candidate_routes(
            target_standards=["GRI"], target_codes=["303_3"], limit=17
        )

    query.limit.assert_called_once_with(18)
