"""PostgreSQL qualification for the 048 revision-integrity migration.

Run this module serially. It resets only the explicitly disposable database named by
SDS_MIGRATION_TEST_DATABASE_URL when SDS_MIGRATION_TEST_ALLOW_RESET=true.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from alembic import command
from src.database.init_db import init_db_for_engine
from src.database.migrations import _alembic_config, assert_database_schema_head
from src.database.models import CurrentValuePointer
from src.database.repositories.value_repository import ValueRepository
from src.services.value_ingest import PreparedValueRecord
from src.services.value_revision_store import (
    ValueRevisionInput,
    ValueRevisionStore,
    _build_event_hash,
    verify_value_revision_event_chain,
)
from src.services.value_versioning import ValueContextIdentity


def test_bulk_keyed_write_infers_tenant_partial_index(migrated_postgres):
    """The production bulk writer must infer the 046 partial key index on PG15."""

    def record(value_id: str) -> PreparedValueRecord:
        return PreparedValueRecord(
            value_id=value_id,
            concept="urn:sds:test:water",
            entity="plant-a",
            period=date(2025, 12, 31),
            value=Decimal("12"),
            value_type="numeric",
            unit="m3",
            external_key="pg15:partial-key-probe",
        )

    with Session(migrated_postgres) as db:
        repo = ValueRepository(db)
        first = repo._save_values_bulk_upsert([record("pg15-tenant-a")], "tenant-a")
        assert len(first) == 1
        with pytest.raises(RuntimeError, match="External key collision"):
            repo._save_values_bulk_upsert([record("pg15-duplicate")], "tenant-a")
        second = repo._save_values_bulk_upsert([record("pg15-tenant-b")], "tenant-b")
        assert len(second) == 1
        db.rollback()


def test_external_schema_gate_accepts_pg_head_under_read_only_transaction(
    migrated_postgres,
):
    with migrated_postgres.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        assert_database_schema_head(connection)
        connection.rollback()
    init_db_for_engine(migrated_postgres, externally_managed=True)


@pytest.fixture(scope="module")
def migrated_postgres() -> Engine:
    url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    if make_url(url).get_backend_name() != "postgresql":
        pytest.skip("revision-integrity qualification requires disposable PostgreSQL")
    if os.environ.get("PYTEST_XDIST_WORKER") is not None:
        pytest.skip("run disposable PostgreSQL schema resets serially, without xdist")

    engine = create_engine(
        url,
        isolation_level="READ COMMITTED",
        connect_args={
            "connect_timeout": 3,
            "options": "-c lock_timeout=5000 -c statement_timeout=15000",
        },
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        with engine.connect() as connection:
            command.upgrade(
                _alembic_config(connection), "047_scope_indicator_jobs_to_tenants"
            )
        with engine.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO value_contexts (
                        id, tenant_id, context_hash_recipe_version, context_hash,
                        entity_id, reporting_period_id, period_start, period_end, period_type,
                        reporting_boundary_id, indicator_identifier,
                        standard_release_id, standard_datapoint_id, scenario_basis,
                        value_kind
                    ) VALUES (
                        1, 'tenant-a', 'sds-value-context-v2', :context_hash,
                        'entity-a', 'FY2025', DATE '2025-01-01', DATE '2025-12-31',
                        'annual', 'operational_control',
                        'urn:sds:test:value', 'test-2025', 'test-value', 'actual',
                        'numeric'
                    )
                    """),
                {"context_hash": "a" * 64},
            )
            connection.execute(text("""
                    INSERT INTO value_revisions (
                        id, context_id, tenant_id, revision_number, state,
                        value_kind, canonical_value, numeric_value
                    ) VALUES (
                        'revision-a1', 1, 'tenant-a', 1, 'approved',
                        'numeric', '1', 1
                    )
                    """))
            connection.execute(
                text("""
                    INSERT INTO value_revision_events (
                        id, tenant_id, event_seq, context_event_seq, context_id,
                        revision_id, event_type, from_state, to_state,
                        pointer_moved, event_payload, occurred_by
                    ) VALUES (
                        'event-a1', 'tenant-a', 1, 1, 1,
                        'revision-a1', 'revision_created', NULL, 'approved',
                        true, CAST(:payload AS jsonb), 'migration-fixture'
                    )
                    """),
                {"payload": '{"revision_number":1}'},
            )
            connection.execute(text("""
                    INSERT INTO current_value_pointers (
                        context_id, tenant_id, revision_id, pointer_basis
                    ) VALUES (1, 'tenant-a', 'revision-a1', 'current')
                    """))
            connection.execute(text("""
                    INSERT INTO reported_value_pointers (
                        tenant_id, entity_id, reporting_period_id,
                        standard_release_id, report_snapshot_id, context_id,
                        revision_id, pointer_basis
                    ) VALUES (
                        'tenant-a', 'entity-a', 'FY2025', 'test-2025',
                        'snapshot-a', 1, 'revision-a1', 'reported'
                    )
                    """))
            connection.execute(
                text("""
                    INSERT INTO value_revision_events (
                        id, tenant_id, event_seq, context_event_seq, context_id,
                        revision_id, event_type, from_state, to_state,
                        pointer_moved, event_payload, occurred_by
                    ) VALUES (
                        'event-a2', 'tenant-a', 2, 2, 1,
                        'revision-a1', 'reported_pointer_created',
                        'approved', 'approved', true,
                        CAST(:payload AS jsonb), 'migration-fixture'
                    )
                    """),
                {"payload": '{"report_snapshot_id":"snapshot-a"}'},
            )
        with engine.begin() as connection:
            connection.execute(text("""
                UPDATE value_contexts SET period_start = NULL, period_end = NULL
                WHERE id = 1
                """))
        with pytest.raises(RuntimeError, match="current context without a period date"):
            with engine.connect() as connection:
                command.upgrade(_alembic_config(connection), "head")
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "047_scope_indicator_jobs_to_tenants"
            )
            connection.execute(text("""
                UPDATE value_contexts
                SET period_start = DATE '2025-01-01', period_end = DATE '2025-12-31'
                WHERE id = 1
                """))
        with engine.begin() as connection:
            connection.execute(text("""
                    INSERT INTO value_revision_events (
                        id, tenant_id, event_seq, context_event_seq, context_id,
                        revision_id, event_type, from_state, to_state,
                        pointer_moved, event_payload, occurred_by
                    ) VALUES (
                        'orphan-report-event', 'tenant-a', 3, 3, 1,
                        'revision-a1', 'reported_pointer_created',
                        'approved', 'approved', true,
                        '{"report_snapshot_id":"snapshot-missing"}'::jsonb,
                        'migration-fixture'
                    )
                    """))
        with pytest.raises(
            RuntimeError, match="legacy reported pointer event lacks a durable pointer"
        ):
            with engine.connect() as connection:
                command.upgrade(_alembic_config(connection), "head")
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "047_scope_indicator_jobs_to_tenants"
            )
            connection.execute(
                text(
                    "DELETE FROM value_revision_events WHERE id = 'orphan-report-event'"
                )
            )
        with engine.begin() as connection:
            connection.execute(text("""
                    INSERT INTO value_revision_events (
                        id, tenant_id, event_seq, context_event_seq, context_id,
                        revision_id, event_type, from_state, to_state,
                        pointer_moved, event_payload, occurred_by
                    ) VALUES (
                        'duplicate-report-event', 'tenant-a', 3, 3, 1,
                        'revision-a1', 'reported_pointer_created',
                        'approved', 'approved', true,
                        '{"report_snapshot_id":"snapshot-a"}'::jsonb,
                        'migration-fixture'
                    )
                    """))
        with pytest.raises(
            RuntimeError, match="duplicate legacy reported pointer events"
        ):
            with engine.connect() as connection:
                command.upgrade(_alembic_config(connection), "head")
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "047_scope_indicator_jobs_to_tenants"
            )
            connection.execute(
                text(
                    "DELETE FROM value_revision_events WHERE id = 'duplicate-report-event'"
                )
            )
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO value_revisions (
                    id, context_id, tenant_id, revision_number, state,
                    value_kind, canonical_value, numeric_value
                ) VALUES (
                    'revision-false-pointer', 1, 'tenant-a', 2, 'draft',
                    'numeric', '2', 2
                )
                """))
            connection.execute(text("""
                INSERT INTO value_revision_events (
                    id, tenant_id, event_seq, context_event_seq, context_id,
                    revision_id, event_type, from_state, to_state,
                    pointer_moved, event_payload, occurred_by
                ) VALUES (
                    'event-false-pointer', 'tenant-a', 3, 3, 1,
                    'revision-false-pointer', 'revision_created', NULL,
                    'draft', true, '{}'::jsonb, 'migration-fixture'
                )
                """))
        with pytest.raises(
            RuntimeError, match="legacy current pointer event does not move"
        ):
            with engine.connect() as connection:
                command.upgrade(_alembic_config(connection), "head")
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "047_scope_indicator_jobs_to_tenants"
            )
            connection.execute(
                text(
                    "DELETE FROM value_revision_events WHERE id = 'event-false-pointer'"
                )
            )
            connection.execute(
                text("DELETE FROM value_revisions WHERE id = 'revision-false-pointer'")
            )
        with engine.begin() as connection:
            connection.execute(text("""
                    INSERT INTO value_revisions (
                        id, context_id, tenant_id, revision_number, state,
                        value_kind, canonical_value, numeric_value
                    ) VALUES (
                        'revision-regression', 1, 'tenant-a', 2, 'approved',
                        'numeric', '2', 2
                    )
                    """))
            connection.execute(text("""
                    INSERT INTO value_revision_events (
                        id, tenant_id, event_seq, context_event_seq, context_id,
                        revision_id, event_type, from_state, to_state,
                        pointer_moved, event_payload, occurred_by
                    ) VALUES (
                        'event-regression', 'tenant-a', 3, 3, 1,
                        'revision-regression', 'revision_created', NULL,
                        'approved', false, '{}'::jsonb, 'migration-fixture'
                    )
                    """))
        with pytest.raises(
            RuntimeError,
            match="legacy current pointer is not the highest eligible revision",
        ):
            with engine.connect() as connection:
                command.upgrade(_alembic_config(connection), "head")
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "047_scope_indicator_jobs_to_tenants"
            )
            connection.execute(
                text("DELETE FROM value_revision_events WHERE id = 'event-regression'")
            )
            connection.execute(
                text("DELETE FROM value_revisions WHERE id = 'revision-regression'")
            )
        with engine.connect() as connection:
            connection.execute(
                text("CREATE TEMP TABLE value_revision_events (id text)")
            )
            connection.execute(text("SET LOCAL search_path = pg_temp, public"))
            command.upgrade(_alembic_config(connection), "head")
            connection.execute(text("DROP TABLE pg_temp.value_revision_events"))
            connection.commit()
            connection.invalidate()  # Drop the attacker's session-local temp schema.
        yield engine
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
        finally:
            engine.dispose()


def _identity(tenant_id: str, entity_id: str) -> ValueContextIdentity:
    return ValueContextIdentity(
        tenant_id=tenant_id,
        entity_id=entity_id,
        reporting_period_id="FY2026",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        period_close_date=None,
        period_type="annual",
        reporting_boundary_id="operational_control",
        sds_indicator_id=None,
        indicator_identifier="urn:sds:test:value",
        standard_release_id="test-2026",
        standard_datapoint_id="test-value",
        dimensions={},
        expected_unit="kg",
        expected_currency=None,
        value_kind="numeric",
    )


def test_populated_upgrade_backfills_runtime_compatible_event_hash(
    migrated_postgres: Engine,
) -> None:
    with migrated_postgres.connect() as connection:
        row = connection.execute(text("""
                SELECT id, tenant_id, event_seq, context_event_seq, context_id,
                       revision_id, event_type, from_state, to_state,
                       pointer_moved, event_payload, event_payload_canonical,
                       idempotency_key, occurred_at, occurred_by,
                       previous_event_hash, event_hash
                FROM value_revision_events
                WHERE id = 'event-a1'
                """)).mappings().one()
        version = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        effects = connection.execute(text("""
                SELECT event_id, effect_kind
                FROM value_revision_event_effects
                WHERE event_id IN ('event-a1', 'event-a2')
                ORDER BY event_id, effect_kind
                """)).all()

    expected = _build_event_hash(
        event_id=row["id"],
        tenant_id=row["tenant_id"],
        event_seq=row["event_seq"],
        context_event_seq=row["context_event_seq"],
        context_id=row["context_id"],
        revision_id=row["revision_id"],
        event_type=row["event_type"],
        from_state=row["from_state"],
        to_state=row["to_state"],
        pointer_moved=row["pointer_moved"],
        event_payload=row["event_payload"],
        event_payload_canonical=row["event_payload_canonical"],
        idempotency_key=row["idempotency_key"],
        occurred_at=row["occurred_at"],
        occurred_by=row["occurred_by"],
        previous_event_hash=row["previous_event_hash"],
    )
    assert version == "051_add_admin_demo_package_installs"
    assert row["event_hash"] == expected
    assert effects == [
        ("event-a1", "current_pointer"),
        ("event-a1", "revision_created"),
        ("event-a2", "reported_pointer"),
    ]


def test_postgres_triggers_accept_store_writes_and_transitions(
    migrated_postgres: Engine,
) -> None:
    with Session(migrated_postgres) as db:
        store = ValueRevisionStore(db)
        first = store.append_revision(
            ValueRevisionInput(
                context_identity=_identity("tenant-b", "entity-b"),
                value=Decimal("2.5"),
                value_kind="numeric",
                unit="kg",
                state="approved",
                created_by="postgres-qualification",
            )
        )
        transition = store.transition_revision(
            revision_id=first.revision.id,
            to_state="voided",
            occurred_by="postgres-qualification",
            event_payload={"reading": 1.23e-5, "negative_zero": -0.0},
        )
        events = store.list_events(tenant_id="tenant-b", limit=100)

        assert transition.from_state == "approved"
        assert transition.to_state == "voided"
        assert verify_value_revision_event_chain(events, tenant_id="tenant-b") is True


def test_postgres_hash_matches_high_precision_payload_and_binds_time(
    migrated_postgres: Engine,
) -> None:
    payload_text = '{"n":0.123456789012345678901234567890123456789}'
    occurred_at = datetime(2026, 1, 2, 3, 4, 5, 6789)
    with migrated_postgres.connect() as connection:
        row = (
            connection.execute(
                text("""
                SELECT public.canonicalize_sds_jsonb(CAST(:payload AS jsonb))
                           AS canonical_payload,
                       public.calculate_sds_value_revision_event_hash(
                           'event-precise', 'tenant-precise', 1, 1, 1,
                           'revision-precise', 'revision_created', NULL,
                           'approved', true, CAST(:payload AS jsonb), NULL,
                           :occurred_at, 'tester', NULL
                       ) AS event_hash,
                       public.calculate_sds_value_revision_event_hash(
                           'event-precise', 'tenant-precise', 1, 1, 1,
                           'revision-precise', 'revision_created', NULL,
                           'approved', true, CAST(:payload AS jsonb), NULL,
                           :later_at, 'tester', NULL
                       ) AS later_hash
                """),
                {
                    "payload": payload_text,
                    "occurred_at": occurred_at,
                    "later_at": datetime(2026, 1, 2, 3, 4, 5, 6790),
                },
            )
            .mappings()
            .one()
        )

    expected = _build_event_hash(
        event_id="event-precise",
        tenant_id="tenant-precise",
        event_seq=1,
        context_event_seq=1,
        context_id=1,
        revision_id="revision-precise",
        event_type="revision_created",
        from_state=None,
        to_state="approved",
        pointer_moved=True,
        event_payload={"n": Decimal("0.123456789012345678901234567890123456789")},
        event_payload_canonical=row["canonical_payload"],
        idempotency_key=None,
        occurred_at=occurred_at,
        occurred_by="tester",
        previous_event_hash=None,
    )
    assert row["event_hash"] == expected
    assert row["event_hash"] != row["later_hash"]


def test_postgres_verifier_accepts_intact_numeric_jsonb_with_high_precision(
    migrated_postgres: Engine,
) -> None:
    payload = '{"n":0.123456789012345678901234567890123456789}'
    occurred_at = datetime(2026, 1, 2, 3, 4, 5, 6789)
    with migrated_postgres.connect() as connection:
        previous = connection.execute(text("""
            SELECT event_seq, context_event_seq, event_hash
            FROM value_revision_events
            WHERE tenant_id = 'tenant-a' AND context_id = 1
            ORDER BY event_seq DESC LIMIT 1
        """)).mappings().one()
        canonical = connection.execute(
            text("SELECT public.canonicalize_sds_jsonb(CAST(:payload AS jsonb))"),
            {"payload": payload},
        ).scalar_one()
    event_hash = _build_event_hash(
        event_id="event-precise-jsonb",
        tenant_id="tenant-a",
        event_seq=previous["event_seq"] + 1,
        context_event_seq=previous["context_event_seq"] + 1,
        context_id=1,
        revision_id="revision-a1",
        event_type="audit_noted",
        from_state="approved",
        to_state="approved",
        pointer_moved=False,
        event_payload={"n": Decimal("0.123456789012345678901234567890123456789")},
        event_payload_canonical=canonical,
        idempotency_key=None,
        occurred_at=occurred_at,
        occurred_by="tester",
        previous_event_hash=previous["event_hash"],
    )
    with migrated_postgres.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO value_revision_events (
                    id, tenant_id, event_seq, context_event_seq, context_id,
                    revision_id, event_type, from_state, to_state,
                    pointer_moved, event_payload, event_payload_canonical,
                    previous_event_hash, event_hash, occurred_at, occurred_by
                ) VALUES (
                    'event-precise-jsonb', 'tenant-a', :event_seq,
                    :context_event_seq, 1, 'revision-a1', 'audit_noted',
                    'approved', 'approved', false, CAST(:payload AS jsonb),
                    :canonical, :previous_hash, :event_hash, :occurred_at,
                    'tester'
                )
            """),
            {
                "event_seq": previous["event_seq"] + 1,
                "context_event_seq": previous["context_event_seq"] + 1,
                "payload": payload,
                "canonical": canonical,
                "previous_hash": previous["event_hash"],
                "event_hash": event_hash,
                "occurred_at": occurred_at,
            },
        )
    with Session(migrated_postgres) as db:
        events = ValueRevisionStore(db).list_events(tenant_id="tenant-a", limit=100)
        assert verify_value_revision_event_chain(events, tenant_id="tenant-a")
        events[-1].event_payload["n"] = 0.5
        assert not verify_value_revision_event_chain(events, tenant_id="tenant-a")


