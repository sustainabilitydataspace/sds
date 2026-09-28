"""Integration tests proving bulk and per-row paths produce identical state."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from src.config.settings import settings
from src.database.models import (
    CurrentValuePointer,
    ESGValue,
    ValueContext,
    ValueRevision,
    ValueRevisionEvent,
)
from src.database.repositories.value_repository import ValueRepository
from src.services.value_ingest import PreparedValueRecord
from src.services.value_revision_backfill import revision_input_for_esg_value
from src.services.value_revision_store import ValueRevisionInput, ValueRevisionStore
from src.services.value_store import DatabaseValueStore
from src.services.value_versioning import (
    CONTEXT_HASH_RECIPE_VERSION_V2,
    SDS_CANONICAL_OPERATIONAL_RELEASE_ID,
    SOURCE_OBSERVATION_CANONICAL_OPERATIONAL,
    ValueContextIdentity,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _session():
    engine = create_engine("sqlite:///:memory:")
    for table in (
        ESGValue.__table__,
        ValueContext.__table__,
        ValueRevision.__table__,
        ValueRevisionEvent.__table__,
        CurrentValuePointer.__table__,
    ):
        table.create(engine)
    return sessionmaker(bind=engine)()


def _prepared_record(
    value_id: str,
    external_key: str | None,
    value: Decimal = Decimal("1.5"),
    concept: str = "urn:sds:reg:test:water",
) -> PreparedValueRecord:
    return PreparedValueRecord(
        value_id=value_id,
        concept=concept,
        entity="madrid_plant",
        period=date(2026, 12, 31),
        external_key=external_key,
        value=value,
        value_type="numeric",
        unit="m3",
        original_value=value,
        original_unit="m3",
        conversion_applied=False,
        metadata={
            "standard_release_id": "TEST",
            "standard_datapoint_id": concept,
            "reporting_period_id": "FY2026",
            "period_type": "annual",
            "reporting_boundary_id": "operational_control",
        },
    )


def _db_record(
    value_id: str,
    external_key: str | None,
    value: Decimal = Decimal("1.5"),
    concept: str = "urn:sds:reg:test:water",
) -> ESGValue:
    return ESGValue(
        id=value_id,
        concept=concept,
        entity="madrid_plant",
        period=date(2026, 12, 31),
        external_key=external_key,
        value=value,
        value_type="numeric",
        unit="m3",
        original_value=value,
        original_unit="m3",
        conversion_applied=False,
        value_metadata={
            "standard_release_id": "TEST",
            "standard_datapoint_id": concept,
            "reporting_period_id": "FY2026",
            "period_type": "annual",
            "reporting_boundary_id": "operational_control",
        },
    )


def test_revision_input_for_esg_value_preserves_canonical_context_metadata():
    canonical_uri = "syg:TotalEnergyConsumptionWithinOrganization"
    record = _db_record(
        "v-canonical", "erp:canonical", Decimal("832.964"), canonical_uri
    )
    record.value_metadata = {
        "canonical_uri": canonical_uri,
        "canonical_concept_id": 101,
        "source_observation_type": SOURCE_OBSERVATION_CANONICAL_OPERATIONAL,
        "context_hash_recipe_version": CONTEXT_HASH_RECIPE_VERSION_V2,
        "indicator_identifier": canonical_uri,
        "standard_release_id": SDS_CANONICAL_OPERATIONAL_RELEASE_ID,
        "standard_datapoint_id": canonical_uri,
        "reporting_period_id": "FY2024",
        "period_type": "annual",
        "reporting_boundary_id": "operational_control",
        "source_standard": "ESRS",
        "source_standard_datapoint_id": "E1-5_02",
    }

    revision_input = revision_input_for_esg_value(
        record,
        tenant_id="tenant-a",
        external_key="nordhaven:uc01:fy2024:total-energy",
        use_legacy_external_key_default=False,
        revision_provenance="api_write",
        created_by="tester",
    )

    identity = revision_input.context_identity
    assert identity.canonical_uri == canonical_uri
    assert identity.canonical_concept_id == 101
    assert identity.source_observation_type == SOURCE_OBSERVATION_CANONICAL_OPERATIONAL
    assert identity.context_hash_recipe_version == CONTEXT_HASH_RECIPE_VERSION_V2
    assert identity.indicator_identifier == canonical_uri
    assert identity.standard_release_id == SDS_CANONICAL_OPERATIONAL_RELEASE_ID
    assert identity.standard_datapoint_id == canonical_uri


def test_save_values_bulk_rejects_existing_external_keys_atomically():
    """Bulk writes must not overwrite rows that already own an external key."""
    db = _session()
    repo = ValueRepository(db)

    # First insert
    records = [
        _prepared_record("v1", "erp:1", Decimal("1.0")),
        _prepared_record("v2", "erp:2", Decimal("2.0")),
    ]
    saved = repo.save_values(records=records, created_by="tester", tenant_id="tenant-a")
    assert len(saved) == 2
    assert {r.external_key for r in saved} == {"erp:1", "erp:2"}

    original_rows = {
        row.external_key: (row.id, row.value) for row in db.query(ESGValue).all()
    }

    # Replaying either key rejects the whole batch without exposing record details.
    updated = [
        _prepared_record("v1-new", "erp:1", Decimal("10.0")),
        _prepared_record("v3", "erp:3", Decimal("30.0")),
    ]
    with pytest.raises(RuntimeError) as exc_info:
        repo.save_values(records=updated, created_by="tester", tenant_id="tenant-a")

    assert str(exc_info.value) == "External key collision during value write"
    assert "erp:1" not in str(exc_info.value)
    assert "v1-new" not in str(exc_info.value)

    persisted_rows = {
        row.external_key: (row.id, row.value) for row in db.query(ESGValue).all()
    }
    assert persisted_rows == original_rows
    assert "erp:3" not in persisted_rows
    assert db.query(ESGValue).count() == 2


def test_bulk_revision_append_matches_per_row_state():
    """Bulk revision append must produce identical context/revision/event counts."""
    db = _session()
    store = ValueRevisionStore(db)

    inputs: list[ValueRevisionInput] = []
    for idx in range(3):
        identity = ValueContextIdentity(
            tenant_id="tenant-a",
            entity_id=f"madrid_plant_{idx}",
            reporting_period_id="FY2026",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            period_close_date=None,
            period_type="annual",
            reporting_boundary_id="operational_control",
            sds_indicator_id=None,
            indicator_identifier="urn:sds:reg:test:water",
            standard_release_id="TEST",
            standard_datapoint_id="urn:sds:reg:test:water",
            dimensions={},
            expected_unit="m3",
            expected_currency=None,
            value_kind="numeric",
        )
        inputs.append(
            ValueRevisionInput(
                context_identity=identity,
                value=Decimal(str(idx + 1)),
                value_kind="numeric",
                unit="m3",
                state="approved",
                source_system="sds_values",
                external_key=f"erp:{idx}",
                source_payload_hash=f"hash-{idx}",
                created_by="tester",
            )
        )

    # Bulk append
    results = store.append_revisions_bulk(inputs, commit=True)
    assert len(results) == 3

    # Verify counts
    assert db.query(ValueContext).count() == 3
    assert db.query(ValueRevision).count() == 3
    assert db.query(ValueRevisionEvent).count() == 3
    assert db.query(CurrentValuePointer).count() == 3

    # Verify sequence monotonicity
    tenant_seqs = [e.event_seq for e in db.query(ValueRevisionEvent).all()]
    assert sorted(tenant_seqs) == tenant_seqs
    assert len(set(tenant_seqs)) == 3

    # Upsert same contexts with new values (same external_key, different payload)
    inputs2: list[ValueRevisionInput] = []
    for idx in range(3):
        identity = ValueContextIdentity(
            tenant_id="tenant-a",
            entity_id=f"madrid_plant_{idx}",
            reporting_period_id="FY2026",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            period_close_date=None,
            period_type="annual",
            reporting_boundary_id="operational_control",
            sds_indicator_id=None,
            indicator_identifier="urn:sds:reg:test:water",
            standard_release_id="TEST",
            standard_datapoint_id="urn:sds:reg:test:water",
            dimensions={},
            expected_unit="m3",
            expected_currency=None,
            value_kind="numeric",
        )
        inputs2.append(
            ValueRevisionInput(
                context_identity=identity,
                value=Decimal(str(idx + 10)),
                value_kind="numeric",
                unit="m3",
                state="approved",
                source_system="sds_values",
                external_key=f"erp:{idx}",
                source_payload_hash=f"hash-new-{idx}",
                created_by="tester",
            )
        )

    results2 = store.append_revisions_bulk(inputs2, commit=True)
    assert len(results2) == 3

    # Contexts unchanged, revisions doubled, events doubled
    assert db.query(ValueContext).count() == 3
    assert db.query(ValueRevision).count() == 6
    assert db.query(ValueRevisionEvent).count() == 6
    assert db.query(CurrentValuePointer).count() == 3

    # Pointers point to latest revisions
    for r in results2:
        pointer = (
            db.query(CurrentValuePointer)
            .filter(CurrentValuePointer.context_id == r.context.id)
            .first()
        )
        assert pointer.revision_id == r.revision.id


def test_unchanged_replay_skips_events_in_bulk_mode():
    """Re-importing identical payload must not create new revisions or events."""
    db = _session()
    store = ValueRevisionStore(db)
    record = _db_record("v1", "erp:same", Decimal("5.0"))

    inp = revision_input_for_esg_value(
        record,
        tenant_id="tenant-a",
        state="approved",
        source_system="sds_values",
        external_key=record.external_key,
        use_legacy_external_key_default=False,
        revision_provenance="api_write",
        created_by="tester",
    )

    # First append
    r1 = store.append_revision(inp, commit=True)
    assert db.query(ValueRevision).count() == 1
    assert db.query(ValueRevisionEvent).count() == 1

    # DatabaseValueStore-level unchanged skip (uses _current_revisions_with_same_payloads)
    value_store = DatabaseValueStore(db, tenant_id="tenant-a")
    revision = value_store._append_revision_for_record(record, created_by="tester")
    assert revision.id == r1.revision.id
    assert db.query(ValueRevision).count() == 1
    assert db.query(ValueRevisionEvent).count() == 1


def test_revision_read_save_with_refresh_false_loads_response_context(monkeypatch):
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    db = _session()
    store = DatabaseValueStore(db, tenant_id="tenant-a")

    responses = store.save(
        records=[_prepared_record("value-1", "erp:response-context")],
        created_by="tester",
        refresh=False,
    )

    assert len(responses) == 1
    assert responses[0].id == "value-1"
    assert responses[0].entity == "madrid_plant"
    assert responses[0].period == date(2026, 12, 31)


def test_event_sequence_monotonicity_under_bulk_load():
    """Event sequences must be gap-free and monotonic within a tenant."""
    db = _session()
    store = ValueRevisionStore(db)

    inputs: list[ValueRevisionInput] = []
    for idx in range(5):
        identity = ValueContextIdentity(
            tenant_id="tenant-b",
            entity_id="madrid_plant",
            reporting_period_id=f"FY{2026 + idx}",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            period_close_date=None,
            period_type="annual",
            reporting_boundary_id="operational_control",
            sds_indicator_id=None,
            indicator_identifier="urn:sds:reg:test:water",
            standard_release_id="TEST",
            standard_datapoint_id="urn:sds:reg:test:water",
            dimensions={},
            expected_unit="m3",
            expected_currency=None,
            value_kind="numeric",
        )
        inputs.append(
            ValueRevisionInput(
                context_identity=identity,
                value=Decimal(str(idx + 1)),
                value_kind="numeric",
                unit="m3",
                state="approved",
                source_system="sds_values",
                external_key=f"erp:{idx}",
                source_payload_hash=f"hash-{idx}",
                created_by="tester",
            )
        )

    store.append_revisions_bulk(inputs, commit=True)

    events = (
        db.query(ValueRevisionEvent)
        .filter(ValueRevisionEvent.tenant_id == "tenant-b")
        .order_by(ValueRevisionEvent.event_seq)
        .all()
    )
    seqs = [e.event_seq for e in events]
    assert seqs == [1, 2, 3, 4, 5]

    context_seqs = {}
    for e in events:
        context_seqs.setdefault(e.context_id, []).append(e.context_event_seq)
    for seq_list in context_seqs.values():
        assert seq_list == [1]

    # Second bulk for same contexts
    inputs2 = []
    for idx in range(5):
        identity = ValueContextIdentity(
            tenant_id="tenant-b",
            entity_id="madrid_plant",
            reporting_period_id=f"FY{2026 + idx}",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            period_close_date=None,
            period_type="annual",
            reporting_boundary_id="operational_control",
            sds_indicator_id=None,
            indicator_identifier="urn:sds:reg:test:water",
            standard_release_id="TEST",
            standard_datapoint_id="urn:sds:reg:test:water",
            dimensions={},
            expected_unit="m3",
            expected_currency=None,
            value_kind="numeric",
        )
        inputs2.append(
            ValueRevisionInput(
                context_identity=identity,
                value=Decimal(str(idx + 10)),
                value_kind="numeric",
                unit="m3",
                state="approved",
                source_system="sds_values",
                external_key=f"erp:{idx}",
                source_payload_hash=f"hash-new-{idx}",
                created_by="tester",
            )
        )

    store.append_revisions_bulk(inputs2, commit=True)

    events2 = (
        db.query(ValueRevisionEvent)
        .filter(ValueRevisionEvent.tenant_id == "tenant-b")
        .order_by(ValueRevisionEvent.event_seq)
        .all()
    )
    seqs2 = [e.event_seq for e in events2]
    assert seqs2 == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    context_seqs2 = {}
    for e in events2:
        context_seqs2.setdefault(e.context_id, []).append(e.context_event_seq)
    for seq_list in context_seqs2.values():
        assert seq_list == [1, 2]


def test_bulk_append_uses_grouped_sequence_max_queries():
    """Bulk sequence allocation must not issue one MAX query per context."""
    db = _session()
    store = ValueRevisionStore(db)
    statements: list[str] = []
    engine = db.get_bind()

    def capture_statement(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ):
        if "MAX(" in statement.upper():
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        inputs: list[ValueRevisionInput] = []
        for idx in range(6):
            identity = ValueContextIdentity(
                tenant_id=f"tenant-{idx % 2}",
                entity_id="madrid_plant",
                reporting_period_id=f"FY{2026 + idx}",
                period_start=date(2026, 1, 1),
                period_end=date(2026, 12, 31),
                period_close_date=None,
                period_type="annual",
                reporting_boundary_id="operational_control",
                sds_indicator_id=None,
                indicator_identifier="urn:sds:reg:test:water",
                standard_release_id="TEST",
                standard_datapoint_id="urn:sds:reg:test:water",
                dimensions={},
                expected_unit="m3",
                expected_currency=None,
                value_kind="numeric",
            )
            inputs.append(
                ValueRevisionInput(
                    context_identity=identity,
                    value=Decimal(str(idx + 1)),
                    value_kind="numeric",
                    unit="m3",
                    state="approved",
                    source_system="sds_values",
                    external_key=f"erp:grouped:{idx}",
                    source_payload_hash=f"hash-grouped-{idx}",
                    created_by="tester",
                )
            )

        store.append_revisions_bulk(inputs, commit=True)
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert len(statements) == 2
    assert all("GROUP BY" in statement.upper() for statement in statements)
    for tenant_id in ("tenant-0", "tenant-1"):
        tenant_events = (
            db.query(ValueRevisionEvent)
            .filter(ValueRevisionEvent.tenant_id == tenant_id)
            .order_by(ValueRevisionEvent.event_seq)
            .all()
        )
        assert [event.event_seq for event in tenant_events] == [1, 2, 3]
        assert tenant_events[0].previous_event_hash is None
        assert all(
            event.previous_event_hash == previous.event_hash
            for previous, event in zip(tenant_events, tenant_events[1:])
        )
