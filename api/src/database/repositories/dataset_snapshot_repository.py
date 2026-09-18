"""Repository for persistent dataset snapshot history and diffs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import and_, false, or_
from sqlalchemy.orm import Session

from ..models import DatasetSnapshot


class DatasetSnapshotRepository:
    """Persist and query dataset snapshots for catalog audit/versioning."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, snapshot_id: int) -> Optional[DatasetSnapshot]:
        return (
            self.db.query(DatasetSnapshot)
            .filter(DatasetSnapshot.id == snapshot_id)
            .first()
        )

    def get_by_dataset_and_manifest(
        self, dataset: str, manifest_hash: str
    ) -> Optional[DatasetSnapshot]:
        return (
            self.db.query(DatasetSnapshot)
            .filter(
                DatasetSnapshot.dataset == dataset,
                DatasetSnapshot.manifest_hash == manifest_hash,
            )
            .first()
        )

    def list_recent(self, dataset: str, limit: int = 20) -> list[DatasetSnapshot]:
        return (
            self.db.query(DatasetSnapshot)
            .filter(DatasetSnapshot.dataset == dataset)
            .order_by(DatasetSnapshot.created_at.desc(), DatasetSnapshot.id.desc())
            .limit(limit)
            .all()
        )

    def list_feed(
        self,
        *,
        datasets: Optional[list[str]] = None,
        limit: int = 100,
        cursor_occurred_at: Optional[datetime] = None,
        cursor_dataset: Optional[str] = None,
        cursor_event_id: Optional[str] = None,
    ) -> list[DatasetSnapshot]:
        """Return catalog snapshots in total feed order for downstream synchronization."""
        query = self.db.query(DatasetSnapshot)
        if datasets:
            query = query.filter(DatasetSnapshot.dataset.in_(datasets))

        if cursor_occurred_at is not None:
            same_timestamp_filters = []
            if cursor_dataset:
                same_timestamp_filters.append(DatasetSnapshot.dataset > cursor_dataset)
                if datasets is None or cursor_dataset in datasets:
                    try:
                        snapshot_cursor_id = (
                            int(cursor_event_id)
                            if cursor_event_id is not None
                            else None
                        )
                    except ValueError:
                        snapshot_cursor_id = None
                    if snapshot_cursor_id is not None:
                        same_timestamp_filters.append(
                            and_(
                                DatasetSnapshot.dataset == cursor_dataset,
                                DatasetSnapshot.id > snapshot_cursor_id,
                            )
                        )

            query = query.filter(
                or_(
                    DatasetSnapshot.created_at > cursor_occurred_at,
                    and_(
                        DatasetSnapshot.created_at == cursor_occurred_at,
                        (
                            or_(*same_timestamp_filters)
                            if same_timestamp_filters
                            else false()
                        ),
                    ),
                )
            )

        return (
            query.order_by(
                DatasetSnapshot.created_at.asc(),
                DatasetSnapshot.dataset.asc(),
                DatasetSnapshot.id.asc(),
            )
            .limit(limit)
            .all()
        )

    def count(self, dataset: str) -> int:
        return (
            self.db.query(DatasetSnapshot)
            .filter(DatasetSnapshot.dataset == dataset)
            .count()
        )

    def get_previous(self, dataset: str, snapshot_id: int) -> Optional[DatasetSnapshot]:
        current = self.get_by_id(snapshot_id)
        if current is None or current.dataset != dataset:
            return None
        return (
            self.db.query(DatasetSnapshot)
            .filter(
                DatasetSnapshot.dataset == dataset,
                or_(
                    DatasetSnapshot.created_at < current.created_at,
                    and_(
                        DatasetSnapshot.created_at == current.created_at,
                        DatasetSnapshot.id < current.id,
                    ),
                ),
            )
            .order_by(DatasetSnapshot.created_at.desc(), DatasetSnapshot.id.desc())
            .first()
        )

    def create_or_get(
        self,
        *,
        dataset: str,
        manifest_hash: str,
        record_count: int,
        contract_version: str,
        item_index: dict[str, Any],
        source_ref: Optional[str] = None,
        source_hash: Optional[str] = None,
        created_by: Optional[str] = None,
        commit: bool = True,
    ) -> tuple[DatasetSnapshot, bool]:
        existing = self.get_by_dataset_and_manifest(dataset, manifest_hash)
        if existing is not None:
            return existing, False

        snapshot = DatasetSnapshot(
            dataset=dataset,
            manifest_hash=manifest_hash,
            record_count=record_count,
            contract_version=contract_version,
            item_index=item_index,
            source_ref=source_ref,
            source_hash=source_hash,
            created_by=created_by,
        )
        self.db.add(snapshot)
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        self.db.refresh(snapshot)
        return snapshot, True