def test_postgres_store_persists_precise_decimal_payload_and_hash(
    migrated_postgres: Engine,
) -> None:
    precise = "0.123456789012345678901234567890123456789"
    with Session(migrated_postgres) as db:
        store = ValueRevisionStore(db)
        result = store.append_revision(
            ValueRevisionInput(
                context_identity=_identity("tenant-precise", "entity-precise"),
                value=Decimal("1"),
                value_kind="numeric",
                state="approved",
            )
        )
        store.transition_revision(
            revision_id=result.revision.id,
            to_state="locked",
            event_payload={"n": Decimal(precise)},
        )
        events = store.list_events(tenant_id="tenant-precise", limit=100)

        assert events[-1].event_payload == {"n": precise}
        assert verify_value_revision_event_chain(events, tenant_id="tenant-precise")


def test_postgres_fallback_transition_commits_with_transition_subject_receipt(
    migrated_postgres: Engine,
) -> None:
    with Session(migrated_postgres) as db:
        store = ValueRevisionStore(db)
        first = store.append_revision(
            ValueRevisionInput(
                context_identity=_identity("tenant-fallback", "entity-fallback"),
                value=Decimal("1"),
                value_kind="numeric",
                state="approved",
            )
        )
        second = store.append_revision(
            ValueRevisionInput(
                context_identity=_identity("tenant-fallback", "entity-fallback"),
                value=Decimal("2"),
                value_kind="numeric",
                state="approved",
            )
        )
        event = store.transition_revision(
            revision_id=second.revision.id,
            to_state="voided",
            occurred_by="postgres-qualification",
        )
        pointer = (
            db.query(CurrentValuePointer)
            .filter(CurrentValuePointer.context_id == first.context.id)
            .one()
        )

        assert pointer is not None
        assert pointer.revision_id == first.revision.id
        assert event.revision_id == second.revision.id
        assert (
            verify_value_revision_event_chain(
                store.list_events(tenant_id="tenant-fallback", limit=100),
                tenant_id="tenant-fallback",
            )
            is True
        )


