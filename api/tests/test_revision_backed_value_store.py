from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from src.config.settings import settings
from src.database.models import (
    CurrentValuePointer,
    ESGValue,
    ReportedValuePointer,
    ValueContext,
    ValueRevision,
    ValueRevisionEvent,
)
from src.services.value_pagination import encode_value_cursor
from src.services.value_revision_backfill import backfill_esg_values_to_revisions
from src.services.value_store import DatabaseValueStore


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
        ReportedValuePointer.__table__,
    ):
        table.create(engine)
    return sessionmaker(bind=engine)()


def _set_revision_read_path(monkeypatch):
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "value_revision_default_tenant_id", "tenant-a")


def _add_current_revision(
    db,
    *,
    context_id: int,
    revision_id: str,
    source_record_id: str,
    period_end: date,
    numeric_value: Decimal,
    tenant_id: str = "tenant-a",
) -> None:
    context = ValueContext(
        id=context_id,
        tenant_id=tenant_id,
        context_hash_recipe_version="value-context-hash-v1",
        context_hash=f"{context_id:064x}"[-64:],
        entity_id="madrid_plant",
        reporting_period_id="FY2026",
        period_start=period_end,
        period_end=period_end,
        period_type="point",
        reporting_boundary_id="operational_control",
        indicator_identifier=f"urn:sds:reg:test:{source_record_id}",
        standard_release_id="ESRS_SET1_2023_12_22",
        standard_datapoint_id=f"DP-{context_id}",
        dimensions_json={},
        scenario_basis="actual",
        value_kind="numeric",
        expected_unit="m3",
    )
    revision = ValueRevision(
        id=revision_id,
        context_id=context_id,
        tenant_id=tenant_id,
        revision_number=1,
        state="approved",
        value_kind="numeric",
        canonical_value=str(numeric_value),
        numeric_value=numeric_value,
        unit="m3",
        source_record_id=source_record_id,
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        creation_event_id=f"{revision_id}-created",
    )
    pointer = CurrentValuePointer(
        context_id=context_id,
        tenant_id=tenant_id,
        revision_id=revision_id,
        last_event_id=f"{revision_id}-created",
    )
    db.add_all([context, revision, pointer])


def test_database_value_store_can_read_current_values_from_revision_pointers(
    monkeypatch,
) -> None:
    db = _session()
    db.add(
        ESGValue(
            id="legacy-value-1",
            tenant_id="tenant-a",
            ownership_state="resolved",
            concept="urn:sds:reg:esrs:e3_5_01",
            entity="madrid_plant",
            period=date(2026, 12, 31),
            external_key="erp:water:2026",
            value=Decimal("12.3000"),
            value_type="numeric",
            unit="m3",
            value_metadata={
                "standard_release_id": "ESRS_SET1_2023_12_22",
                "standard_datapoint_id": "E3-5_01",
                "reporting_period_id": "FY2026",
                "period_type": "annual",
                "reporting_boundary_id": "operational_control",
            },
        )
    )
    db.commit()
    backfill_esg_values_to_revisions(
        db=db,
        tenant_id="tenant-a",
        dry_run=False,
        created_by="tester",
    )
    _set_revision_read_path(monkeypatch)
    store = DatabaseValueStore(db, tenant_id="tenant-a")

    items, total = store.list(
        concept="urn:sds:reg:esrs:e3_5_01",
        entity="madrid_plant",
        period_start=None,
        period_end=None,
        unit=None,
        limit=10,
        offset=0,
    )
    by_id = store.get("legacy-value-1")

    assert total == 1
    assert items[0].id == "legacy-value-1"
    assert items[0].value == Decimal("12.300000000000")
    assert items[0].metadata["value_revision"]["revision_state"] == "legacy_current"
    assert by_id is not None
    assert by_id.id == "legacy-value-1"


def test_database_value_store_dual_writes_new_values_when_revision_read_path_is_active(
    monkeypatch,
) -> None:
    db = _session()
    _set_revision_read_path(monkeypatch)
    store = DatabaseValueStore(db, tenant_id="tenant-a")

    created = store.create(
        value_id="value-2",
        concept="urn:sds:reg:esrs:e1_6_07",
        entity="madrid_plant",
        period=date(2026, 12, 31),
        value=Decimal("2.5"),
        unit="tCO2e",
        original_value=None,
        original_unit=None,
        conversion_applied=False,
        metadata={
            "standard_release_id": "ESRS_SET1_2023_12_22",
            "standard_datapoint_id": "E1-6_07",
            "reporting_period_id": "FY2026",
            "period_type": "annual",
            "reporting_boundary_id": "operational_control",
        },
        created_by="tester",
    )

    assert created.id == "value-2"
    assert db.query(ESGValue).count() == 1
    assert db.query(ValueRevision).count() == 1
    assert db.query(CurrentValuePointer).count() == 1
    revision = db.query(ValueRevision).one()
    assert revision.state == "approved"
    assert revision.source_system == "sds_values"
    assert revision.external_key is None


