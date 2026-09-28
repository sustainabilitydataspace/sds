from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from src.config.settings import settings
from src.services.change_feed import ChangeFeedCursor
from src.services.value_ingest import PreparedValueRecord
from src.services.value_store import (
    DatabaseValueStore,
    InMemoryValueStore,
    _concept_or_source_standard_filter,
    _infer_value_type,
    _metadata_bool,
    _metadata_date,
    _record_value,
    _revision_value,
    get_value_store,
    resolve_value_store_tenant_id,
    revision_value_store_needs_tenant,
)


def _record(
    value_id: str,
    *,
    concept: str = "csrd:E3_5",
    entity: str = "plant-a",
    period: date = date(2026, 1, 31),
    value: Decimal = Decimal("1"),
    unit: str = "L",
    external_key: str | None = None,
) -> PreparedValueRecord:
    return PreparedValueRecord(
        value_id=value_id,
        concept=concept,
        entity=entity,
        period=period,
        value=value,
        value_type="numeric",
        unit=unit,
        original_value=value,
        original_unit=unit,
        conversion_applied=False,
        metadata={"source": "test"},
        external_key=external_key,
    )


@pytest.mark.parametrize("commit", [True, False])
@pytest.mark.parametrize("method", ["create", "bulk_create", "save"])
def test_in_memory_writes_accept_transaction_owner_option(method, commit):
    store = InMemoryValueStore()
    record = _record("value-commit", external_key="row-commit")
    if method == "create":
        response = store.create(**vars(record), created_by="tester", commit=commit)
    else:
        response = getattr(store, method)(
            records=[record], created_by="tester", commit=commit
        )[0]
    assert response.id == record.value_id
    assert store.get(record.value_id) == response


def test_in_memory_value_store_save_rejects_external_key_collisions_atomically() -> (
    None
):
    store = InMemoryValueStore()

    first = store.save(
        records=[_record("value-1", external_key="row-1", value=Decimal("1"))],
        created_by="tester",
    )[0]

    with pytest.raises(HTTPException) as existing_collision:
        store.save(
            records=[
                _record("value-2", external_key="row-2", value=Decimal("2")),
                _record("value-3", external_key="row-1", value=Decimal("3")),
            ],
            created_by="tester",
        )

    assert existing_collision.value.status_code == 409
    assert existing_collision.value.detail == "Value external key already exists"
    assert set(store._values) == {"value-1"}
    assert store.get("value-1") == first
    assert store.get("value-1").value == Decimal("1")

    with pytest.raises(HTTPException) as batch_collision:
        store.save(
            records=[
                _record("value-4", external_key="row-4", value=Decimal("4")),
                _record("value-5", external_key="row-4", value=Decimal("5")),
            ],
            created_by="tester",
        )

    assert batch_collision.value.status_code == 409
    assert batch_collision.value.detail == "Value external key already exists"
    assert set(store._values) == {"value-1"}


def test_in_memory_value_store_filters_list_iterate_and_cursor_pages() -> None:
    store = InMemoryValueStore()
    store.bulk_create(
        records=[
            _record(
                "water-new",
                concept="csrd:E3_5",
                entity="plant-a",
                period=date(2026, 2, 28),
                value=Decimal("2"),
                unit="L",
            ),
            _record(
                "water-old",
                concept="csrd:E3_5",
                entity="plant-a",
                period=date(2026, 1, 31),
                value=Decimal("1"),
                unit="L",
            ),
            _record(
                "energy",
                concept="csrd:E1_5",
                entity="plant-b",
                period=date(2026, 2, 28),
                value=Decimal("100"),
                unit="kWh",
            ),
        ],
        created_by="tester",
    )
    changed_since = min(value.updated_at for value in store._values.values())

    listed, total = store.list(
        concept="csrd:E3_5",
        entity="plant-a",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        unit="L",
        changed_since=changed_since,
        limit=10,
        offset=0,
    )
    assert [value.id for value in listed] == ["water-new", "water-old"]
    assert total == 2

    first_page, total, cursor, has_more = store.list_cursor(
        concept="csrd:E3_5",
        entity="plant-a",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        unit="L",
        changed_since=changed_since,
        limit=1,
        cursor=None,
    )
    assert [value.id for value in first_page] == ["water-new"]
    assert total == 2
    assert cursor is not None
    assert has_more is True

    second_page, total, next_cursor, has_more = store.list_cursor(
        concept="csrd:E3_5",
        entity="plant-a",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        unit="L",
        changed_since=changed_since,
        limit=1,
        cursor=cursor,
    )
    assert [value.id for value in second_page] == ["water-old"]
    assert total == 2
    assert next_cursor is None
    assert has_more is False

    iterated = list(
        store.iterate(
            concept="csrd:E3_5",
            entity="plant-a",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            unit="L",
            changed_since=changed_since,
        )
    )
    assert [value.id for value in iterated] == ["water-new", "water-old"]