def test_two_uncommitted_appends_to_same_context_commit_with_ordered_effects(
    migrated_postgres: Engine,
) -> None:
    identity = _identity("tenant-two-appends", "entity-two-appends")
    with Session(migrated_postgres) as db:
        store = ValueRevisionStore(db)
        first = store.append_revision(
            ValueRevisionInput(
                context_identity=identity,
                value=Decimal("1"),
                value_kind="numeric",
                state="approved",
            ),
            commit=False,
        )
        second = store.append_revision(
            ValueRevisionInput(
                context_identity=identity,
                value=Decimal("2"),
                value_kind="numeric",
                state="approved",
            ),
            commit=False,
        )
        db.commit()
        expected_revisions = (first.revision.id, second.revision.id)
        expected_event_id = second.event.id

    with Session(migrated_postgres) as db:
        store = ValueRevisionStore(db)
        events = store.list_events(tenant_id="tenant-two-appends", limit=100)
        assert len(events) == 2
        assert verify_value_revision_event_chain(events, tenant_id="tenant-two-appends")
        pointer = (
            db.query(CurrentValuePointer)
            .filter_by(context_id=events[0].context_id)
            .one()
        )
        assert pointer.revision_id == expected_revisions[1]
        assert pointer.last_event_id == expected_event_id
        effects = db.execute(text("""
                SELECT effect.previous_pointer_revision_id,
                       effect.new_pointer_revision_id
                FROM value_revision_event_effects AS effect
                JOIN value_revision_events AS event ON event.id = effect.event_id
                WHERE event.tenant_id = 'tenant-two-appends'
                  AND effect.effect_kind = 'current_pointer'
                ORDER BY event.context_event_seq
                """)).all()
        assert effects == [(None, expected_revisions[0]), expected_revisions]