def test_revision_read_path_preserves_conversion_response_fields(monkeypatch) -> None:
    db = _session()
    _set_revision_read_path(monkeypatch)
    store = DatabaseValueStore(db, tenant_id="tenant-a")

    created = store.create(
        value_id="value-converted",
        concept="urn:sds:reg:esrs:e1_6_07",
        entity="madrid_plant",
        period=date(2026, 12, 31),
        value=Decimal("9.5"),
        unit="tCO2e",
        original_value=Decimal("9500"),
        original_unit="kg CO2e",
        conversion_applied=True,
        currency="EUR",
        original_currency="USD",
        currency_conversion_applied=True,
        conversion_trace=[
            {"kind": "unit", "from": "kg CO2e", "to": "tCO2e"},
            {"kind": "fx", "from": "USD", "to": "EUR"},
        ],
        value_date=date(2026, 6, 30),
        metadata={
            "standard_release_id": "ESRS_SET1_2023_12_22",
            "standard_datapoint_id": "E1-6_07",
            "reporting_period_id": "FY2026",
            "period_type": "annual",
            "reporting_boundary_id": "operational_control",
        },
        created_by="tester",
        external_key="erp:converted",
    )
    revision = db.query(ValueRevision).one()

    assert created.id == "value-converted"
    assert created.conversion_applied is True
    assert created.currency_conversion_applied is True
    assert created.value_date == date(2026, 6, 30)
    assert revision.materiality_metadata["value_response"] == {
        "conversion_applied": True,
        "currency_conversion_applied": True,
        "value_date": "2026-06-30",
    }
    assert revision.source_system == "sds_values"
    assert revision.external_key == "erp:converted"


def test_revision_cursor_pagination_preserves_projected_response_id(
    monkeypatch,
) -> None:
    db = _session()
    same_period = date(2026, 12, 31)
    _add_current_revision(
        db,
        context_id=1,
        revision_id="revision-a",
        source_record_id="value-c",
        period_end=same_period,
        numeric_value=Decimal("3"),
    )
    _add_current_revision(
        db,
        context_id=2,
        revision_id="revision-b",
        source_record_id="value-b",
        period_end=same_period,
        numeric_value=Decimal("2"),
    )
    _add_current_revision(
        db,
        context_id=3,
        revision_id="revision-c",
        source_record_id="value-a",
        period_end=same_period,
        numeric_value=Decimal("1"),
    )
    db.commit()
    _set_revision_read_path(monkeypatch)
    store = DatabaseValueStore(db, tenant_id="tenant-a")

    first_items, total, first_cursor, first_has_more = store.list_cursor(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        limit=1,
        cursor=None,
    )
    second_items, _total, second_cursor, second_has_more = store.list_cursor(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        limit=1,
        cursor=first_cursor,
    )
    third_items, _total, third_cursor, third_has_more = store.list_cursor(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        limit=1,
        cursor=second_cursor,
    )

    assert total == 3
    assert first_has_more is True
    assert second_has_more is True
    assert third_has_more is False
    assert third_cursor is None
    assert [item.id for item in first_items + second_items + third_items] == [
        "value-a",
        "value-b",
        "value-c",
    ]


def test_revision_cursor_does_not_skip_shared_source_id(monkeypatch) -> None:
    db = _session()
    for index, revision_id in enumerate(("revision-b", "revision-a"), start=1):
        _add_current_revision(
            db,
            context_id=index,
            revision_id=revision_id,
            source_record_id="same-source",
            period_end=date(2026, 12, 31),
            numeric_value=Decimal(index),
        )
    db.commit()
    _set_revision_read_path(monkeypatch)
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    filters = dict(
        concept=None, entity=None, period_start=None, period_end=None, unit=None
    )
    first, total, cursor, more = store.list_cursor(**filters, limit=1, cursor=None)
    second, _, next_cursor, last = store.list_cursor(**filters, limit=1, cursor=cursor)
    assert total == 2 and more and not last and next_cursor is None
    assert [
        row.metadata["value_revision"]["revision_id"] for row in first + second
    ] == [
        "revision-b",
        "revision-a",
    ]


def test_revision_read_rejects_legacy_ambiguous_projected_cursor(monkeypatch) -> None:
    db = _session()
    _add_current_revision(
        db,
        context_id=1,
        revision_id="revision-a",
        source_record_id="source-a",
        period_end=date(2026, 12, 31),
        numeric_value=Decimal("1"),
    )
    db.commit()
    _set_revision_read_path(monkeypatch)
    cursor = encode_value_cursor(
        period=date(2026, 12, 31),
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        value_id="source-a",
    )
    with pytest.raises(ValueError, match="Invalid values cursor"):
        DatabaseValueStore(db, tenant_id="tenant-a").list_cursor(
            concept=None,
            entity=None,
            period_start=None,
            period_end=None,
            unit=None,
            limit=1,
            cursor=cursor,
        )