def test_in_memory_value_store_change_feed_cursor_and_delete() -> None:
    store = InMemoryValueStore()
    store.bulk_create(
        records=[
            _record("first", period=date(2026, 1, 1), unit="L"),
            _record("second", period=date(2026, 1, 2), unit="L"),
            _record("other-unit", period=date(2026, 1, 3), unit="kWh"),
        ],
        created_by="tester",
    )
    changed_since = min(value.updated_at for value in store._values.values())

    changes, cursor_payload, has_more = store.list_changes(
        concept="csrd:E3_5",
        entity="plant-a",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        unit="L",
        changed_since=changed_since,
        limit=1,
        cursor=None,
    )
    assert len(changes) == 1
    assert cursor_payload is not None
    assert has_more is True

    cursor = ChangeFeedCursor(
        occurred_at=datetime.fromisoformat(cursor_payload[0]),
        dataset="values",
        event_id=cursor_payload[1],
    )
    next_changes, next_cursor, has_more = store.list_changes(
        concept="csrd:E3_5",
        entity="plant-a",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        unit="L",
        changed_since=changed_since,
        limit=5,
        cursor=cursor,
    )
    assert [value.id for value in next_changes] == ["second"]
    assert next_cursor is None
    assert has_more is False

    assert store.delete("first") is True
    assert store.delete("missing") is False
    assert store.get("first") is None


def test_in_memory_value_store_change_feed_no_duplicates_or_omissions_under_interleaved_writes() -> (
    None
):
    """Paging through changes while new rows are inserted must not skip or duplicate."""
    store = InMemoryValueStore()
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Seed rows with identical updated_at to stress tie-break logic
    store.bulk_create(
        records=[
            _record("v1", period=date(2026, 1, 1), unit="L"),
            _record("v2", period=date(2026, 1, 2), unit="L"),
            _record("v3", period=date(2026, 1, 3), unit="L"),
        ],
        created_by="tester",
    )
    # Force identical updated_at for v1 and v2 to stress (updated_at, id) ordering
    store._values["v1"] = store._values["v1"].__class__(
        **{**store._values["v1"].__dict__, "updated_at": base_time}
    )
    store._values["v2"] = store._values["v2"].__class__(
        **{**store._values["v2"].__dict__, "updated_at": base_time}
    )
    store._values["v3"] = store._values["v3"].__class__(
        **{**store._values["v3"].__dict__, "updated_at": base_time}
    )

    # Page 1
    changes1, cursor1, has_more1 = store.list_changes(
        concept="csrd:E3_5",
        entity="plant-a",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        unit="L",
        changed_since=base_time,
        limit=1,
        cursor=None,
    )
    assert [v.id for v in changes1] == ["v1"]
    assert has_more1 is True

    # Interleave a new row with updated_at between v1 and v2 (same timestamp, higher id)
    store.create(
        value_id="v1_5",
        concept="csrd:E3_5",
        entity="plant-a",
        period=date(2026, 1, 1),
        value=Decimal("5"),
        unit="L",
        original_unit="L",
        conversion_applied=False,
        metadata={},
    )
    store._values["v1_5"] = store._values["v1_5"].__class__(
        **{**store._values["v1_5"].__dict__, "updated_at": base_time}
    )

    # Page 2 using cursor from page 1
    cursor = ChangeFeedCursor(
        occurred_at=datetime.fromisoformat(cursor1[0]),
        dataset="values",
        event_id=cursor1[1],
    )
    changes2, cursor2, has_more2 = store.list_changes(
        concept="csrd:E3_5",
        entity="plant-a",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        unit="L",
        changed_since=base_time,
        limit=2,
        cursor=cursor,
    )
    # v1_5 and v2 should appear; v3 remains
    assert [v.id for v in changes2] == ["v1_5", "v2"]
    assert has_more2 is True

    # Page 3
    cursor = ChangeFeedCursor(
        occurred_at=datetime.fromisoformat(cursor2[0]),
        dataset="values",
        event_id=cursor2[1],
    )
    changes3, cursor3, has_more3 = store.list_changes(
        concept="csrd:E3_5",
        entity="plant-a",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        unit="L",
        changed_since=base_time,
        limit=2,
        cursor=cursor,
    )
    assert [v.id for v in changes3] == ["v3"]
    assert has_more3 is False
    assert cursor3 is None

    # Overall no duplicates or omissions
    all_ids = [v.id for v in changes1 + changes2 + changes3]
    assert sorted(all_ids) == ["v1", "v1_5", "v2", "v3"]


