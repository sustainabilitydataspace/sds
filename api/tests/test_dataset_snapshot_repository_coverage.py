from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.database.models import DatasetSnapshot
from src.database.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)


class _Query:
    def __init__(self, rows=None, first=None, count=0):
        self.rows = list(rows or [])
        self.first_value = first
        self.count_value = count
        self.filters = []

    def filter(self, *criteria):
        self.filters.extend(criteria)
        return self

    def order_by(self, *_args):
        return self

    def limit(self, _limit):
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.first_value

    def count(self):
        return self.count_value


def _snapshot(snapshot_id: int, *, dataset: str = "indicators") -> DatasetSnapshot:
    return DatasetSnapshot(
        id=snapshot_id,
        dataset=dataset,
        manifest_hash=f"manifest-{snapshot_id}",
        record_count=1,
        contract_version="1.0",
        item_index={"items": []},
        created_at=datetime(2026, 1, snapshot_id, tzinfo=timezone.utc),
    )


def test_dataset_snapshot_repository_queries_feed_count_and_previous() -> None:
    first = _snapshot(1)
    second = _snapshot(2)
    db = MagicMock()
    db.query.side_effect = [
        _Query(first=first),
        _Query(first=first),
        _Query(rows=[first, second]),
        _Query(rows=[first, second]),
        _Query(count=2),
        _Query(first=second),
        _Query(first=first),
        _Query(first=_snapshot(3, dataset="mappings")),
    ]
    repo = DatasetSnapshotRepository(db)

    assert repo.get_by_id(1) is first
    assert repo.get_by_dataset_and_manifest("indicators", "manifest-1") is first
    assert repo.list_recent("indicators") == [first, second]
    assert repo.list_feed(
        datasets=["indicators"],
        cursor_occurred_at=first.created_at,
        cursor_dataset="indicators",
        cursor_event_id="1",
    ) == [first, second]
    assert repo.count("indicators") == 2
    assert repo.get_previous("indicators", 2) is first
    assert repo.get_previous("indicators", 3) is None


def test_dataset_snapshot_feed_tolerates_non_numeric_cursor_id() -> None:
    first = _snapshot(1)
    second = _snapshot(2)
    db = MagicMock()
    query = _Query(rows=[first, second])
    db.query.return_value = query
    repo = DatasetSnapshotRepository(db)

    assert repo.list_feed(
        datasets=["indicators"],
        cursor_occurred_at=first.created_at,
        cursor_dataset="indicators",
        cursor_event_id="not-an-int",
    ) == [first, second]


def test_dataset_snapshot_repository_create_or_get_existing_and_new() -> None:
    existing = _snapshot(1)
    db = MagicMock()
    repo = DatasetSnapshotRepository(db)
    repo.get_by_dataset_and_manifest = MagicMock(return_value=existing)

    snapshot, created = repo.create_or_get(
        dataset="indicators",
        manifest_hash="manifest-1",
        record_count=1,
        contract_version="1.0",
        item_index={"items": []},
    )
    assert snapshot is existing
    assert created is False

    repo.get_by_dataset_and_manifest = MagicMock(return_value=None)
    snapshot, created = repo.create_or_get(
        dataset="indicators",
        manifest_hash="manifest-2",
        record_count=2,
        contract_version="1.0",
        item_index={"items": ["a", "b"]},
        source_ref="register.csv",
        source_hash="sha",
        created_by="admin",
        commit=False,
    )
    assert created is True
    assert snapshot.dataset == "indicators"
    db.add.assert_called_with(snapshot)
    db.flush.assert_called()
    db.refresh.assert_called_with(snapshot)

    db.reset_mock()
    repo.create_or_get(
        dataset="indicators",
        manifest_hash="manifest-3",
        record_count=3,
        contract_version="1.0",
        item_index={"items": ["a", "b", "c"]},
        commit=True,
    )
    db.commit.assert_called()
