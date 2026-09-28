"""VARCH-1e contract tests — factors + consolidation.

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
MIGRATION_036 = (
    API_ROOT / "alembic" / "versions" / "036_add_factors_and_consolidation.py"
)

VERSIONED_TABLES = (
    "semantic_factor_sets",
    "semantic_factor_values",
    "factor_vintage_selection_policies",
    "semantic_consolidation_groups",
    "semantic_consolidation_consents",
    "consolidation_composition_profiles",
    "consolidation_composed_decisions",
)
CHILD_TABLES = ("semantic_consolidation_group_members",)
NEW_TABLES = VERSIONED_TABLES + CHILD_TABLES


def _has_exclude(table_name: str, name: str) -> bool:
    table = Base.metadata.tables[table_name]
    return any(
        isinstance(c, ExcludeConstraint) and c.name == name for c in table.constraints
    )


# --- Layer 1: metadata drift coverage -------------------------------------------------


def test_all_varch1e_tables_registered() -> None:
    for t in NEW_TABLES:
        assert t in Base.metadata.tables, t


def test_every_versioned_table_has_no_overlap_and_version_chain() -> None:
    expected_exclude = {
        "semantic_factor_sets": "ex_semantic_factor_sets_no_overlap",
        "semantic_factor_values": "ex_semantic_factor_values_no_overlap",
        "factor_vintage_selection_policies": (
            "ex_factor_vintage_selection_policies_no_overlap"
        ),
        "semantic_consolidation_groups": (
            "ex_semantic_consolidation_groups_no_overlap"
        ),
        "semantic_consolidation_consents": (
            "ex_semantic_consolidation_consents_no_overlap"
        ),
        "consolidation_composition_profiles": (
            "ex_consolidation_composition_profiles_no_overlap"
        ),
        "consolidation_composed_decisions": (
            "ex_consolidation_composed_decisions_no_overlap"
        ),
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


def test_factor_value_shares_set_composite_fk() -> None:
    table = Base.metadata.tables["semantic_factor_values"]
    composite = [
        c
        for c in table.constraints
        if isinstance(c, ForeignKeyConstraint)
        and {col.name for col in c.columns} == {"factor_set_id", "factor_set_key"}
    ]
    assert composite and {fk.column.table.name for fk in composite[0].elements} == {
        "semantic_factor_sets"
    }


def test_group_member_shares_group_composite_fk() -> None:
    table = Base.metadata.tables["semantic_consolidation_group_members"]
    composite = [
        c
        for c in table.constraints
        if isinstance(c, ForeignKeyConstraint)
        and {col.name for col in c.columns} == {"group_id", "group_key"}
    ]
    assert composite and {fk.column.table.name for fk in composite[0].elements} == {
        "semantic_consolidation_groups"
    }
    # Child is NOT bitemporal versioned: no version chain columns.
    cols = table.c
    assert "previous_version_id" not in cols
    assert "superseded_by_version_id" not in cols


def test_consent_and_decision_real_fks() -> None:
    consents = Base.metadata.tables["semantic_consolidation_consents"]
    assert any(
        fk.column.table.name == "semantic_consolidation_groups"
        and fk.parent.name == "group_id"
        for fk in consents.foreign_keys
    )
    decisions = Base.metadata.tables["consolidation_composed_decisions"]
    assert any(
        fk.column.table.name == "consolidation_composition_profiles"
        and fk.parent.name == "composition_profile_id"
        for fk in decisions.foreign_keys
    )


def test_loose_string_refs_have_no_fk() -> None:
    # group_ref (composed decisions) and subject_ref (vintage policies) are loose refs.
    group_ref = Base.metadata.tables["consolidation_composed_decisions"].c["group_ref"]
    assert not group_ref.foreign_keys
    subject_ref = Base.metadata.tables["factor_vintage_selection_policies"].c[
        "subject_ref"
    ]
    assert not subject_ref.foreign_keys


def test_enum_checks_declared() -> None:
    def check_names(t):
        return [
            c.name
            for c in Base.metadata.tables[t].constraints
            if c.name and c.name.startswith("ck_")
        ]

    def checks(t):
        return set(check_names(t))

    assert "ck_factor_vintage_selection_policies_kind" in checks(
        "factor_vintage_selection_policies"
    )
    consent_check_names = check_names("semantic_consolidation_consents")
    consent_checks = set(consent_check_names)
    assert "ck_semantic_consolidation_consents_status" in consent_checks
    assert "ck_semantic_consolidation_consents_consent_status" in consent_checks
    assert len(consent_check_names) == len(consent_checks)
    assert "ck_consolidation_composition_profiles_kind" in checks(
        "consolidation_composition_profiles"
    )
    assert "ck_consolidation_composed_decisions_outcome" in checks(
        "consolidation_composed_decisions"
    )


# --- Layer 2: migration-text coverage -------------------------------------------------


def test_migration_036_text() -> None:
    assert MIGRATION_036.exists()
    body = MIGRATION_036.read_text(encoding="utf-8")
    for snippet in [
        'revision = "036_add_factors_and_consolidation"',
        'down_revision = "035_add_profile_registry_tables"',
        "CREATE TABLE IF NOT EXISTS semantic_factor_sets",
        "CREATE TABLE IF NOT EXISTS semantic_consolidation_group_members",
        "fk_semantic_factor_values_set_key",
        "fk_consolidation_group_members_group_key",
        "EXCLUDE USING gist",
        "FOR EACH ROW EXECUTE FUNCTION sds_reject_versioned_mutation()",
        "FOR EACH ROW EXECUTE FUNCTION sds_reject_child_mutation()",
    ]:
        assert snippet in body, snippet


def test_migration_036_downgrade_does_not_drop_shared_functions() -> None:
    body = MIGRATION_036.read_text(encoding="utf-8")
    downgrade = body.split("def downgrade", 1)[1]
    # 036 must NOT drop the shared 033 trigger functions.
    assert "DROP FUNCTION" not in downgrade
    # Downgrade drops the new tables via a for-loop (f-string form).
    assert "DROP TABLE IF EXISTS {table}" in downgrade
    # Children dropped before the versioned mains they reference.
    assert "_CHILD_TABLES + tuple(reversed(_VERSIONED_TABLES))" in downgrade
    # All new tables appear in the migration body.
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


def test_disposable_db_varch1e_guardrails() -> None:
    engine = _disposable_engine()
    try:
        from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

        db_errors = (IntegrityError, InternalError, ProgrammingError)

        # Seed a decision-time backbone row + a published factor set.
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
                    "INSERT INTO semantic_factor_sets "
                    "(id, factor_set_key, source_authority, factor_set_version, "
                    "decision_commit_id, valid_from) VALUES "
                    "('fs1', 'fk', 'DEFRA', 1, :c, '2026-01-01Z')"
                ),
                {"c": c},
            )

        # Vintage-policy enum CHECK rejects an unknown policy_kind.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                c = connection.execute(
                    text("SELECT min(commit_id) FROM decision_commit_sequence")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO factor_vintage_selection_policies "
                        "(id, policy_key, subject_ref, policy_kind, policy_version, "
                        "decision_commit_id, valid_from) VALUES "
                        "('vpX', 'pk', 's', 'not_a_kind', 1, :c, '2026-01-01Z')"
                    ),
                    {"c": c},
                )

        # No-overlap on factor sets per factor_set_key.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                c = connection.execute(
                    text("SELECT min(commit_id) FROM decision_commit_sequence")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO semantic_factor_sets "
                        "(id, factor_set_key, source_authority, factor_set_version, "
                        "decision_commit_id, valid_from, valid_to) VALUES "
                        "('fs2', 'fk', 'DEFRA', 2, :c, '2026-01-01Z', '2027-01-01Z')"
                    ),
                    {"c": c},
                )

        # Value-shares-set composite FK: a value with a mismatched (set_id, set_key)
        # pair is rejected (key 'wrong' does not match factor set 'fs1' key 'fk').
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                c = connection.execute(
                    text("SELECT min(commit_id) FROM decision_commit_sequence")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO semantic_factor_values "
                        "(id, factor_set_id, factor_set_key, factor_key, "
                        "factor_value_version, decision_commit_id, valid_from) VALUES "
                        "('fv1', 'fs1', 'wrong', 'co2', 1, :c, '2026-01-01Z')"
                    ),
                    {"c": c},
                )

        # Member-shares-group composite FK rejects an unknown (group_id, group_key) pair.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO semantic_consolidation_group_members "
                        "(id, group_id, group_key, member_tenant_ref) VALUES "
                        "('gm1', 'nope', 'nope', 't1')"
                    )
                )

        # Append-only: DELETE of a published factor set is rejected by the shared trigger.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM semantic_factor_sets WHERE id = 'fs1'")
                )
    finally:
        engine.dispose()
