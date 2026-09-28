"""VARCH-1c contract tests — value dimensions + bindings.

Three evidence layers (pytest mocks Alembic): metadata drift, migration-text, and a
disposable-DB smoke (skipped unless SDS_MIGRATION_TEST_DATABASE_URL +
SDS_MIGRATION_TEST_ALLOW_RESET=true).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import ForeignKeyConstraint, create_engine, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint

from src.database import models as _models  # noqa: F401 - register metadata
from src.database.base import Base

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_034 = (
    API_ROOT / "alembic" / "versions" / "034_add_value_dimensions_and_bindings.py"
)

NEW_TABLES = (
    "value_context_dimensions",
    "sygris_variable_bindings",
    "mapping_component_bindings",
    "measurement_basis_contracts",
)


def _has_exclude(table_name: str, name: str) -> bool:
    table = Base.metadata.tables[table_name]
    return any(
        isinstance(c, ExcludeConstraint) and c.name == name for c in table.constraints
    )


# --- Layer 1: metadata drift coverage -------------------------------------------------


def test_all_varch1c_tables_registered() -> None:
    for t in NEW_TABLES:
        assert t in Base.metadata.tables, t


def test_every_table_has_no_overlap_and_version_chain() -> None:
    expected_exclude = {
        "value_context_dimensions": "ex_value_context_dimensions_no_overlap",
        "sygris_variable_bindings": "ex_sygris_variable_bindings_no_overlap",
        "mapping_component_bindings": "ex_mapping_component_bindings_no_overlap",
        "measurement_basis_contracts": "ex_measurement_basis_contracts_no_overlap",
    }
    for table, name in expected_exclude.items():
        assert _has_exclude(table, name), name
        cols = Base.metadata.tables[table].c
        assert "previous_version_id" in cols and "superseded_by_version_id" in cols
        prev = next(
            (
                idx
                for idx in Base.metadata.tables[table].indexes
                if idx.name.endswith("_prev_version")
            ),
            None,
        )
        assert prev is not None and prev.unique is True, table


def test_value_context_dimension_fk_and_member_shares_axis() -> None:
    table = Base.metadata.tables["value_context_dimensions"]
    # Real FK to the existing value_contexts(id).
    assert any(
        fk.column.table.name == "value_contexts"
        and fk.parent.name == "value_context_id"
        for fk in table.foreign_keys
    )
    # Composite member-shares-axis FK to semantic_terms(id, axis_id).
    composite = [
        c
        for c in table.constraints
        if isinstance(c, ForeignKeyConstraint)
        and {col.name for col in c.columns} == {"term_id", "axis_id"}
    ]
    assert composite and {fk.column.table.name for fk in composite[0].elements} == {
        "semantic_terms"
    }


def test_component_ref_is_integer_loose_ref() -> None:
    col = Base.metadata.tables["mapping_component_bindings"].c["component_ref"]
    assert col.type.python_type is int
    # No FK on component_ref (mapping import path is in flux).
    assert not col.foreign_keys


def test_enum_checks_declared() -> None:
    def checks(t):
        return {
            c.name
            for c in Base.metadata.tables[t].constraints
            if c.name and c.name.startswith("ck_")
        }

    assert "ck_sygris_variable_bindings_target_kind" in checks(
        "sygris_variable_bindings"
    )
    assert "ck_sygris_variable_bindings_projection_flag" in checks(
        "sygris_variable_bindings"
    )
    assert "ck_measurement_basis_contracts_kind" in checks(
        "measurement_basis_contracts"
    )


# --- Layer 2: migration-text coverage -------------------------------------------------


def test_migration_034_text() -> None:
    assert MIGRATION_034.exists()
    body = MIGRATION_034.read_text(encoding="utf-8")
    for snippet in [
        'revision = "034_add_value_dimensions_and_bindings"',
        'down_revision = "033_add_semantic_vocabulary_layer"',
        "CREATE TABLE IF NOT EXISTS value_context_dimensions",
        "REFERENCES value_contexts (id)",
        "fk_value_context_dimensions_term_axis",
        "EXCLUDE USING gist",
        "FOR EACH ROW EXECUTE FUNCTION sds_reject_versioned_mutation()",
    ]:
        assert snippet in body, snippet


def test_migration_034_downgrade_does_not_drop_shared_functions() -> None:
    body = MIGRATION_034.read_text(encoding="utf-8")
    downgrade = body.split("def downgrade", 1)[1]
    # 034 must NOT drop the shared 033 trigger functions.
    assert "DROP FUNCTION" not in downgrade
    # Downgrade drops the new tables via a for-loop over _NEW_TABLES (f-string form).
    assert "DROP TABLE IF EXISTS {table}" in downgrade
    # All four new tables are listed in the _NEW_TABLES tuple driving the loop.
    for table in NEW_TABLES:
        assert f'"{table}"' in body


# --- Layer 3: disposable-DB smoke -----------------------------------------------------


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


def test_disposable_db_varch1c_guardrails() -> None:
    engine = _disposable_engine()
    try:
        from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

        db_errors = (IntegrityError, InternalError, ProgrammingError)

        # measurement_basis enum CHECK rejects an unknown basis_kind.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO decision_commit_epochs "
                    "(epoch_number, fence_token, status) VALUES (1, 'f', 'active')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO decision_commit_fences "
                    "(id, fence_token, epoch_number, status) VALUES "
                    "('f1', 'f', 1, 'held')"
                )
            )
            c = connection.execute(
                text(
                    "INSERT INTO decision_commit_sequence (epoch_number, fence_token) "
                    "VALUES (1, 'f') RETURNING commit_id"
                )
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO measurement_basis_contracts "
                    "(id, basis_key, basis_kind, basis_version, decision_commit_id, "
                    "valid_from) VALUES "
                    "('mb1', 'bk', 'gross_net', 1, :c, '2026-01-01Z')"
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
                        "INSERT INTO measurement_basis_contracts "
                        "(id, basis_key, basis_kind, basis_version, decision_commit_id, "
                        "valid_from) VALUES "
                        "('mbX', 'bk2', 'not_a_basis', 1, :c, '2026-01-01Z')"
                    ),
                    {"c": c},
                )

        # No-overlap on measurement basis per basis_key.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                c = connection.execute(
                    text("SELECT min(commit_id) FROM decision_commit_sequence")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO measurement_basis_contracts "
                        "(id, basis_key, basis_kind, basis_version, decision_commit_id, "
                        "valid_from, valid_to) VALUES "
                        "('mb2', 'bk', 'gross_net', 2, :c, '2026-01-01Z', '2027-01-01Z')"
                    ),
                    {"c": c},
                )

        # Append-only: DELETE of a published basis row is rejected by the shared trigger.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM measurement_basis_contracts WHERE id = 'mb1'")
                )
    finally:
        engine.dispose()
