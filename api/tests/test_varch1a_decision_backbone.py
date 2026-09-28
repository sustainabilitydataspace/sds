"""VARCH-1a contract tests — semantic-atomization decision-time backbone.

Three layers of evidence, because the pytest suite mocks Alembic and cannot apply DDL:
1. Metadata drift coverage — Base.metadata declares the five tables with the identity
   PK, partial unique indexes, CHECK constraints, EXCLUDE constraint, and resolver index
   that the migration creates (mirrors test_alembic_metadata_drift_guard.py).
2. Migration-text coverage — the 032 migration declares the revision chain, btree_gist,
   the guardrail SQL, the append-only trigger, and a reverse-order downgrade.
3. Disposable-DB smoke — when SDS_MIGRATION_TEST_DATABASE_URL is provided, apply head and
   assert the live guardrails actually reject violations. Skipped otherwise (residual
   risk recorded in the run artifacts).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint

from src.database import models as _models  # noqa: F401 - register metadata
from src.database.base import Base

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_032 = (
    API_ROOT / "alembic" / "versions" / "032_add_semantic_decision_backbone.py"
)

VARCH1A_TABLES = (
    "semantic_stewards",
    "decision_commit_epochs",
    "decision_commit_fences",
    "decision_commit_sequence",
    "semantic_publication_chains",
)


def _index(table_name: str, index_name: str):
    table = Base.metadata.tables[table_name]
    return next((idx for idx in table.indexes if idx.name == index_name), None)


def _check_names(table_name: str) -> set[str]:
    table = Base.metadata.tables[table_name]
    return {c.name for c in table.constraints if c.name and c.name.startswith("ck_")}


# --- Layer 1: metadata drift coverage -------------------------------------------------


def test_all_varch1a_tables_are_registered_on_metadata() -> None:
    for table_name in VARCH1A_TABLES:
        assert table_name in Base.metadata.tables, table_name


def test_commit_id_is_db_owned_identity() -> None:
    commit_id = Base.metadata.tables["decision_commit_sequence"].c.commit_id
    assert commit_id.primary_key is True
    assert commit_id.identity is not None, "commit_id must be GENERATED AS IDENTITY"
    assert commit_id.identity.always is True


def test_partial_unique_guardrail_indexes_exist() -> None:
    one_active = _index("decision_commit_epochs", "ux_one_active_decision_commit_epoch")
    assert one_active is not None and one_active.unique is True

    one_held = _index("decision_commit_fences", "ux_one_held_fence_per_epoch")
    assert one_held is not None and one_held.unique is True

    no_fork = _index("decision_commit_sequence", "ux_decision_commit_sequence_previous")
    assert no_fork is not None and no_fork.unique is True


def test_fence_token_is_unique_constraint_for_fk_target() -> None:
    from sqlalchemy import UniqueConstraint

    table = Base.metadata.tables["decision_commit_fences"]
    assert any(
        isinstance(c, UniqueConstraint)
        and tuple(col.name for col in c.columns) == ("fence_token",)
        for c in table.constraints
    )


def test_commit_epoch_fence_binding_is_composite() -> None:
    from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

    fences = Base.metadata.tables["decision_commit_fences"]
    assert any(
        isinstance(c, UniqueConstraint)
        and tuple(col.name for col in c.columns) == ("epoch_number", "fence_token")
        for c in fences.constraints
    ), "fences need a composite UNIQUE(epoch_number, fence_token) FK target"

    sequence = Base.metadata.tables["decision_commit_sequence"]
    composite_fks = [
        c
        for c in sequence.constraints
        if isinstance(c, ForeignKeyConstraint)
        and {col.name for col in c.columns} == {"epoch_number", "fence_token"}
    ]
    assert composite_fks, "sequence must bind (epoch_number, fence_token) via one FK"
    # The pair must point at decision_commit_fences, not two independent parents.
    targets = {fk.column.table.name for fk in composite_fks[0].elements}
    assert targets == {"decision_commit_fences"}


def test_sequence_check_constraints_declared() -> None:
    checks = _check_names("decision_commit_sequence")
    assert "ck_decision_commit_sequence_positive" in checks
    assert "ck_decision_commit_sequence_monotonic" in checks
    assert "ck_decision_commit_sequence_hash_hex" in checks


def test_publication_no_overlap_exclude_constraint_declared() -> None:
    table = Base.metadata.tables["semantic_publication_chains"]
    excludes = [c for c in table.constraints if isinstance(c, ExcludeConstraint)]
    assert any(c.name == "ex_semantic_publication_chains_no_overlap" for c in excludes)


def test_publication_resolver_index_columns() -> None:
    idx = _index(
        "semantic_publication_chains", "ix_semantic_publication_chains_resolver"
    )
    assert idx is not None
    assert tuple(col.name for col in idx.columns) == (
        "subject_kind",
        "subject_id",
        "valid_from",
        "valid_to",
        "decision_commit_id",
    )


def test_steward_role_check_declared() -> None:
    assert "ck_semantic_stewards_role" in _check_names("semantic_stewards")


# --- Layer 2: migration-text coverage -------------------------------------------------


def test_migration_032_declares_chain_and_guardrails() -> None:
    assert MIGRATION_032.exists()
    body = MIGRATION_032.read_text(encoding="utf-8")

    for snippet in [
        'revision = "032_add_semantic_decision_backbone"',
        'down_revision = "031_add_semantic_projection_metadata"',
        "CREATE EXTENSION IF NOT EXISTS btree_gist",
        "GENERATED ALWAYS AS IDENTITY",
        "ux_one_active_decision_commit_epoch",
        "ux_one_held_fence_per_epoch",
        "ux_decision_commit_sequence_previous",
        "ex_semantic_publication_chains_no_overlap",
        "EXCLUDE USING gist",
        "ix_semantic_publication_chains_resolver",
        "uq_decision_commit_fences_epoch_token",
        "fk_decision_commit_sequence_epoch_fence",
        "FOREIGN KEY (epoch_number, fence_token)",
        "superseded_by_commit_id IS NULL",
        "CREATE OR REPLACE FUNCTION sds_reject_publication_mutation",
        "CREATE TRIGGER trg_reject_publication_mutation",
    ]:
        assert snippet in body, snippet


def test_migration_032_downgrade_is_reverse_fk_order() -> None:
    body = MIGRATION_032.read_text(encoding="utf-8")
    downgrade = body.split("def downgrade", 1)[1]
    order = [
        downgrade.index("DROP TABLE IF EXISTS semantic_publication_chains"),
        downgrade.index("DROP TABLE IF EXISTS decision_commit_sequence"),
        downgrade.index("DROP TABLE IF EXISTS decision_commit_fences"),
        downgrade.index("DROP TABLE IF EXISTS decision_commit_epochs"),
        downgrade.index("DROP TABLE IF EXISTS semantic_stewards"),
    ]
    assert order == sorted(order), "tables must drop in reverse FK dependency order"
    # Trigger and function are dropped before their table.
    assert "DROP TRIGGER IF EXISTS trg_reject_publication_mutation" in downgrade
    assert "DROP FUNCTION IF EXISTS sds_reject_publication_mutation" in downgrade


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


def _seed_epoch_fence_commit(connection) -> int:
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
    commit_id = connection.execute(
        text(
            "INSERT INTO decision_commit_sequence (epoch_number, fence_token) "
            "VALUES (1, 'fence-1') RETURNING commit_id"
        )
    ).scalar_one()
    return commit_id


def test_disposable_db_guardrails_reject_violations() -> None:
    engine = _disposable_engine()
    try:
        from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

        db_errors = (IntegrityError, InternalError, ProgrammingError)

        # commit_id is DB-allocated and monotonic.
        with engine.begin() as connection:
            first = _seed_epoch_fence_commit(connection)
            second = connection.execute(
                text(
                    "INSERT INTO decision_commit_sequence "
                    "(epoch_number, fence_token, previous_commit_id) "
                    "VALUES (1, 'fence-1', :prev) RETURNING commit_id"
                ),
                {"prev": first},
            ).scalar_one()
            assert second > first

        # Only one active epoch allowed.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO decision_commit_epochs "
                        "(epoch_number, fence_token, status) "
                        "VALUES (2, 'fence-2', 'active')"
                    )
                )

        # Publication no-overlap: two overlapping published rows for one subject.
        with engine.begin() as connection:
            commit_id = connection.execute(
                text("SELECT min(commit_id) FROM decision_commit_sequence")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO semantic_publication_chains "
                    "(id, subject_kind, subject_id, valid_from, valid_to, "
                    "decision_commit_id) VALUES "
                    "('p1', 'concept', 's1', '2026-01-01Z', '2026-06-01Z', :c)"
                ),
                {"c": commit_id},
            )
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                commit_id = connection.execute(
                    text("SELECT min(commit_id) FROM decision_commit_sequence")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO semantic_publication_chains "
                        "(id, subject_kind, subject_id, valid_from, valid_to, "
                        "decision_commit_id) VALUES "
                        "('p2', 'concept', 's1', '2026-03-01Z', '2026-09-01Z', :c)"
                    ),
                    {"c": commit_id},
                )

        # Append-only: DELETE of a published row is rejected by the trigger.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM semantic_publication_chains WHERE id = 'p1'")
                )

        # Append-only: supersede transition must set superseded_by_commit_id.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE semantic_publication_chains SET status = 'superseded' "
                        "WHERE id = 'p1'"
                    )
                )

        # Composite binding: a commit cannot pair fence-1 (epoch 1) with epoch 2.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO decision_commit_epochs "
                    "(epoch_number, fence_token, status) "
                    "VALUES (2, 'fence-2', 'superseded')"
                )
            )
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO decision_commit_sequence (epoch_number, fence_token) "
                        "VALUES (2, 'fence-1')"
                    )
                )
    finally:
        engine.dispose()
