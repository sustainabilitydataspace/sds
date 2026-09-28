"""VARCH-1f contract tests — EVS + restatement + tenant-private + disclosure/license.

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
MIGRATION_037 = (
    API_ROOT / "alembic" / "versions" / "037_add_evs_restatement_tenant_private.py"
)

VERSIONED_TABLES = (
    "semantic_effective_version_sets",
    "semantic_restatement_events",
    "semantic_trace_supersessions",
    "semantic_restatement_subscriptions",
    "semantic_tombstones",
    "license_rights_windows",
    "license_rights_decisions",
    "aggregate_disclosure_policies",
    "aggregate_suppression_decision_pins",
)
REGISTRY_TABLES = (
    "private_commitment_profiles",
    "private_commitment_keys",
    "aggregate_disclosure_profiles",
)
CHILD_TABLES = (
    "semantic_effective_version_set_members",
    "semantic_restatement_outbox",
    "aggregate_release_ledger",
    "aggregate_query_audit",
    "tenant_private_value_payloads",
)
ALL_TABLES = VERSIONED_TABLES + REGISTRY_TABLES + CHILD_TABLES


def _has_exclude(table_name: str, name: str) -> bool:
    table = Base.metadata.tables[table_name]
    return any(
        isinstance(c, ExcludeConstraint) and c.name == name for c in table.constraints
    )


# --- Layer 1: metadata drift coverage -------------------------------------------------


def test_all_varch1f_tables_registered() -> None:
    for t in ALL_TABLES:
        assert t in Base.metadata.tables, t


def test_versioned_tables_have_no_overlap_and_version_chain() -> None:
    expected_exclude = {
        "semantic_effective_version_sets": "ex_semantic_evs_no_overlap",
        "semantic_restatement_events": ("ex_semantic_restatement_events_no_overlap"),
        "semantic_trace_supersessions": ("ex_semantic_trace_supersessions_no_overlap"),
        "semantic_restatement_subscriptions": (
            "ex_semantic_restatement_subscriptions_no_overlap"
        ),
        "semantic_tombstones": "ex_semantic_tombstones_no_overlap",
        "license_rights_windows": "ex_license_rights_windows_no_overlap",
        "license_rights_decisions": "ex_license_rights_decisions_no_overlap",
        "aggregate_disclosure_policies": (
            "ex_aggregate_disclosure_policies_no_overlap"
        ),
        "aggregate_suppression_decision_pins": (
            "ex_aggregate_suppression_decision_pins_no_overlap"
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


def test_registry_tables_are_not_bitemporal_and_version_is_varchar() -> None:
    for t in REGISTRY_TABLES:
        cols = Base.metadata.tables[t].c
        # Registry shape: NO valid/decision bitemporal columns, version VARCHAR.
        assert "valid_from" not in cols and "decision_commit_id" not in cols, t
        assert "version" in cols
        assert cols["version"].type.python_type is str, t
        # Content-addressable hash is NOT NULL.
        hash_col = next(c for c in cols if c.name.endswith("_hash"))
        assert hash_col.nullable is False, t


def test_ledger_and_child_tables_are_not_versioned() -> None:
    # Insert-only ledgers + immutable children carry no version chain.
    for t in CHILD_TABLES:
        cols = Base.metadata.tables[t].c
        assert "previous_version_id" not in cols, t
        assert "superseded_by_version_id" not in cols, t


def test_tenant_private_payload_is_tenant_scoped_no_overlap_omitted() -> None:
    table = Base.metadata.tables["tenant_private_value_payloads"]
    cols = table.c
    # Tenant-scoped + holds ciphertext bytes.
    assert cols["tenant_id"].nullable is False
    assert cols["payload_ciphertext"].type.python_type is bytes
    assert cols["payload_ciphertext"].nullable is False
    # Documented choice: NO no-overlap EXCLUDE on the tenant-private store.
    assert not any(isinstance(c, ExcludeConstraint) for c in table.constraints)


def test_evs_member_shares_scope_composite_fk() -> None:
    table = Base.metadata.tables["semantic_effective_version_set_members"]
    composite = [
        c
        for c in table.constraints
        if isinstance(c, ForeignKeyConstraint)
        and {col.name for col in c.columns}
        == {"effective_version_set_id", "scope_context"}
    ]
    assert composite and {fk.column.table.name for fk in composite[0].elements} == {
        "semantic_effective_version_sets"
    }


def test_license_decision_shares_window_composite_fk() -> None:
    table = Base.metadata.tables["license_rights_decisions"]
    composite = [
        c
        for c in table.constraints
        if isinstance(c, ForeignKeyConstraint)
        and {col.name for col in c.columns} == {"license_window_id", "license_key"}
    ]
    assert composite and {fk.column.table.name for fk in composite[0].elements} == {
        "license_rights_windows"
    }


def test_enum_checks_declared() -> None:
    def checks(t):
        return {
            c.name
            for c in Base.metadata.tables[t].constraints
            if c.name and c.name.startswith("ck_")
        }

    assert "ck_semantic_restatement_events_policy" in checks(
        "semantic_restatement_events"
    )
    assert "ck_license_rights_decisions_egress_policy" in checks(
        "license_rights_decisions"
    )
    assert "ck_semantic_tombstones_reason" in checks("semantic_tombstones")
    assert "ck_aggregate_suppression_decision_pins_decision" in checks(
        "aggregate_suppression_decision_pins"
    )


def test_insert_only_ledgers_have_idempotency_or_key_unique() -> None:
    from sqlalchemy import UniqueConstraint

    def unique_names(t):
        return {
            c.name
            for c in Base.metadata.tables[t].constraints
            if isinstance(c, UniqueConstraint)
        }

    assert "uq_semantic_restatement_outbox_idempotency" in unique_names(
        "semantic_restatement_outbox"
    )
    assert "uq_aggregate_release_ledger_release_key" in unique_names(
        "aggregate_release_ledger"
    )


# --- Layer 2: migration-text coverage -------------------------------------------------


def test_migration_037_text() -> None:
    assert MIGRATION_037.exists()
    body = MIGRATION_037.read_text(encoding="utf-8")
    for snippet in [
        'revision = "037_add_evs_restatement_tenant_private"',
        'down_revision = "036_add_factors_and_consolidation"',
        "CREATE TABLE IF NOT EXISTS semantic_effective_version_sets",
        "CREATE TABLE IF NOT EXISTS tenant_private_value_payloads",
        "fk_semantic_evs_members_evs_scope",
        "fk_license_rights_decisions_window_key",
        "payload_ciphertext BYTEA NOT NULL",
        "EXCLUDE USING gist",
        "FOR EACH ROW EXECUTE FUNCTION sds_reject_versioned_mutation()",
        "FOR EACH ROW EXECUTE FUNCTION sds_reject_profile_mutation()",
        "FOR EACH ROW EXECUTE FUNCTION sds_reject_child_mutation()",
    ]:
        assert snippet in body, snippet


def test_migration_037_creates_no_new_trigger_functions() -> None:
    body = MIGRATION_037.read_text(encoding="utf-8")
    # VARCH-1f reuses 033 + 035 functions; it must not (re)create any function.
    assert "CREATE OR REPLACE FUNCTION" not in body
    assert "CREATE FUNCTION" not in body


def test_migration_037_downgrade_does_not_drop_shared_functions() -> None:
    body = MIGRATION_037.read_text(encoding="utf-8")
    downgrade = body.split("def downgrade", 1)[1]
    # Must NOT drop the shared 033/035 trigger functions.
    assert "DROP FUNCTION" not in downgrade
    # Downgrade drops the new tables via a for-loop (f-string form).
    assert "DROP TABLE IF EXISTS {table}" in downgrade
    # Every new table appears in the module (drives the loops).
    for table in ALL_TABLES:
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


def _seed_commit(connection) -> int:
    connection.execute(
        text(
            "INSERT INTO decision_commit_epochs "
            "(epoch_number, fence_token, status) VALUES (1, 'f', 'active') "
            "ON CONFLICT DO NOTHING"
        )
    )
    connection.execute(
        text(
            "INSERT INTO decision_commit_fences "
            "(id, fence_token, epoch_number, status) VALUES "
            "('f1', 'f', 1, 'held') ON CONFLICT DO NOTHING"
        )
    )
    return connection.execute(
        text(
            "INSERT INTO decision_commit_sequence (epoch_number, fence_token) "
            "VALUES (1, 'f') RETURNING commit_id"
        )
    ).scalar_one()


def test_disposable_db_varch1f_guardrails() -> None:
    engine = _disposable_engine()
    try:
        from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

        db_errors = (IntegrityError, InternalError, ProgrammingError)

        # Seed a license window + decision (valid bitemporal row).
        with engine.begin() as connection:
            c = _seed_commit(connection)
            connection.execute(
                text(
                    "INSERT INTO license_rights_windows "
                    "(id, license_key, source_authority, dataset_ref, license_id, "
                    "rights_scope, access_scope, license_window_version, "
                    "decision_commit_id, valid_from) VALUES "
                    "('lw1', 'lk', 'auth', 'ds', 'lic', 'derived', 'shared', 1, "
                    ":c, '2026-01-01Z')"
                ),
                {"c": c},
            )
            connection.execute(
                text(
                    "INSERT INTO license_rights_decisions "
                    "(id, license_window_id, license_key, decision_key, "
                    "temporal_egress_policy, allowed_recipient_scope, "
                    "decision_version, decision_commit_id, valid_from) VALUES "
                    "('ld1', 'lw1', 'lk', 'dk', 'trace_decision_time', 'shared', "
                    "1, :c, '2026-01-01Z')"
                ),
                {"c": c},
            )

        # Enum CHECK rejects an unknown temporal_egress_policy.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                c = connection.execute(
                    text("SELECT min(commit_id) FROM decision_commit_sequence")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO license_rights_decisions "
                        "(id, license_window_id, license_key, decision_key, "
                        "temporal_egress_policy, allowed_recipient_scope, "
                        "decision_version, decision_commit_id, valid_from) VALUES "
                        "('ldX', 'lw1', 'lk', 'dk2', 'not_a_policy', 'shared', 1, "
                        ":c, '2026-01-01Z')"
                    ),
                    {"c": c},
                )

        # Composite window FK: a decision cannot pair a window id with a wrong key.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                c = connection.execute(
                    text("SELECT min(commit_id) FROM decision_commit_sequence")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO license_rights_decisions "
                        "(id, license_window_id, license_key, decision_key, "
                        "temporal_egress_policy, allowed_recipient_scope, "
                        "decision_version, decision_commit_id, valid_from) VALUES "
                        "('ldY', 'lw1', 'wrong_key', 'dk3', 'egress_read_time', "
                        "'shared', 1, :c, '2026-01-01Z')"
                    ),
                    {"c": c},
                )

        # No-overlap on license windows per license_key.
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                c = connection.execute(
                    text("SELECT min(commit_id) FROM decision_commit_sequence")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO license_rights_windows "
                        "(id, license_key, source_authority, dataset_ref, "
                        "license_id, rights_scope, access_scope, "
                        "license_window_version, decision_commit_id, valid_from, "
                        "valid_to) VALUES "
                        "('lw2', 'lk', 'auth', 'ds', 'lic', 'derived', 'shared', "
                        "2, :c, '2026-01-01Z', '2027-01-01Z')"
                    ),
                    {"c": c},
                )

        # Registry append-only: a deprecated profile row cannot be mutated further.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO private_commitment_profiles "
                    "(id, profile_id, version, algorithm, descriptor, "
                    "profile_hash) VALUES "
                    "('pcp1', 'pid', 'v1', 'hmac_sha256', '{}'::jsonb, "
                    "'" + "a" * 64 + "')"
                )
            )
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM private_commitment_profiles WHERE id = 'pcp1'")
                )

        # Tenant-private payload is append-only: DELETE rejected by the child guard.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_private_value_payloads "
                    "(id, tenant_id, payload_key, value_context_ref, "
                    "payload_ciphertext, dek_ref, payload_version) VALUES "
                    "('tp1', 't1', 'pk', 'vc', '\\x00'::bytea, 'dek', 1)"
                )
            )
        with pytest.raises(db_errors):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM tenant_private_value_payloads WHERE id = 'tp1'")
                )

        # Insert-only ledger: outbox dedup on idempotency_key.
        with engine.begin() as connection:
            c = connection.execute(
                text("SELECT min(commit_id) FROM decision_commit_sequence")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO semantic_restatement_events "
                    "(id, restatement_key, subject_kind, subject_ref, "
                    "restatement_policy, reason_code, restatement_version, "
                    "decision_commit_id, valid_from) VALUES "
                    "('re1', 'rk', 'concept', 'sref', 'supersede_trace', "
                    "'correction', 1, :c, '2026-01-01Z')"
                ),
                {"c": c},
            )
            connection.execute(
                text(
                    "INSERT INTO semantic_restatement_outbox "
                    "(id, restatement_id, subscriber_ref, idempotency_key, "
                    "decision_commit_id, event_schema_version, payload) VALUES "
                    "('ob1', 're1', 'sub', 'idem-1', :c, 'v1', '{}'::jsonb)"
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
                        "INSERT INTO semantic_restatement_outbox "
                        "(id, restatement_id, subscriber_ref, idempotency_key, "
                        "decision_commit_id, event_schema_version, payload) VALUES "
                        "('ob2', 're1', 'sub', 'idem-1', :c, 'v1', '{}'::jsonb)"
                    ),
                    {"c": c},
                )
    finally:
        engine.dispose()
