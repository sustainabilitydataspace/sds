"""VARCH-1d contract tests — profile / manifest / schema registry tables.

Three evidence layers (pytest mocks Alembic): metadata drift, migration-text, and a
disposable-DB smoke (skipped unless SDS_MIGRATION_TEST_DATABASE_URL +
SDS_MIGRATION_TEST_ALLOW_RESET=true).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import UniqueConstraint, create_engine, text

from src.database import models as _models  # noqa: F401 - register metadata
from src.database.base import Base

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_035 = API_ROOT / "alembic" / "versions" / "035_add_profile_registry_tables.py"

MAIN_TABLES = (
    "canonical_hash_profiles",
    "computation_profiles",
    "semantic_replay_manifests",
    "contract_schema_versions",
)
FIXTURE_TABLES = (
    "canonical_hash_profile_fixtures",
    "computation_profile_fixtures",
    "semantic_replay_manifest_fixtures",
)
ALL_TABLES = MAIN_TABLES + FIXTURE_TABLES


def _unique_cols(table_name: str):
    table = Base.metadata.tables[table_name]
    return {
        tuple(c.name for c in con.columns)
        for con in table.constraints
        if isinstance(con, UniqueConstraint)
    }


# --- Layer 1: metadata ----------------------------------------------------------------


def test_all_varch1d_tables_registered() -> None:
    for t in ALL_TABLES:
        assert t in Base.metadata.tables, t


def test_registry_is_not_bitemporal_and_version_is_varchar() -> None:
    for t in MAIN_TABLES:
        cols = Base.metadata.tables[t].c
        # Registry shape: NO valid/decision bitemporal columns.
        assert "valid_from" not in cols and "decision_commit_id" not in cols, t
        assert "version" in cols
        assert cols["version"].type.python_type is str, t


def test_content_addressable_hash_is_notnull_and_unique() -> None:
    hash_cols = {
        "canonical_hash_profiles": "profile_hash",
        "computation_profiles": "profile_hash",
        "semantic_replay_manifests": "manifest_hash",
        "contract_schema_versions": "schema_hash",
    }
    for table, col in hash_cols.items():
        c = Base.metadata.tables[table].c[col]
        assert c.nullable is False, table
        assert (col,) in _unique_cols(table), table


def test_id_version_unique_and_kind_status_checks() -> None:
    assert ("profile_id", "version") in _unique_cols("canonical_hash_profiles")
    assert ("manifest_id", "version") in _unique_cols("semantic_replay_manifests")
    assert ("schema_id", "version") in _unique_cols("contract_schema_versions")
    checks = {
        c.name
        for c in Base.metadata.tables["canonical_hash_profiles"].constraints
        if c.name and c.name.startswith("ck_")
    }
    assert "ck_canonical_hash_profiles_kind" in checks
    assert "ck_canonical_hash_profiles_status" in checks


# --- Layer 2: migration-text ----------------------------------------------------------


def test_migration_035_text() -> None:
    assert MIGRATION_035.exists()
    body = MIGRATION_035.read_text(encoding="utf-8")
    for snippet in [
        'revision = "035_add_profile_registry_tables"',
        'down_revision = "034_add_value_dimensions_and_bindings"',
        "CREATE OR REPLACE FUNCTION sds_reject_profile_mutation",
        "active->deprecated",
        "FOR EACH ROW EXECUTE FUNCTION sds_reject_profile_mutation()",
        "FOR EACH ROW EXECUTE FUNCTION sds_reject_child_mutation()",
        "version VARCHAR NOT NULL",
        "deprecation must set deprecated_at",
        "uq_{name}_hash UNIQUE (fixture_hash)",
    ]:
        assert snippet in body, snippet


def test_fixture_tables_are_content_addressable() -> None:
    for t in FIXTURE_TABLES:
        assert ("fixture_hash",) in _unique_cols(t), t


def test_migration_035_downgrade_drops_profile_fn_not_child_fn() -> None:
    body = MIGRATION_035.read_text(encoding="utf-8")
    downgrade = body.split("def downgrade", 1)[1]
    assert "DROP FUNCTION IF EXISTS sds_reject_profile_mutation" in downgrade
    # The shared 033 child function must NOT be dropped here.
    assert "DROP FUNCTION IF EXISTS sds_reject_child_mutation" not in downgrade


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


_H = "a" * 64


def test_disposable_db_varch1d_append_only() -> None:
    engine = _disposable_engine()
    try:
        from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

        db_errors = (IntegrityError, InternalError, ProgrammingError)

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO canonical_hash_profiles "
                    "(id, profile_id, version, kind, descriptor, profile_hash) VALUES "
                    # synthetic id: must not collide with the VARCH-3 seed
                    # (sds-canonical-json-v1), which init_db now populates.
                    "('p1', 'sds-test-1d-canonical', 'v1', 'implemented', '{}', :h)"
                ),
                {"h": _H},
            )

        # active -> deprecated is allowed.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE canonical_hash_profiles SET status = 'deprecated', "
                    "deprecated_at = now() WHERE id = 'p1'"
                )
            )

        # Mutating an immutable column is rejected (and the row is now deprecated anyway).
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE canonical_hash_profiles SET profile_id = 'x' "
                        "WHERE id = 'p1'"
                    )
                )

        # DELETE is forbidden.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM canonical_hash_profiles WHERE id = 'p1'")
                )

        # Hash uniqueness (content-addressable) is enforced.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO computation_profiles "
                        "(id, profile_id, version, kind, descriptor, profile_hash) "
                        "VALUES ('c1', 'x', 'v1', 'implemented', '{}', :h), "
                        "('c2', 'y', 'v1', 'implemented', '{}', :h)"
                    ),
                    {"h": "b" * 64},
                )
    finally:
        engine.dispose()