def test_two_state_transitions_of_one_revision_commit_in_one_transaction(
    migrated_postgres: Engine,
) -> None:
    with Session(migrated_postgres) as db:
        result = ValueRevisionStore(db).append_revision(
            ValueRevisionInput(
                context_identity=_identity("tenant-two-states", "entity-two-states"),
                value=Decimal("1"),
                value_kind="numeric",
                state="draft",
            )
        )
        revision_id = result.revision.id
        context_id = result.context.id

    with migrated_postgres.begin() as connection:
        previous = connection.execute(text("""
                SELECT event_seq, context_event_seq, event_hash
                FROM value_revision_events
                WHERE tenant_id = 'tenant-two-states'
                ORDER BY event_seq DESC LIMIT 1
            """)).mappings().one()
        from_state = "draft"
        for event_id, to_state in (
            ("double-state-submitted", "submitted"),
            ("double-state-rejected", "rejected"),
        ):
            occurred_at = datetime(2026, 1, 2, 3, 4, 5, 6789)
            event_seq = previous["event_seq"] + 1
            context_event_seq = previous["context_event_seq"] + 1
            event_hash = _build_event_hash(
                event_id=event_id,
                tenant_id="tenant-two-states",
                event_seq=event_seq,
                context_event_seq=context_event_seq,
                context_id=context_id,
                revision_id=revision_id,
                event_type="state_transition",
                from_state=from_state,
                to_state=to_state,
                pointer_moved=False,
                event_payload=None,
                event_payload_canonical="null",
                idempotency_key=None,
                occurred_at=occurred_at,
                occurred_by="tester",
                previous_event_hash=previous["event_hash"],
            )
            connection.execute(
                text("""
                    UPDATE value_revisions
                    SET state = :to_state, last_state_event_id = :event_id
                    WHERE id = :revision_id
                """),
                {
                    "to_state": to_state,
                    "event_id": event_id,
                    "revision_id": revision_id,
                },
            )
            connection.execute(
                text("""
                    INSERT INTO value_revision_events (
                        id, tenant_id, event_seq, context_event_seq, context_id,
                        revision_id, event_type, from_state, to_state,
                        pointer_moved, previous_event_hash, event_hash,
                        event_payload_canonical, occurred_at, occurred_by
                    ) VALUES (
                        :event_id, 'tenant-two-states', :event_seq,
                        :context_event_seq, :context_id, :revision_id,
                        'state_transition', :from_state, :to_state, false,
                        :previous_hash, :event_hash, 'null', :occurred_at,
                        'tester'
                    )
                """),
                {
                    "event_id": event_id,
                    "event_seq": event_seq,
                    "context_event_seq": context_event_seq,
                    "context_id": context_id,
                    "revision_id": revision_id,
                    "from_state": from_state,
                    "to_state": to_state,
                    "previous_hash": previous["event_hash"],
                    "event_hash": event_hash,
                    "occurred_at": occurred_at,
                },
            )
            previous = {
                "event_seq": event_seq,
                "context_event_seq": context_event_seq,
                "event_hash": event_hash,
            }
            from_state = to_state

    with migrated_postgres.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM value_revisions WHERE id = :revision_id"),
                {"revision_id": revision_id},
            ).scalar_one()
            == "rejected"
        )


