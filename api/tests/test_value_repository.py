"""ValueRepository unit tests (SQLAlchemy session mocked)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from src.database.models import ESGValue
from src.database.repositories.value_repository import ValueRepository
from src.services.value_ingest import PreparedValueRecord


def test_create_value_persists_record():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    repo = ValueRepository(db)

    record = repo.create_value(
        value_id="v1",
        tenant_id="tenant-a",
        concept="c1",
        entity="e1",
        period=date(2025, 1, 1),
        value=1.23,
        unit="kg",
        original_unit="g",
        conversion_applied=True,
        metadata={"source": "test"},
        created_by="u1",
    )

    assert isinstance(record, ESGValue)
    assert record.tenant_id == "tenant-a"
    assert record.ownership_state == "resolved"
    db.add.assert_called_once()
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(record)


def test_create_value_rejects_blank_tenant_before_write():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    repo = ValueRepository(db)

    with pytest.raises(ValueError, match="tenant_id"):
        repo.create_value(
            value_id="v1",
            tenant_id="  ",
            concept="c1",
            entity="e1",
            period=date(2025, 1, 1),
            value=1.23,
            unit="kg",
        )

    db.add.assert_not_called()


def test_get_value_by_id_builds_query_chain():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.first.return_value = "one"

    repo = ValueRepository(db)
    assert repo.get_value_by_id("v1", tenant_id="tenant-a") == "one"
    assert len(query.filter.call_args.args) == 3
    query.first.assert_called_once()


def test_value_reads_reject_blank_tenant_before_query():
    db = MagicMock()
    repo = ValueRepository(db)

    with pytest.raises(ValueError, match="tenant_id"):
        repo.get_value_by_id("v1", tenant_id=" ")

    db.query.assert_not_called()


def test_list_values_applies_filters_and_paginates():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.count.return_value = 2
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = ["a", "b"]

    repo = ValueRepository(db)
    items, total = repo.list_values(
        tenant_id="tenant-a",
        concept="c1",
        entity="e1",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        unit="kg",
        limit=10,
        offset=0,
    )

    assert items == ["a", "b"]
    assert total == 2
    assert query.filter.call_count >= 5
    query.order_by.assert_called_once()
    query.all.assert_called_once()


def test_delete_value_deletes_when_found():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    repo = ValueRepository(db)

    record = MagicMock()
    repo.get_value_by_id = MagicMock(return_value=record)

    assert repo.delete_value("v1", tenant_id="tenant-a") is True
    repo.get_value_by_id.assert_called_once_with(
        "v1", tenant_id="tenant-a", allowed_entities=None
    )
    db.delete.assert_called_once_with(record)
    db.commit.assert_called_once()


def test_delete_value_returns_false_when_missing():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    repo = ValueRepository(db)

    repo.get_value_by_id = MagicMock(return_value=None)

    assert repo.delete_value("missing", tenant_id="tenant-a") is False
    db.delete.assert_not_called()


def test_create_value_rolls_back_when_semantic_tables_are_touched():
    from src.database.models import Indicator
    from src.database.semantic_guard import SemanticIsolationError

    db = MagicMock()
    db.new = [
        Indicator(id="i1", identifier="urn:sds:reg:test:1", title="T", dimension="E")
    ]
    db.dirty = []
    db.deleted = []
    repo = ValueRepository(db)

    with pytest.raises(SemanticIsolationError):
        repo.create_value(
            value_id="v1",
            tenant_id="tenant-a",
            concept="c1",
            entity="e1",
            period=date(2025, 1, 1),
            value=1.23,
            unit="kg",
            metadata={},
            created_by="u1",
        )

    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_create_values_commits_once_for_batch():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    repo = ValueRepository(db)

    records = repo.create_values(
        tenant_id="tenant-a",
        records=[
            PreparedValueRecord(
                value_id="v1",
                concept="c1",
                entity="e1",
                period=date(2025, 1, 1),
                value=1.23,
                value_type="numeric",
                unit="kg",
                original_unit=None,
                conversion_applied=False,
                metadata={"source": "batch"},
            ),
            PreparedValueRecord(
                value_id="v2",
                concept="c2",
                entity="e1",
                period=date(2025, 1, 2),
                value=2.34,
                value_type="numeric",
                unit="kg",
                original_unit=None,
                conversion_applied=False,
                metadata={"source": "batch"},
            ),
        ],
        created_by="u1",
    )

    assert len(records) == 2
    assert all(isinstance(record, ESGValue) for record in records)
    assert db.add.call_count == 2
    db.commit.assert_called_once()
    assert db.refresh.call_count == 2


def test_create_values_rolls_back_on_failure():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    db.add.side_effect = RuntimeError("add failed")
    repo = ValueRepository(db)

    with pytest.raises(RuntimeError, match="add failed"):
        repo.create_values(
            tenant_id="tenant-a",
            records=[
                PreparedValueRecord(
                    value_id="v-fail",
                    concept="c1",
                    entity="e1",
                    period=date(2025, 1, 1),
                    value=1.23,
                    value_type="numeric",
                    unit="kg",
                    original_unit=None,
                    conversion_applied=False,
                    metadata={},
                )
            ],
        )

    db.rollback.assert_called_once()


def test_value_repository_batch_paths_flush_without_commit_and_empty_save():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    repo = ValueRepository(db)
    prepared = PreparedValueRecord(
        value_id="v3",
        concept="c3",
        entity="e1",
        period=date(2025, 1, 3),
        value=True,
        value_type="boolean",
        unit="flag",
        original_unit=None,
        conversion_applied=False,
        metadata={"source": "batch"},
    )

    created = repo.create_values(
        records=[prepared],
        tenant_id="tenant-a",
        created_by="u1",
        commit=False,
    )
    saved = repo.save_values(
        records=[prepared], tenant_id="tenant-a", created_by="u1", commit=False
    )

    assert len(created) == 1
    assert len(saved) == 1
    assert saved[0].boolean_value is True
    assert repo.save_values(records=[], tenant_id="tenant-a", created_by="u1") == []
    assert db.flush.call_count == 2
    db.commit.assert_not_called()


def test_save_values_inserts_external_keys_and_rolls_back_on_failure():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    persisted = ESGValue(
        id="persisted-id",
        concept="c-upsert",
        entity="e1",
        period=date(2025, 1, 5),
        value=Decimal("9.9"),
        unit="kg",
        external_key="external:v-upsert",
    )
    # Bulk upsert returns the ESGValue directly via scalars().all()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [persisted]
    db.execute.return_value = mock_result
    repo = ValueRepository(db)

    saved = repo.save_values(
        tenant_id="tenant-a",
        records=[
            PreparedValueRecord(
                value_id="v-upsert",
                concept="c-upsert",
                entity="e1",
                period=date(2025, 1, 5),
                value=Decimal("9.9"),
                value_type="numeric",
                unit="kg",
                original_unit=None,
                conversion_applied=False,
                metadata={"source": "upsert"},
                external_key="external:v-upsert",
            )
        ],
        created_by="u1",
    )

    assert saved == [persisted]
    db.execute.assert_called_once()
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(persisted)

    failing_db = MagicMock()
    failing_db.new = []
    failing_db.dirty = []
    failing_db.deleted = []
    failing_db.execute.side_effect = RuntimeError("execute failed")
    failing_repo = ValueRepository(failing_db)

    with pytest.raises(RuntimeError, match="execute failed"):
        failing_repo.save_values(
            tenant_id="tenant-a",
            records=[
                PreparedValueRecord(
                    value_id="v-upsert",
                    concept="c-upsert",
                    entity="e1",
                    period=date(2025, 1, 5),
                    value=Decimal("9.9"),
                    value_type="numeric",
                    unit="kg",
                    original_unit=None,
                    conversion_applied=False,
                    metadata={},
                    external_key="external:v-upsert",
                )
            ],
        )

    failing_db.rollback.assert_called_once()


def test_save_values_preserves_input_order_for_mixed_keyed_and_unkeyed_records():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    repo = ValueRepository(db)
    keyed_1 = ESGValue(
        id="keyed-1",
        concept="c-keyed-1",
        entity="e1",
        period=date(2025, 1, 1),
        value=Decimal("1.0"),
        unit="kg",
        external_key="external:keyed-1",
    )
    keyed_3 = ESGValue(
        id="keyed-3",
        concept="c-keyed-3",
        entity="e1",
        period=date(2025, 1, 3),
        value=Decimal("3.0"),
        unit="kg",
        external_key="external:keyed-3",
    )
    repo._save_values_bulk_upsert = MagicMock(return_value=[keyed_1, keyed_3])

    saved = repo.save_values(
        tenant_id="tenant-a",
        records=[
            PreparedValueRecord(
                value_id="keyed-1",
                concept="c-keyed-1",
                entity="e1",
                period=date(2025, 1, 1),
                value=Decimal("1.0"),
                value_type="numeric",
                unit="kg",
                original_unit=None,
                conversion_applied=False,
                metadata={},
                external_key="external:keyed-1",
            ),
            PreparedValueRecord(
                value_id="unkeyed-2",
                concept="c-unkeyed-2",
                entity="e1",
                period=date(2025, 1, 2),
                value=Decimal("2.0"),
                value_type="numeric",
                unit="kg",
                original_unit=None,
                conversion_applied=False,
                metadata={},
            ),
            PreparedValueRecord(
                value_id="keyed-3",
                concept="c-keyed-3",
                entity="e1",
                period=date(2025, 1, 3),
                value=Decimal("3.0"),
                value_type="numeric",
                unit="kg",
                original_unit=None,
                conversion_applied=False,
                metadata={},
                external_key="external:keyed-3",
            ),
            PreparedValueRecord(
                value_id="unkeyed-4",
                concept="c-unkeyed-4",
                entity="e1",
                period=date(2025, 1, 4),
                value=Decimal("4.0"),
                value_type="numeric",
                unit="kg",
                original_unit=None,
                conversion_applied=False,
                metadata={},
            ),
        ],
        created_by="u1",
        commit=False,
        refresh=False,
    )

    assert [record.id for record in saved] == [
        "keyed-1",
        "unkeyed-2",
        "keyed-3",
        "unkeyed-4",
    ]
    repo._save_values_bulk_upsert.assert_called_once()
    db.flush.assert_called_once()


def test_save_values_external_key_collision_uses_insert_only_and_fails_generically():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    db.execute.return_value = mock_result
    repo = ValueRepository(db)

    with pytest.raises(RuntimeError) as exc_info:
        repo.save_values(
            tenant_id="tenant-a",
            records=[
                PreparedValueRecord(
                    value_id="safe-new",
                    concept="scope-3",
                    entity="tenant-a-entity",
                    period=date(2025, 1, 1),
                    value=Decimal("1.0"),
                    value_type="numeric",
                    unit="tCO2e",
                    original_unit=None,
                    conversion_applied=False,
                    metadata={"source": "batch-new"},
                ),
                PreparedValueRecord(
                    value_id="incoming-secret-id",
                    concept="scope-3",
                    entity="tenant-b-secret-entity",
                    period=date(2025, 1, 1),
                    value=Decimal("999.9"),
                    value_type="numeric",
                    unit="tCO2e",
                    original_unit=None,
                    conversion_applied=False,
                    metadata={"payload": "secret-payload"},
                    external_key="global-secret-key",
                ),
            ],
            created_by="u1",
        )

    db.execute.assert_called_once()
    compiled = str(
        db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    ).upper()
    assert "ON CONFLICT" in compiled
    assert "DO NOTHING" in compiled
    assert "DO UPDATE" not in compiled
    assert "GLOBAL-SECRET-KEY" not in str(exc_info.value).upper()
    assert "INCOMING-SECRET-ID" not in str(exc_info.value).upper()
    assert "999.9" not in str(exc_info.value)
    assert "TENANT-B-SECRET-ENTITY" not in str(exc_info.value).upper()
    assert "SECRET-PAYLOAD" not in str(exc_info.value).upper()
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
    db.refresh.assert_not_called()


def test_value_repository_cursor_iter_and_change_feed_paths():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    repo = ValueRepository(db)
    first = SimpleNamespace(
        id="v2",
        period=date(2025, 1, 2),
        created_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
        updated_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    second = SimpleNamespace(
        id="v1",
        period=date(2025, 1, 1),
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.count.return_value = 2
    query.order_by.return_value = query
    query.limit.return_value = query
    query.first.return_value = first
    query.all.return_value = [first, second]
    query.yield_per.return_value = [first, second]

    items, total, cursor, has_more = repo.list_values_cursor(
        tenant_id="tenant-a",
        concept="c1",
        entity="e1",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        unit="kg",
        changed_since=datetime(2025, 1, 1, tzinfo=timezone.utc),
        limit=1,
        cursor="eyJwZXJpb2QiOiIyMDI1LTAxLTAyIiwiY3JlYXRlZF9hdCI6IjIwMjUtMDEtMDJUMDA6MDA6MDArMDA6MDAiLCJ2YWx1ZV9pZCI6InYyIn0=",
    )
    assert items == [first]
    assert total == 2
    assert cursor is not None
    assert has_more is True

    assert (
        repo.get_latest_value(
            tenant_id="tenant-a",
            concept="c1",
            entity="e1",
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
        )
        is first
    )
    query.limit.assert_any_call(1)

    assert list(
        repo.iter_values(
            tenant_id="tenant-a",
            concept="c1",
            entity="e1",
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            unit="kg",
            changed_since=datetime(2025, 1, 1, tzinfo=timezone.utc),
            batch_size=10,
        )
    ) == [first, second]

    changes, change_cursor, has_more = repo.list_value_changes(
        tenant_id="tenant-a",
        concept="c1",
        entity="e1",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        unit="kg",
        changed_since=datetime(2025, 1, 1, tzinfo=timezone.utc),
        limit=1,
        cursor_changed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        cursor_value_id="v1",
    )
    assert changes == [first]
    assert change_cursor == (first.updated_at.isoformat(), first.id)
    assert has_more is True


def test_value_repository_delete_rolls_back_on_failure():
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    db.delete.side_effect = RuntimeError("delete failed")
    repo = ValueRepository(db)
    repo.get_value_by_id = MagicMock(return_value=MagicMock())

    with pytest.raises(RuntimeError):
        repo.delete_value("v1", tenant_id="tenant-a")

    db.rollback.assert_called_once()


def test_value_repository_value_column_helpers_cover_types():
    from src.database.repositories.value_repository import _value_columns

    assert _value_columns(True, None)["value_type"] == "boolean"
    assert _value_columns("narrative text", None) == {
        "value": None,
        "value_type": "narrative",
        "text_value": "narrative text",
        "boolean_value": None,
    }
    assert _value_columns("semi", "semi_narrative")["value_type"] == "semi-narrative"
    assert _value_columns("narrative text", "string")["value_type"] == "narrative"
    assert _value_columns(Decimal("1.5"), "decimal")["value"] == Decimal("1.5")
    assert _value_columns("x", "custom")["value_type"] == "numeric"


def test_external_key_unique_index_is_tenant_scoped() -> None:
    indexes = {index.name: index for index in ESGValue.__table__.indexes}
    index = indexes["ix_esg_values_tenant_external_key_unique"]
    assert index.unique is True
    assert [column.name for column in index.columns] == ["tenant_id", "external_key"]