def _db_record(value_id: str = "db-value", **overrides):
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    data = {
        "id": value_id,
        "concept": "csrd:E3_5",
        "entity": "plant-a",
        "period": date(2026, 1, 31),
        "external_key": "external-1",
        "value": Decimal("1.5"),
        "boolean_value": None,
        "text_value": None,
        "original_value": Decimal("1.5"),
        "value_type": "numeric",
        "unit": "m3",
        "original_unit": "m3",
        "conversion_applied": False,
        "currency": None,
        "original_currency": None,
        "currency_conversion_applied": False,
        "conversion_trace": None,
        "value_date": None,
        "period_start": None,
        "period_end": None,
        "value_metadata": {"source": "repo"},
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _revision_response(value_id: str, *, period: date = date(2026, 1, 31)):
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    return SimpleNamespace(id=value_id, period=period, created_at=now, updated_at=now)


def test_database_value_store_legacy_read_write_paths_fail_before_repository(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "legacy")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", False)

    store = DatabaseValueStore(MagicMock())
    repo = MagicMock()
    store._repo = repo

    filters = {
        "concept": None,
        "entity": None,
        "period_start": None,
        "period_end": None,
        "unit": None,
        "changed_since": None,
    }
    operations = [
        lambda: store.create(
            value_id="db-value",
            concept="csrd:E3_5",
            entity="plant-a",
            period=date(2026, 1, 31),
            value=Decimal("1.5"),
            unit="m3",
            original_value=Decimal("1.5"),
            original_unit="m3",
            conversion_applied=False,
            metadata={"source": "test"},
            created_by="tester",
        ),
        lambda: store.get("db-value"),
        lambda: store.bulk_create(records=[_record("bulk")], created_by="tester"),
        lambda: store.save(records=[_record("save")], created_by="tester"),
        lambda: store.list(**filters, limit=10, offset=0),
        lambda: store.list_cursor(**filters, limit=10, cursor=None),
        lambda: store.latest(
            concept="csrd:E3_5",
            entity="plant-a",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        ),
        lambda: list(store.iterate(**filters)),
        lambda: store.list_changes(**filters, limit=10, cursor=None),
        lambda: store.delete("db-value"),
    ]

    for operation in operations:
        with pytest.raises(HTTPException) as unavailable:
            operation()
        assert unavailable.value.status_code == 503
        assert unavailable.value.detail == "Value service unavailable"

    assert repo.mock_calls == []


def test_database_value_store_revision_paths_page_iterate_and_changefeed(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    db = MagicMock()
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    revisions = [
        _revision_response("revision-a", period=date(2026, 2, 28)),
        _revision_response("revision-b", period=date(2026, 1, 31)),
    ]
    store._to_revision_response = lambda revision: revision
    store._get_current_revision = MagicMock(side_effect=[revisions[0], None])
    store._list_current_revisions = MagicMock(
        side_effect=[(revisions, 2), ([revisions[0]], 1), ([], 1)]
    )
    store._list_current_revision_page = MagicMock(side_effect=[[revisions[0]], []])
    store._list_current_revisions_after_cursor = MagicMock(
        return_value=([revisions[1]], 2)
    )
    store._list_current_revisions_after_change_cursor = MagicMock(
        return_value=(revisions, 2)
    )

    assert store.get("revision-a").id == "revision-a"
    assert store.get("missing") is None
    first_page, total, cursor, has_more = store.list_cursor(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        changed_since=None,
        limit=1,
        cursor=None,
    )
    assert [item.id for item in first_page] == ["revision-a"]
    assert total == 2
    assert cursor is not None
    assert has_more is True
    second_page, total, next_cursor, has_more = store.list_cursor(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        changed_since=None,
        limit=1,
        cursor=cursor,
    )
    assert [item.id for item in second_page] == ["revision-b"]
    assert total == 2
    assert next_cursor is None
    assert has_more is False
    assert [
        item.id
        for item in store.iterate(
            concept=None,
            entity=None,
            period_start=None,
            period_end=None,
            unit=None,
            changed_since=None,
        )
    ] == ["revision-a"]
    assert store._list_current_revision_page.call_count == 2
    changes, change_cursor, has_more = store.list_changes(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        changed_since=None,
        limit=1,
        cursor=ChangeFeedCursor(
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            dataset="values",
            event_id="revision-a",
        ),
    )
    assert [item.id for item in changes] == ["revision-a"]
    assert change_cursor == (revisions[0].updated_at.isoformat(), "revision-a")
    assert has_more is True


def test_database_value_store_export_snapshot_is_one_bounded_query(monkeypatch):
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    store = DatabaseValueStore(MagicMock(), tenant_id="tenant-a")
    first = _revision_response("revision-a")
    store._list_current_revision_page = MagicMock(return_value=[first])
    store._to_revision_response = lambda revision: revision

    result = store.iterate_export_snapshot(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        changed_since=None,
        limit=501,
    )
    assert [row.id for row in result] == ["revision-a"]
    store._list_current_revision_page.assert_called_once()
    assert store._list_current_revision_page.call_args.kwargs["limit"] == 501
    assert store._list_current_revision_page.call_args.kwargs["offset"] == 0

    with pytest.raises(ValueError, match="out of bounds"):
        store.iterate_export_snapshot(
            concept=None,
            entity=None,
            period_start=None,
            period_end=None,
            unit=None,
            changed_since=None,
            limit=50001,
        )
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "legacy")
    with pytest.raises(HTTPException) as unavailable:
        store.iterate_export_snapshot(
            concept=None,
            entity=None,
            period_start=None,
            period_end=None,
            unit=None,
            changed_since=None,
            limit=1,
        )
    assert unavailable.value.status_code == 503


def test_database_value_store_reads_calculation_snapshot_in_one_bounded_query(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    db = MagicMock()
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    revision = _revision_response("revision-a")
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = [revision]
    store._current_revision_query = MagicMock(return_value=query)
    store._apply_revision_filters = MagicMock(return_value=query)
    store._with_context_eager_loading = MagicMock(return_value=query)
    store._to_revision_response = lambda revision: revision

    rows = list(
        store.iterate_snapshot(
            concept="urn:sds:energy",
            entities=["facility", "warehouse"],
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            unit=None,
            changed_since=None,
            limit=101,
        )
    )

    assert [row.id for row in rows] == ["revision-a"]
    store._apply_revision_filters.assert_called_once()
    query.filter.assert_called_once()
    query.limit.assert_called_once_with(101)
    query.all.assert_called_once_with()

    with pytest.raises(ValueError, match="positive"):
        store.iterate_snapshot(
            concept=None,
            entities=["facility"],
            period_start=None,
            period_end=None,
            unit=None,
            changed_since=None,
            limit=0,
        )
    assert (
        store.iterate_snapshot(
            concept=None,
            entities=[],
            period_start=None,
            period_end=None,
            unit=None,
            changed_since=None,
            limit=1,
        )
        == []
    )


def test_database_value_store_revision_write_paths_commit_and_rollback(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    db = MagicMock()
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    record = _db_record()
    revision = _revision_response("revision-a")
    store._repo = MagicMock()
    store._repo.create_value.return_value = record
    store._repo.create_values.return_value = [record]
    store._repo.save_values.return_value = [record]
    store._append_revision_for_record = MagicMock(return_value=revision)
    store._append_revisions_for_records = MagicMock(return_value=[revision])
    store._to_revision_response = lambda item: item

    assert (
        store.create(
            value_id="db-value",
            concept="csrd:E3_5",
            entity="plant-a",
            period=date(2026, 1, 31),
            value=Decimal("1.5"),
            unit="m3",
            original_value=None,
            original_unit=None,
            conversion_applied=False,
            metadata={},
            created_by="tester",
        ).id
        == "revision-a"
    )
    assert (
        store.bulk_create(records=[_record("bulk")], created_by="tester")[0].id
        == "revision-a"
    )
    assert (
        store.save(records=[_record("save")], created_by="tester")[0].id == "revision-a"
    )
    assert db.commit.call_count == 3
    assert db.refresh.call_count == 6

    failing = DatabaseValueStore(db, tenant_id="tenant-a")
    failing._repo = MagicMock()
    failing._repo.create_value.side_effect = RuntimeError("write failed")
    with pytest.raises(RuntimeError, match="write failed"):
        failing.create(
            value_id="bad",
            concept="csrd:E3_5",
            entity="plant-a",
            period=date(2026, 1, 31),
            value=Decimal("1.5"),
            unit="m3",
            original_value=None,
            original_unit=None,
            conversion_applied=False,
            metadata={},
            created_by="tester",
        )
    assert db.rollback.called is True

    db.rollback.reset_mock()
    failing._repo.create_values.side_effect = RuntimeError("bulk failed")
    with pytest.raises(RuntimeError, match="bulk failed"):
        failing.bulk_create(records=[_record("bad-bulk")], created_by="tester")
    db.rollback.assert_called_once()

    db.rollback.reset_mock()
    failing._repo.save_values.side_effect = RuntimeError("save failed")
    with pytest.raises(RuntimeError, match="save failed"):
        failing.save(records=[_record("bad-save")], created_by="tester")
    db.rollback.assert_called_once()


@pytest.mark.parametrize("method", ("create", "bulk_create", "save"))
def test_revision_tenant_lock_precedes_legacy_value_write(monkeypatch, method):
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    store = DatabaseValueStore(MagicMock(), tenant_id="tenant-a")
    store._repo = MagicMock()
    events = []
    store._revision_store._acquire_bulk_append_locks = lambda tenant_ids: events.append(
        ("tenant-lock", tenant_ids)
    )

    def abort_after_record_write(**_kwargs):
        events.append(("legacy-write", None))
        raise RuntimeError("stop before revision append")

    getattr(
        store._repo,
        {
            "create": "create_value",
            "bulk_create": "create_values",
            "save": "save_values",
        }[method],
    ).side_effect = abort_after_record_write
    with pytest.raises(RuntimeError, match="stop before revision append"):
        if method == "create":
            store.create(
                value_id="value-a",
                concept="test:energy",
                entity="site",
                period=date(2024, 1, 1),
                value=Decimal("1"),
                unit="MWh",
                original_unit=None,
                conversion_applied=False,
                metadata=None,
                created_by="test",
            )
        else:
            getattr(store, method)(records=[_record("value-a")], created_by="test")
    assert events == [("tenant-lock", {"tenant-a"}), ("legacy-write", None)]


def test_database_value_store_revision_writes_can_defer_commit(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    db = MagicMock()
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    record = _db_record()
    revision = _revision_response("revision-a")
    store._repo = MagicMock()
    store._repo.create_value.return_value = record
    store._repo.create_values.return_value = [record]
    store._repo.save_values.return_value = [record]
    store._append_revision_for_record = MagicMock(return_value=revision)
    store._append_revisions_for_records = MagicMock(return_value=[revision])
    store._to_revision_response = lambda item: item

    created = store.create(
        value_id="db-value",
        concept="csrd:E3_5",
        entity="plant-a",
        period=date(2026, 1, 31),
        value=Decimal("1.5"),
        unit="m3",
        original_value=None,
        original_unit=None,
        conversion_applied=False,
        metadata={},
        created_by="tester",
        commit=False,
    )
    bulk_created = store.bulk_create(
        records=[_record("bulk")],
        created_by="tester",
        commit=False,
    )
    saved = store.save(
        records=[_record("save")],
        created_by="tester",
        commit=False,
    )

    assert created.id == "revision-a"
    assert [item.id for item in bulk_created] == ["revision-a"]
    assert [item.id for item in saved] == ["revision-a"]
    assert db.flush.call_count == 3
    assert db.refresh.call_count == 6
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    db.close.assert_not_called()
    assert store._repo.create_value.call_args.kwargs["commit"] is False
    assert store._repo.create_values.call_args.kwargs["commit"] is False
    assert store._repo.save_values.call_args.kwargs["commit"] is False
    store._append_revision_for_record.assert_called_once_with(
        record,
        created_by="tester",
    )
    assert store._append_revisions_for_records.call_count == 2


@pytest.mark.parametrize("operation", ["create", "bulk_create", "save"])
def test_database_value_store_deferred_write_errors_do_not_rollback(
    monkeypatch,
    operation: str,
) -> None:
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    db.add.side_effect = RuntimeError("write failed")
    store = DatabaseValueStore(db, tenant_id="tenant-a")

    if operation == "create":

        def invoke():
            return store.create(
                value_id="bad",
                concept="csrd:E3_5",
                entity="plant-a",
                period=date(2026, 1, 31),
                value=Decimal("1.5"),
                unit="m3",
                original_value=None,
                original_unit=None,
                conversion_applied=False,
                metadata={},
                created_by="tester",
                commit=False,
            )

    elif operation == "bulk_create":

        def invoke():
            return store.bulk_create(
                records=[_record("bad-bulk")],
                created_by="tester",
                commit=False,
            )

    else:

        def invoke():
            return store.save(
                records=[_record("bad-save")],
                created_by="tester",
                commit=False,
            )

    with pytest.raises(RuntimeError, match="write failed"):
        invoke()

    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    db.close.assert_not_called()


def test_database_value_store_deferred_bulk_raw_return_preserves_shape(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    db = MagicMock()
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    record = _db_record()
    revision = _revision_response("revision-a")
    store._repo = MagicMock()
    store._repo.create_values.return_value = [record]
    store._repo.save_values.return_value = [record]
    store._append_revisions_for_records = MagicMock(return_value=[revision])

    assert store.bulk_create(
        records=[_record("bulk")],
        created_by="tester",
        refresh=False,
        return_responses=False,
        commit=False,
    ) == [record]
    assert store.save(
        records=[_record("save")],
        created_by="tester",
        refresh=False,
        return_responses=False,
        commit=False,
    ) == [record]
    assert db.flush.call_count == 2
    db.refresh.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    db.close.assert_not_called()


def test_database_value_store_revision_write_with_legacy_readback_returns_records(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "legacy")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    db = MagicMock()
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    record = _db_record()
    revision = _revision_response("revision-a")
    store._repo = MagicMock()
    store._repo.create_value.return_value = record
    store._repo.create_values.return_value = [record]
    store._repo.save_values.return_value = [record]
    store._append_revision_for_record = MagicMock(return_value=revision)

    assert (
        store.create(
            value_id="db-value",
            concept="csrd:E3_5",
            entity="plant-a",
            period=date(2026, 1, 31),
            value=Decimal("1.5"),
            unit="m3",
            original_value=None,
            original_unit=None,
            conversion_applied=False,
            metadata={},
            created_by="tester",
        ).id
        == "db-value"
    )
    assert (
        store.bulk_create(records=[_record("bulk")], created_by="tester")[0].id
        == "db-value"
    )
    assert (
        store.save(records=[_record("save")], created_by="tester")[0].id == "db-value"
    )


def test_database_value_store_revision_filter_and_change_cursor_branches(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    db = MagicMock()
    store = DatabaseValueStore(db, tenant_id="tenant-a")
    query = MagicMock()
    query.filter.return_value = query
    query.count.return_value = 1
    query.options.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = ["revision"]
    store._current_revision_query = MagicMock(return_value=query)

    filtered = store._apply_revision_filters(
        query,
        concept="csrd:E3_5",
        entity="plant-a",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        unit="m3",
        changed_since=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert filtered is query
    assert query.filter.call_count == 6

    revisions, total = store._list_current_revisions_after_change_cursor(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        changed_since=None,
        limit=5,
        cursor_changed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        cursor_value_id="revision-a",
    )
    assert revisions == ["revision"]
    assert total == 1

    query.count.reset_mock()
    page = store._list_current_revision_page(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        changed_since=None,
        limit=5,
        offset=0,
    )
    assert page == ["revision"]
    query.count.assert_not_called()


def test_revision_concept_filter_matches_source_standard_datapoint_metadata() -> None:
    compiled = str(
        _concept_or_source_standard_filter("urn:sds:reg:esrs:e1_5_12").compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "indicator_identifier" in compiled
    assert "source_standard_datapoint_id" in compiled
    assert "urn:sds:reg:esrs:e1_5_12" in compiled


def test_database_value_store_revision_response_uses_reporting_period_fallback() -> (
    None
):
    store = DatabaseValueStore(MagicMock())
    revision = SimpleNamespace(
        id="revision-a",
        revision_number=2,
        state="approved",
        source_system="test",
        source_record_id=None,
        external_key="external-1",
        value_kind="numeric",
        numeric_value=Decimal("7.5"),
        canonical_value=None,
        boolean_value=None,
        text_value=None,
        original_value=Decimal("7.5"),
        unit="m3",
        original_unit="L",
        currency=None,
        original_currency=None,
        conversion_trace=[{"step": "converted"}],
        materiality_metadata={"value_response": {"value_date": "2026-01-15"}},
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        context=SimpleNamespace(
            id="context-a",
            context_hash="hash-a",
            indicator_identifier="csrd:E3_5",
            entity_id="plant-a",
            period_start=None,
            period_end=None,
            reporting_period_id="2026-01-31",
            standard_release_id="ESRS_SET1_2023_12_22",
            standard_datapoint_id="E3-5",
        ),
    )

    with pytest.raises(RuntimeError, match="undated current value context"):
        store._to_revision_response(revision)
    revision.context.period_end = date(2026, 1, 31)
    response = store._to_revision_response(revision)

    assert response.id == "revision-a"
    assert response.period == date(2026, 1, 31)
    assert response.value == Decimal("7.5")
    assert response.conversion_applied is True
    assert response.value_date == date(2026, 1, 15)


def test_value_store_dependency_and_scalar_helpers(monkeypatch) -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    monkeypatch.setattr(settings, "require_database", False)
    assert get_value_store(request=request, db=None) is get_value_store(
        request=request, db=None
    )

    monkeypatch.setattr(settings, "require_database", True)
    with pytest.raises(HTTPException) as unavailable:
        get_value_store(request=request, db=None)
    assert unavailable.value.status_code == 503
    assert isinstance(
        get_value_store(request=request, db=MagicMock()), DatabaseValueStore
    )

    monkeypatch.setattr(settings, "value_revision_primary_read_path", "legacy")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", False)
    assert revision_value_store_needs_tenant() is False
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", True)
    assert revision_value_store_needs_tenant() is True

    assert (
        resolve_value_store_tenant_id(SimpleNamespace(company_id="tenant-x"))
        == "tenant-x"
    )
    monkeypatch.setattr(settings, "value_revision_default_tenant_id", "default-tenant")
    for role, company_id in [
        ("admin", None),
        ("admin", ""),
        ("admin", "   "),
        ("admin", "string"),
        ("viewer", None),
    ]:
        with pytest.raises(HTTPException) as denied:
            resolve_value_store_tenant_id(
                SimpleNamespace(company_id=company_id, role=role)
            )
        assert denied.value.status_code == 403
        assert denied.value.detail == "Access denied: user is not assigned to a tenant"

    assert _infer_value_type(True) == "boolean"
    assert _infer_value_type("narrative") == "narrative"
    assert _infer_value_type(Decimal("1")) == "numeric"
    assert _record_value(_db_record(value_type="boolean", boolean_value=True)) is True
    assert _record_value(_db_record(value_type="text", text_value="hello")) == "hello"
    assert (
        _revision_value(SimpleNamespace(value_kind="boolean", boolean_value=True))
        is True
    )
    assert (
        _revision_value(SimpleNamespace(value_kind="text", text_value="hello"))
        == "hello"
    )
    assert _revision_value(
        SimpleNamespace(value_kind="numeric", numeric_value=Decimal("2"))
    ) == Decimal("2")
    assert _revision_value(
        SimpleNamespace(value_kind="numeric", canonical_value="3.5")
    ) == Decimal("3.5")
    assert _revision_value(
        SimpleNamespace(value_kind="numeric", canonical_value=None)
    ) == Decimal(0)
    assert _metadata_bool({"flag": "yes"}, "flag", default=False) is True
    assert _metadata_bool({"flag": "no"}, "flag", default=True) is False
    assert _metadata_bool({"flag": "unknown"}, "flag", default=True) is True
    assert _metadata_date({"d": ""}, "d") is None
    assert _metadata_date({"d": date(2026, 1, 1)}, "d") == date(2026, 1, 1)
    assert _metadata_date({"d": "2026-01-02"}, "d") == date(2026, 1, 2)