def test_postgres_concurrent_appends_preserve_revision_and_event_order(
    migrated_postgres: Engine,
) -> None:
    identity = _identity("tenant-concurrent", "entity-concurrent")
    with Session(migrated_postgres) as db:
        ValueRevisionStore(db).append_revision(
            ValueRevisionInput(
                context_identity=identity,
                value=Decimal("1"),
                value_kind="numeric",
                state="approved",
            )
        )

    start = Barrier(2)

    def append(value: str) -> int:
        with Session(migrated_postgres) as db:
            start.wait(timeout=10)
            result = ValueRevisionStore(db).append_revision(
                ValueRevisionInput(
                    context_identity=identity,
                    value=Decimal(value),
                    value_kind="numeric",
                    state="approved",
                )
            )
            return result.revision.revision_number

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(append, value) for value in ("2", "3")]
        assert sorted(item.result(timeout=20) for item in pending) == [2, 3]

    with Session(migrated_postgres) as db:
        store = ValueRevisionStore(db)
        assert verify_value_revision_event_chain(
            store.list_events(tenant_id="tenant-concurrent", limit=100),
            tenant_id="tenant-concurrent",
        )


def test_postgres_rejects_forged_event_hash(migrated_postgres: Engine) -> None:
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO value_revision_events (
                        id, tenant_id, event_seq, context_event_seq, context_id,
                        revision_id, event_type, from_state, to_state,
                        pointer_moved, event_payload, event_payload_canonical,
                        previous_event_hash, event_hash
                    )
                    SELECT 'event-forged', 'tenant-a', 3, 3, 1,
                           'revision-a1', 'forged', 'approved', 'approved',
                           false, '{}'::jsonb, '{}', event_hash, :forged_hash
                    FROM value_revision_events
                    WHERE id = 'event-a2'
                    """),
                {"forged_hash": "0" * 64},
            )


def test_postgres_rejects_hashed_noop_with_false_revision_source_state(
    migrated_postgres: Engine,
) -> None:
    occurred_at = datetime(2026, 1, 2, 3, 4, 5, 6789)
    with migrated_postgres.connect() as connection:
        previous = connection.execute(text("""
                SELECT event_seq, context_event_seq, event_hash
                FROM value_revision_events
                WHERE tenant_id = 'tenant-a' AND context_id = 1
                ORDER BY event_seq DESC
                LIMIT 1
                """)).mappings().one()
    event_hash = _build_event_hash(
        event_id="event-false-source",
        tenant_id="tenant-a",
        event_seq=previous["event_seq"] + 1,
        context_event_seq=previous["context_event_seq"] + 1,
        context_id=1,
        revision_id="revision-a1",
        event_type="audit_noted",
        from_state="draft",
        to_state="draft",
        pointer_moved=False,
        event_payload=None,
        event_payload_canonical="null",
        idempotency_key=None,
        occurred_at=occurred_at,
        occurred_by="attacker",
        previous_event_hash=previous["event_hash"],
    )
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO value_revision_events (
                        id, tenant_id, event_seq, context_event_seq, context_id,
                        revision_id, event_type, from_state, to_state,
                        pointer_moved, previous_event_hash, event_hash,
                        event_payload_canonical, occurred_at, occurred_by
                    ) VALUES (
                        'event-false-source', 'tenant-a', :event_seq,
                        :context_event_seq, 1, 'revision-a1', 'audit_noted',
                        'draft', 'draft', false, :previous_hash, :event_hash,
                        'null', :occurred_at, 'attacker'
                    )
                    """),
                {
                    "event_seq": previous["event_seq"] + 1,
                    "context_event_seq": previous["context_event_seq"] + 1,
                    "previous_hash": previous["event_hash"],
                    "event_hash": event_hash,
                    "occurred_at": occurred_at,
                },
            )