def test_revision_cursor_does_not_skip_null_ended_contexts(monkeypatch) -> None:
    db = _session()
    for index, period in enumerate((date(2026, 12, 31), date(2025, 12, 31)), start=1):
        _add_current_revision(
            db,
            context_id=index,
            revision_id=f"revision-{index}",
            source_record_id=f"source-{index}",
            period_end=period,
            numeric_value=Decimal(index),
        )
    for context in db.query(ValueContext):
        context.period_end = None
    db.commit()
    _set_revision_read_path(monkeypatch)
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    filters = dict(
        concept=None, entity=None, period_start=None, period_end=None, unit=None
    )
    first, total, cursor, more = store.list_cursor(**filters, limit=1, cursor=None)
    second, _, next_cursor, last = store.list_cursor(**filters, limit=1, cursor=cursor)
    assert total == 2 and more and not last and next_cursor is None
    assert [row.period for row in first + second] == [
        date(2026, 12, 31),
        date(2025, 12, 31),
    ]


def test_revision_date_filters_use_the_ordered_effective_period(monkeypatch) -> None:
    db = _session()
    _add_current_revision(
        db,
        context_id=1,
        revision_id="revision-2026",
        source_record_id="source-2026",
        period_end=date(2026, 12, 31),
        numeric_value=Decimal("1"),
    )
    context = db.query(ValueContext).one()
    context.period_end = None
    db.commit()
    _set_revision_read_path(monkeypatch)
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    for period_filter in (
        {"period_start": date(2026, 1, 1), "period_end": None},
        {"period_start": None, "period_end": date(2026, 12, 31)},
    ):
        results, total = store.list(
            concept=None,
            entity=None,
            unit=None,
            limit=10,
            offset=0,
            **period_filter,
        )
        assert total == 1
        assert results[0].period == date(2026, 12, 31)


def test_undated_current_revision_blocks_before_partial_cursor_page(
    monkeypatch,
) -> None:
    db = _session()
    for index, period in enumerate((date(2026, 12, 31), date(2025, 12, 31)), 1):
        _add_current_revision(
            db,
            context_id=index,
            revision_id=f"revision-{index}",
            source_record_id=f"source-{index}",
            period_end=period,
            numeric_value=Decimal(index),
        )
    undated = db.query(ValueContext).filter(ValueContext.id == 2).one()
    undated.period_start = None
    undated.period_end = None
    db.commit()
    _set_revision_read_path(monkeypatch)
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    with pytest.raises(RuntimeError, match="undated current value context"):
        store.list_cursor(
            concept=None,
            entity=None,
            period_start=None,
            period_end=None,
            unit=None,
            limit=1,
            cursor=None,
        )


def test_database_value_store_uses_bound_revision_tenant(monkeypatch) -> None:
    db = _session()
    _add_current_revision(
        db,
        context_id=1,
        revision_id="tenant-a-revision",
        source_record_id="tenant-a-value",
        period_end=date(2026, 12, 31),
        numeric_value=Decimal("1"),
        tenant_id="tenant-a",
    )
    _add_current_revision(
        db,
        context_id=2,
        revision_id="tenant-b-revision",
        source_record_id="tenant-b-value",
        period_end=date(2026, 12, 31),
        numeric_value=Decimal("2"),
        tenant_id="tenant-b",
    )
    db.commit()
    _set_revision_read_path(monkeypatch)
    store = DatabaseValueStore(db, tenant_id="tenant-b")

    items, total = store.list(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        limit=10,
        offset=0,
    )

    assert total == 1
    assert items[0].id == "tenant-b-value"
    assert store.get("tenant-a-value") is None
    assert store.get("tenant-b-value").id == "tenant-b-value"


def test_revision_read_path_rejects_mismatched_pointer_tenant(monkeypatch) -> None:
    db = _session()
    _add_current_revision(
        db,
        context_id=1,
        revision_id="tenant-a-revision",
        source_record_id="tenant-a-value",
        period_end=date(2026, 12, 31),
        numeric_value=Decimal("1"),
        tenant_id="tenant-a",
    )
    _add_current_revision(
        db,
        context_id=2,
        revision_id="tenant-b-revision",
        source_record_id="tenant-b-value",
        period_end=date(2026, 12, 31),
        numeric_value=Decimal("2"),
        tenant_id="tenant-b",
    )
    contaminated_pointer = (
        db.query(CurrentValuePointer).filter(CurrentValuePointer.context_id == 1).one()
    )
    contaminated_pointer.tenant_id = "tenant-b"
    db.commit()
    _set_revision_read_path(monkeypatch)
    store = DatabaseValueStore(db, tenant_id="tenant-b")

    items, total = store.list(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        limit=10,
        offset=0,
    )
    iterated = list(
        store.iterate(
            concept=None,
            entity=None,
            period_start=None,
            period_end=None,
            unit=None,
            changed_since=None,
        )
    )

    assert total == 1
    assert [item.id for item in items] == ["tenant-b-value"]
    assert [item.id for item in iterated] == ["tenant-b-value"]
