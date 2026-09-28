"""VARCH-1b contract tests — bitemporal semantic vocabulary + atomization contracts.

Same three evidence layers as VARCH-1a (pytest mocks Alembic): metadata drift coverage,
migration-text coverage, and a disposable-DB smoke (skipped unless
SDS_MIGRATION_TEST_DATABASE_URL + SDS_MIGRATION_TEST_ALLOW_RESET=true).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, create_engine, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint

from src.database import models as _models  # noqa: F401 - register metadata
from src.database.base import Base

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_033 = (
    API_ROOT / "alembic" / "versions" / "033_add_semantic_vocabulary_layer.py"
)

MAIN_TABLES = (
    "semantic_axes",
    "semantic_terms",
    "semantic_term_relations",
    "partition_sets",
    "concept_atomization_contracts",
    "semantic_scope_assignments",
)
CHILD_TABLES = ("partition_set_members", "contract_required_axes")
ALL_TABLES = MAIN_TABLES + CHILD_TABLES


def _index(table_name: str, index_name: str):
    table = Base.metadata.tables[table_name]
    return next((idx for idx in table.indexes if idx.name == index_name), None)


def _has_exclude(table_name: str, name: str) -> bool:
    table = Base.metadata.tables[table_name]
    return any(
        isinstance(c, ExcludeConstraint) and c.name == name for c in table.constraints
    )


# --- Layer 1: metadata drift coverage -------------------------------------------------


def test_all_varch1b_tables_registered() -> None:
    for t in ALL_TABLES:
        assert t in Base.metadata.tables, t


def test_every_main_table_has_no_overlap_exclude() -> None:
    expected = {
        "semantic_axes": "ex_semantic_axes_no_overlap",
        "semantic_terms": "ex_semantic_terms_no_overlap",
        "semantic_term_relations": "ex_semantic_term_relations_no_overlap",
        "partition_sets": "ex_partition_sets_no_overlap",
        "concept_atomization_contracts": "ex_concept_atomization_contracts_no_overlap",
        "semantic_scope_assignments": "ex_semantic_scope_assignments_no_overlap",
    }
    for table, name in expected.items():
        assert _has_exclude(table, name), name


def test_every_main_table_has_version_chain_and_fork_guard() -> None:
    for t in MAIN_TABLES:
        cols = Base.metadata.tables[t].c
        assert "previous_version_id" in cols, t
        assert "superseded_by_version_id" in cols, t
        # partial-unique fork prevention on previous_version_id
        prev = next(
            (
                idx
                for idx in Base.metadata.tables[t].indexes
                if idx.name.endswith("_prev_version")
            ),
            None,
        )
        assert prev is not None and prev.unique is True, t


def test_member_shares_axis_via_composite_fks() -> None:
    members = Base.metadata.tables["partition_set_members"]
    fk_targets = {
        frozenset(col.name for col in c.columns): {
            fk.column.table.name for fk in c.elements
        }
        for c in members.constraints
        if isinstance(c, ForeignKeyConstraint)
    }
    assert fk_targets.get(frozenset({"partition_set_id", "axis_id"})) == {
        "partition_sets"
    }
    assert fk_targets.get(frozenset({"term_id", "axis_id"})) == {"semantic_terms"}
    # The composite FK targets require these composite UNIQUEs on the parents.
    for table, cols in (
        ("partition_sets", ("id", "axis_id")),
        ("semantic_terms", ("id", "axis_id")),
    ):
        parent = Base.metadata.tables[table]
        assert any(
            isinstance(c, UniqueConstraint)
            and tuple(col.name for col in c.columns) == cols
            for c in parent.constraints
        ), table


def test_scope_assignment_is_tenant_aware() -> None:
    cols = Base.metadata.tables["semantic_scope_assignments"].c
    assert cols["tenant_id"].nullable is False
    assert _has_exclude(
        "semantic_scope_assignments", "ex_semantic_scope_assignments_no_overlap"
    )


def test_contract_enums_and_resolver_index() -> None:
    checks = {
        c.name
        for c in Base.metadata.tables["concept_atomization_contracts"].constraints
        if c.name and c.name.startswith("ck_")
    }
    assert "ck_concept_atomization_contracts_subject_kind" in checks
    assert "ck_concept_atomization_contracts_formula_kind" in checks
    assert "ck_concept_atomization_contracts_aggregation" in checks
    idx = _index(
        "concept_atomization_contracts",
        "ix_concept_atomization_contracts_resolver",
    )
    assert idx is not None


# --- Layer 2: migration-text coverage -------------------------------------------------


def test_migration_033_declares_chain_and_guardrails() -> None:
    assert MIGRATION_033.exists()
    body = MIGRATION_033.read_text(encoding="utf-8")
    for snippet in [
        'revision = "033_add_semantic_vocabulary_layer"',
        'down_revision = "032_add_semantic_decision_backbone"',
        "CREATE EXTENSION IF NOT EXISTS btree_gist",
        "CREATE TABLE IF NOT EXISTS semantic_axes",
        "CREATE TABLE IF NOT EXISTS partition_set_members",
        "CREATE TABLE IF NOT EXISTS concept_atomization_contracts",
        "EXCLUDE USING gist",
        "fk_partition_set_members_partition_axis",
        "fk_partition_set_members_term_axis",
        "CREATE OR REPLACE FUNCTION sds_reject_versioned_mutation",
        "CREATE OR REPLACE FUNCTION sds_reject_child_mutation",
        "to_jsonb(OLD) - 'status' - 'superseded_by_version_id'",
    ]:
        assert snippet in body, snippet


def test_migration_033_downgrade_is_reverse_fk_order() -> None:
    body = MIGRATION_033.read_text(encoding="utf-8")
    downgrade = body.split("def downgrade", 1)[1]
    # The downgrade drops tables via a for-loop over a tuple; the tuple order IS the
    # reverse-FK drop order. Assert the quoted table names appear in that order.
    order = [
        downgrade.index('"semantic_scope_assignments"'),
        downgrade.index('"contract_required_axes"'),
        downgrade.index('"concept_atomization_contracts"'),
        downgrade.index('"partition_set_members"'),
        downgrade.index('"partition_sets"'),
        downgrade.index('"semantic_term_relations"'),
        downgrade.index('"semantic_terms"'),
        downgrade.index('"semantic_axes"'),
    ]
    assert order == sorted(order), "tables must drop in reverse FK dependency order"
    assert "DROP FUNCTION IF EXISTS sds_reject_versioned_mutation" in downgrade
    assert "DROP FUNCTION IF EXISTS sds_reject_child_mutation" in downgrade


# --- Layer 3: disposable-DB smoke (live guardrails) -----------------------------------


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
    init_db_for_engine(engine)
    return engine


def _seed_commit(connection) -> int:
    connection.execute(
        text(
            "INSERT INTO decision_commit_epochs (epoch_number, fence_token, status) "
            "VALUES (1, 'fence-1', 'active')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO decision_commit_fences (id, fence_token, epoch_number, status) "
            "VALUES ('f1', 'fence-1', 1, 'held')"
        )
    )
    return connection.execute(
        text(
            "INSERT INTO decision_commit_sequence (epoch_number, fence_token) "
            "VALUES (1, 'fence-1') RETURNING commit_id"
        )
    ).scalar_one()


def test_disposable_db_varch1b_guardrails() -> None:
    engine = _disposable_engine()
    try:
        from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

        db_errors = (IntegrityError, InternalError, ProgrammingError)

        with engine.begin() as connection:
            c = _seed_commit(connection)
            connection.execute(
                text(
                    "INSERT INTO semantic_axes "
                    "(id, axis_key, axis_version, decision_commit_id, valid_from, "
                    "valid_to) VALUES "
                    "('a1', 'k1', 1, :c, '2026-01-01Z', '2026-06-01Z')"
                ),
                {"c": c},
            )

        # No-overlap: a second published axis row for k1 overlapping in valid time.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                c = connection.execute(
                    text("SELECT min(commit_id) FROM decision_commit_sequence")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO semantic_axes "
                        "(id, axis_key, axis_version, decision_commit_id, valid_from, "
                        "valid_to) VALUES "
                        "('a2', 'k1', 2, :c, '2026-03-01Z', '2026-09-01Z')"
                    ),
                    {"c": c},
                )

        # Append-only: DELETE of a published axis is rejected by the trigger.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM semantic_axes WHERE id = 'a1'"))

        # Fork prevention: two axes claiming the same previous_version_id.
        with engine.begin() as connection:
            c = connection.execute(
                text("SELECT min(commit_id) FROM decision_commit_sequence")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO semantic_axes "
                    "(id, axis_key, axis_version, previous_version_id, "
                    "decision_commit_id, valid_from) VALUES "
                    "('a3', 'k3', 1, 'a1', :c, '2026-01-01Z')"
                ),
                {"c": c},
            )
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                c = connection.execute(
                    text("SELECT min(commit_id) FROM decision_commit_sequence")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO semantic_axes "
                        "(id, axis_key, axis_version, previous_version_id, "
                        "decision_commit_id, valid_from) VALUES "
                        "('a4', 'k4', 1, 'a1', :c, '2026-01-01Z')"
                    ),
                    {"c": c},
                )

        # Member-shares-axis: a member whose axis does not match its partition.
        with engine.begin() as connection:
            c = connection.execute(
                text("SELECT min(commit_id) FROM decision_commit_sequence")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO semantic_axes "
                    "(id, axis_key, axis_version, decision_commit_id, valid_from) "
                    "VALUES ('ax', 'kx', 1, :c, '2026-01-01Z')"
                ),
                {"c": c},
            )
            connection.execute(
                text(
                    "INSERT INTO semantic_terms "
                    "(id, axis_id, term_key, term_version, decision_commit_id, "
                    "valid_from) VALUES ('t1', 'ax', 'tk1', 1, :c, '2026-01-01Z')"
                ),
                {"c": c},
            )
            connection.execute(
                text(
                    "INSERT INTO partition_sets "
                    "(id, partition_key, axis_id, partition_version, "
                    "decision_commit_id, valid_from) VALUES "
                    "('p1', 'pk1', 'ax', 1, :c, '2026-01-01Z')"
                ),
                {"c": c},
            )
        # term t1 belongs to axis 'ax'; partition p1 is on 'ax'; using axis 'a1' must fail.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO partition_set_members "
                        "(id, partition_set_id, axis_id, term_id) VALUES "
                        "('m_bad', 'p1', 'a1', 't1')"
                    )
                )
        # Correct axis succeeds.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO partition_set_members "
                    "(id, partition_set_id, axis_id, term_id) VALUES "
                    "('m_ok', 'p1', 'ax', 't1')"
                )
            )
    finally:
        engine.dispose()