def test_postgres_rejects_hashed_noop_claiming_state_transition(
    migrated_postgres: Engine,
) -> None:
    occurred_at = datetime(2026, 1, 2, 3, 4, 5, 6789)
    with migrated_postgres.connect() as connection:
        previous = connection.execute(text("""
            SELECT event_seq, context_event_seq, event_hash
            FROM value_revision_events
            WHERE tenant_id = 'tenant-a' AND context_id = 1
            ORDER BY event_seq DESC
            LIMIT 1
            """)).mappings().one()
    event_hash = _build_event_hash(
        event_id="event-noop-transition",
        tenant_id="tenant-a",
        event_seq=previous["event_seq"] + 1,
        context_event_seq=previous["context_event_seq"] + 1,
        context_id=1,
        revision_id="revision-a1",
        event_type="state_transition",
        from_state="approved",
        to_state="approved",
        pointer_moved=False,
        event_payload=None,
        event_payload_canonical="null",
        idempotency_key=None,
        occurred_at=occurred_at,
        occurred_by="attacker",
        previous_event_hash=previous["event_hash"],
    )
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO value_revision_events (
                        id, tenant_id, event_seq, context_event_seq, context_id,
                        revision_id, event_type, from_state, to_state,
                        pointer_moved, previous_event_hash, event_hash,
                        event_payload_canonical, occurred_at, occurred_by
                    ) VALUES (
                        'event-noop-transition', 'tenant-a', :event_seq,
                        :context_event_seq, 1, 'revision-a1',
                        'state_transition', 'approved', 'approved', false,
                        :previous_hash, :event_hash, 'null', :occurred_at,
                        'attacker'
                    )
                    """),
                {
                    "event_seq": previous["event_seq"] + 1,
                    "context_event_seq": previous["context_event_seq"] + 1,
                    "previous_hash": previous["event_hash"],
                    "event_hash": event_hash,
                    "occurred_at": occurred_at,
                },
            )


def test_postgres_rejects_eligible_revision_without_current_pointer(
    migrated_postgres: Engine,
) -> None:
    occurred_at = datetime(2026, 1, 2, 3, 4, 5, 6789)
    with migrated_postgres.connect() as connection:
        previous = connection.execute(text("""
                SELECT event_seq, context_event_seq, event_hash
                FROM value_revision_events
                WHERE tenant_id = 'tenant-a' AND context_id = 1
                ORDER BY event_seq DESC
                LIMIT 1
                """)).mappings().one()
    event_hash = _build_event_hash(
        event_id="event-unpointed",
        tenant_id="tenant-a",
        event_seq=previous["event_seq"] + 1,
        context_event_seq=previous["context_event_seq"] + 1,
        context_id=1,
        revision_id="revision-unpointed",
        event_type="revision_created",
        from_state=None,
        to_state="approved",
        pointer_moved=False,
        event_payload=None,
        event_payload_canonical="null",
        idempotency_key=None,
        occurred_at=occurred_at,
        occurred_by="attacker",
        previous_event_hash=previous["event_hash"],
    )
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(text("""
                    INSERT INTO value_revisions (
                        id, context_id, tenant_id, revision_number, state,
                        value_kind, canonical_value, numeric_value,
                        creation_event_id
                    ) VALUES (
                        'revision-unpointed', 1, 'tenant-a', 2, 'approved',
                        'numeric', '2', 2, 'event-unpointed'
                    )
                    """))
            connection.execute(
                text("""
                    INSERT INTO value_revision_events (
                        id, tenant_id, event_seq, context_event_seq, context_id,
                        revision_id, event_type, from_state, to_state,
                        pointer_moved, previous_event_hash, event_hash,
                        event_payload_canonical, occurred_at, occurred_by
                    ) VALUES (
                        'event-unpointed', 'tenant-a', :event_seq,
                        :context_event_seq, 1, 'revision-unpointed',
                        'revision_created', NULL, 'approved', false,
                        :previous_hash, :event_hash, 'null', :occurred_at,
                        'attacker'
                    )
                    """),
                {
                    "event_seq": previous["event_seq"] + 1,
                    "context_event_seq": previous["context_event_seq"] + 1,
                    "previous_hash": previous["event_hash"],
                    "event_hash": event_hash,
                    "occurred_at": occurred_at,
                },
            )


def test_postgres_rejects_context_and_reported_identity_mutation(
    migrated_postgres: Engine,
) -> None:
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(
                text("UPDATE value_contexts SET entity_id = 'forged' WHERE id = 1")
            )
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(text("""
                    INSERT INTO reported_value_pointers (
                        tenant_id, entity_id, reporting_period_id,
                        standard_release_id, report_snapshot_id, context_id,
                        revision_id, pointer_basis, creation_event_id
                    ) VALUES (
                        'tenant-a', 'forged', 'FY2025', 'test-2025',
                        'snapshot-forged', 1, 'revision-a1', 'reported',
                        'reported-forged-create'
                    )
                    """))


def test_postgres_rejects_unaudited_state_change(migrated_postgres: Engine) -> None:
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(
                text(
                    "UPDATE value_revisions SET state = 'voided' "
                    "WHERE id = 'revision-a1'"
                )
            )


def test_postgres_rejects_forbidden_state_transition(
    migrated_postgres: Engine,
) -> None:
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(text("""
                    UPDATE value_revisions
                    SET state = 'draft', last_state_event_id = 'forbidden-event'
                    WHERE id = 'revision-a1'
                    """))


def test_postgres_rejects_cross_context_inputs_and_parent_cycles(
    migrated_postgres: Engine,
) -> None:
    with migrated_postgres.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO value_contexts (
                    id, tenant_id, context_hash_recipe_version, context_hash,
                    entity_id, reporting_period_id, period_type,
                    reporting_boundary_id, indicator_identifier,
                    standard_release_id, standard_datapoint_id, scenario_basis,
                    value_kind
                ) VALUES (
                    1000000, 'tenant-a', 'sds-value-context-v2', :context_hash,
                    'entity-2', 'FY2025', 'annual', 'operational_control',
                    'urn:sds:test:value-2', 'test-2025', 'test-value-2',
                    'actual', 'numeric'
                )
                """),
            {"context_hash": "b" * 64},
        )
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(text("""
                    INSERT INTO value_revisions (
                        id, context_id, tenant_id, revision_number, state,
                        value_kind, canonical_value, numeric_value,
                        input_revision_ids, creation_event_id
                    ) VALUES (
                        'cross-context', 1000000, 'tenant-a', 1, 'draft',
                        'numeric', '2', 2, '["revision-a1"]'::jsonb,
                        'cross-context-create'
                    )
                    """))
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(text("""
                    INSERT INTO value_revisions (
                        id, context_id, tenant_id, revision_number, state,
                        value_kind, canonical_value, numeric_value,
                        parent_revision_id, creation_event_id
                    ) VALUES (
                        'self-cycle', 1000000, 'tenant-a', 1, 'draft',
                        'numeric', '2', 2, 'self-cycle', 'self-cycle-create'
                    )
                    """))


def test_postgres_rejects_unapplied_transition_event(
    migrated_postgres: Engine,
) -> None:
    occurred_at = datetime(2026, 1, 2, 3, 4, 5, 6789)
    with migrated_postgres.connect() as connection:
        previous = connection.execute(text("""
                SELECT event_seq, context_event_seq, event_hash
                FROM value_revision_events
                WHERE tenant_id = 'tenant-a' AND context_id = 1
                ORDER BY event_seq DESC
                LIMIT 1
                """)).mappings().one()
    event_hash = _build_event_hash(
        event_id="event-unapplied",
        tenant_id="tenant-a",
        event_seq=previous["event_seq"] + 1,
        context_event_seq=previous["context_event_seq"] + 1,
        context_id=1,
        revision_id="revision-a1",
        event_type="state_transition",
        from_state="approved",
        to_state="voided",
        pointer_moved=False,
        event_payload=None,
        event_payload_canonical="null",
        idempotency_key=None,
        occurred_at=occurred_at,
        occurred_by="attacker",
        previous_event_hash=previous["event_hash"],
    )

    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO value_revision_events (
                        id, tenant_id, event_seq, context_event_seq, context_id,
                        revision_id, event_type, from_state, to_state,
                        pointer_moved, previous_event_hash, event_hash,
                        event_payload_canonical, occurred_at, occurred_by
                    ) VALUES (
                        'event-unapplied', 'tenant-a', :event_seq,
                        :context_event_seq, 1, 'revision-a1', 'state_transition',
                        'approved', 'voided', false, :previous_hash, :event_hash,
                        'null', :occurred_at, 'attacker'
                    )
                    """),
                {
                    "event_seq": previous["event_seq"] + 1,
                    "context_event_seq": previous["context_event_seq"] + 1,
                    "previous_hash": previous["event_hash"],
                    "event_hash": event_hash,
                    "occurred_at": occurred_at,
                },
            )


def test_postgres_rejects_reused_event_receipt(migrated_postgres: Engine) -> None:
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(text("""
                    UPDATE value_revisions
                    SET state = 'voided', last_state_event_id = 'event-reused'
                    WHERE id = 'revision-a1'
                    """))
            connection.execute(text("""
                    UPDATE value_revisions
                    SET state = 'redacted', last_state_event_id = 'event-reused'
                    WHERE id = 'revision-a1'
                    """))


def test_postgres_rejects_reusing_legacy_creation_event_for_transition(
    migrated_postgres: Engine,
) -> None:
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(text("""
                    UPDATE value_revisions
                    SET state = 'voided', last_state_event_id = 'event-a1'
                    WHERE id = 'revision-a1'
                    """))


def test_postgres_rejects_current_pointer_regression_and_eligible_delete(
    migrated_postgres: Engine,
) -> None:
    with Session(migrated_postgres) as db:
        store = ValueRevisionStore(db)
        first = store.append_revision(
            ValueRevisionInput(
                context_identity=_identity("tenant-pointer", "entity-pointer"),
                value=Decimal("1"),
                value_kind="numeric",
                state="approved",
            )
        )
        second = store.append_revision(
            ValueRevisionInput(
                context_identity=_identity("tenant-pointer", "entity-pointer"),
                value=Decimal("2"),
                value_kind="numeric",
                state="approved",
            )
        )
        first_revision_id = first.revision.id
        context_id = second.context.id

    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(
                text("""
                    UPDATE current_value_pointers
                    SET revision_id = :revision_id, last_event_id = 'regression-event'
                    WHERE context_id = :context_id
                    """),
                {"revision_id": first_revision_id, "context_id": context_id},
            )
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM current_value_pointers WHERE context_id = :context_id"
                ),
                {"context_id": context_id},
            )


def test_postgres_rejects_direct_sql_promotion_of_undated_context(
    migrated_postgres: Engine,
) -> None:
    with Session(migrated_postgres) as db:
        store = ValueRevisionStore(db)
        draft = store.append_revision(
            ValueRevisionInput(
                context_identity=replace(
                    _identity("tenant-undated", "entity-undated"),
                    period_start=None,
                    period_end=None,
                ),
                value=Decimal("1"),
                value_kind="numeric",
                state="draft",
            )
        )
        store.transition_revision(revision_id=draft.revision.id, to_state="submitted")
        revision_id = draft.revision.id
    with pytest.raises(DBAPIError, match="current context without a period date"):
        with migrated_postgres.begin() as connection:
            connection.execute(
                text("""
                    UPDATE value_revisions
                    SET state = 'validated', last_state_event_id = 'invalid-direct-promotion'
                    WHERE id = :revision_id
                """),
                {"revision_id": revision_id},
            )
            connection.execute(
                text(
                    "SET CONSTRAINTS trg_value_revisions_require_current_head IMMEDIATE"
                )
            )
    with migrated_postgres.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM value_revisions WHERE id = :revision_id"),
                {"revision_id": revision_id},
            ).scalar_one()
            == "submitted"
        )


def test_postgres_rejects_nested_temp_trigger_effect_forgery(
    migrated_postgres: Engine,
) -> None:
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(text("CREATE TEMP TABLE forge_driver (id integer)"))
            connection.execute(text("""
                    CREATE FUNCTION pg_temp.forge_effect() RETURNS trigger
                    LANGUAGE plpgsql AS $$
                    BEGIN
                        INSERT INTO public.value_revision_event_effects (
                            event_id, effect_kind, tenant_id, context_id,
                            revision_id, previous_state, new_state
                        ) VALUES (
                            'event-a1', 'state_transition', 'tenant-a', 1,
                            'revision-a1', 'approved', 'voided'
                        );
                        RETURN NEW;
                    END;
                    $$
                    """))
            connection.execute(text("""
                    CREATE TRIGGER forge_effect
                    AFTER INSERT ON forge_driver
                    FOR EACH ROW EXECUTE FUNCTION pg_temp.forge_effect()
                    """))
            connection.execute(text("INSERT INTO forge_driver VALUES (1)"))


def test_postgres_rejects_temp_schema_audit_spoof(migrated_postgres: Engine) -> None:
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(text("""
                    CREATE TEMP TABLE value_revision_events (
                        id text, revision_id text, context_id bigint,
                        tenant_id text, from_state text, to_state text,
                        pointer_moved boolean
                    )
                    """))
            connection.execute(text("""
                    INSERT INTO value_revision_events VALUES (
                        'temp-event', 'revision-a1', 1, 'tenant-a',
                        'approved', 'voided', true
                    )
                    """))
            connection.execute(text("""
                    UPDATE public.value_revisions
                    SET state = 'voided', last_state_event_id = 'temp-event'
                    WHERE id = 'revision-a1'
                    """))


def test_postgres_rejects_direct_effect_forgery(migrated_postgres: Engine) -> None:
    with pytest.raises(DBAPIError):
        with migrated_postgres.begin() as connection:
            connection.execute(text("""
                    INSERT INTO value_revision_event_effects (
                        event_id, effect_kind, tenant_id, context_id, revision_id,
                        previous_state, new_state
                    ) VALUES (
                        'forged-effect', 'state_transition', 'tenant-a', 1,
                        'revision-a1', 'approved', 'voided'
                    )
                    """))
