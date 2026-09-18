"""Value persistence backends (DB or in-memory)."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from fastapi import Depends, HTTPException, Request
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session, contains_eager

import structlog
from src.api.models import ValueResponse, ValueScalar
from src.config.settings import settings
from src.database.models import CurrentValuePointer
from src.database.models import HierarchyConfiguration as HierarchyConfigurationModel
from src.database.models import ValueContext, ValueRevision
from src.database.repositories.value_repository import ValueRepository
from src.database.session import get_db_optional
from src.services.change_feed import ChangeFeedCursor
from src.services.value_ingest import PreparedValueRecord
from src.services.value_pagination import (
    decode_value_cursor,
    encode_value_cursor,
    is_after_cursor,
)
from src.services.value_revision_backfill import (
    VALUE_RESPONSE_METADATA_KEY,
    revision_input_for_esg_value,
)
from src.services.value_revision_store import ValueRevisionStore
from src.services.value_versioning import build_context_hash

logger = structlog.get_logger(__name__)

_VALUE_SERVICE_UNAVAILABLE_DETAIL = "Value service unavailable"


def _raise_value_service_unavailable() -> None:
    raise HTTPException(
        status_code=503,
        detail=_VALUE_SERVICE_UNAVAILABLE_DETAIL,
    )


class _ExternallyManagedWriteSession:
    """Delegate work while leaving transaction finalization to its owner."""

    def __init__(self, db: Session):
        self._db = db

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)


@dataclass(frozen=True)
class _StoredValue:
    id: str
    concept: str
    entity: str
    period: date
    external_key: Optional[str]
    value: ValueScalar
    value_type: str
    unit: str
    original_value: Optional[ValueScalar]
    original_unit: Optional[str]
    conversion_applied: bool
    currency: Optional[str]
    original_currency: Optional[str]
    currency_conversion_applied: bool
    conversion_trace: Optional[List[Dict[str, Any]]]
    value_date: Optional[date]
    period_start: Optional[date]
    period_end: Optional[date]
    metadata: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime


class InMemoryValueStore:
    """Offline-safe value store (ephemeral, per-process).

    ``commit`` is accepted for backend compatibility; writes are immediate
    because this backend has no database transaction to finalize.
    """

    def __init__(self):
        self._values: Dict[str, _StoredValue] = {}

    def create(
        self,
        *,
        value_id: str,
        concept: str,
        entity: str,
        period: date,
        value: ValueScalar,
        unit: str,
        original_value: Optional[ValueScalar] = None,
        original_unit: Optional[str],
        conversion_applied: bool,
        currency: Optional[str] = None,
        original_currency: Optional[str] = None,
        currency_conversion_applied: bool = False,
        conversion_trace: Optional[List[Dict[str, Any]]] = None,
        value_date: Optional[date] = None,
        period_start: Optional[date] = None,
        period_end: Optional[date] = None,
        metadata: Optional[Dict[str, Any]],
        created_by: Optional[str] = None,
        external_key: Optional[str] = None,
        value_type: Optional[str] = None,
        commit: bool = True,
    ) -> ValueResponse:
        now = datetime.now(timezone.utc)
        stored = _StoredValue(
            id=value_id,
            concept=concept,
            entity=entity,
            period=period,
            external_key=external_key,
            value=value,
            value_type=value_type or _infer_value_type(value),
            unit=unit,
            original_value=original_value,
            original_unit=original_unit,
            conversion_applied=conversion_applied,
            currency=currency,
            original_currency=original_currency,
            currency_conversion_applied=currency_conversion_applied,
            conversion_trace=conversion_trace,
            value_date=value_date,
            period_start=period_start,
            period_end=period_end,
            metadata=metadata,
            created_at=now,
            updated_at=now,
        )
        self._values[value_id] = stored
        return self._to_response(stored)

    def get(self, value_id: str) -> Optional[ValueResponse]:
        stored = self._values.get(value_id)
        if not stored:
            return None
        return self._to_response(stored)

    def bulk_create(
        self,
        *,
        records: List[PreparedValueRecord],
        created_by: Optional[str] = None,
        refresh: bool = True,
        return_responses: bool = True,
        commit: bool = True,
    ) -> List[Any]:
        responses: List[ValueResponse] = []
        for record in records:
            responses.append(
                self.create(
                    value_id=record.value_id,
                    concept=record.concept,
                    entity=record.entity,
                    period=record.period,
                    external_key=record.external_key,
                    value=record.value,
                    value_type=record.value_type,
                    unit=record.unit,
                    original_value=record.original_value,
                    original_unit=record.original_unit,
                    conversion_applied=record.conversion_applied,
                    currency=record.currency,
                    original_currency=record.original_currency,
                    currency_conversion_applied=record.currency_conversion_applied,
                    conversion_trace=record.conversion_trace,
                    value_date=record.value_date,
                    period_start=record.period_start,
                    period_end=record.period_end,
                    metadata=record.metadata,
                    created_by=created_by,
                )
            )
        return responses

    def save(
        self,
        *,
        records: List[PreparedValueRecord],
        created_by: Optional[str] = None,
        refresh: bool = True,
        return_responses: bool = True,
        commit: bool = True,
    ) -> List[Any]:
        external_index = {
            stored.external_key: stored_id
            for stored_id, stored in self._values.items()
            if stored.external_key
        }
        batch_external_keys: set[str] = set()
        for record in records:
            if not record.external_key:
                continue
            if (
                record.external_key in external_index
                or record.external_key in batch_external_keys
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Value external key already exists",
                )
            batch_external_keys.add(record.external_key)

        responses: List[ValueResponse] = []
        for record in records:
            responses.append(
                self.create(
                    value_id=record.value_id,
                    concept=record.concept,
                    entity=record.entity,
                    period=record.period,
                    external_key=record.external_key,
                    value=record.value,
                    value_type=record.value_type,
                    unit=record.unit,
                    original_value=record.original_value,
                    original_unit=record.original_unit,
                    conversion_applied=record.conversion_applied,
                    currency=record.currency,
                    original_currency=record.original_currency,
                    currency_conversion_applied=record.currency_conversion_applied,
                    conversion_trace=record.conversion_trace,
                    value_date=record.value_date,
                    period_start=record.period_start,
                    period_end=record.period_end,
                    metadata=record.metadata,
                    created_by=created_by,
                )
            )
        return responses

    def list(
        self,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime] = None,
        limit: int,
        offset: int,
    ) -> Tuple[List[ValueResponse], int]:
        items = list(self._values.values())
        if concept:
            items = [v for v in items if v.concept == concept]
        if entity:
            items = [v for v in items if v.entity == entity]
        if period_start:
            items = [v for v in items if v.period >= period_start]
        if period_end:
            items = [v for v in items if v.period <= period_end]
        if unit:
            items = [v for v in items if v.unit == unit]
        if changed_since:
            items = [
                v
                for v in items
                if v.updated_at >= changed_since or v.created_at >= changed_since
            ]

        total = len(items)
        paginated = items[offset : offset + limit]
        return [self._to_response(v) for v in paginated], total

    def list_cursor(
        self,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime] = None,
        limit: int,
        cursor: Optional[str],
    ) -> Tuple[List[ValueResponse], int, Optional[str], bool]:
        items = list(self._values.values())
        if concept:
            items = [v for v in items if v.concept == concept]
        if entity:
            items = [v for v in items if v.entity == entity]
        if period_start:
            items = [v for v in items if v.period >= period_start]
        if period_end:
            items = [v for v in items if v.period <= period_end]
        if unit:
            items = [v for v in items if v.unit == unit]
        if changed_since:
            items = [
                v
                for v in items
                if v.updated_at >= changed_since or v.created_at >= changed_since
            ]

        total = len(items)
        items.sort(
            key=lambda item: (item.period, item.created_at, item.id), reverse=True
        )
        if cursor:
            decoded = decode_value_cursor(cursor)
            items = [
                item
                for item in items
                if is_after_cursor(
                    decoded,
                    period=item.period,
                    created_at=item.created_at,
                    value_id=item.id,
                )
            ]

        page = items[: limit + 1]
        has_more = len(page) > limit
        selected = page[:limit]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = encode_value_cursor(
                period=last.period, created_at=last.created_at, value_id=last.id
            )
        return [self._to_response(v) for v in selected], total, next_cursor, has_more

    def latest(
        self,
        *,
        concept: str,
        entity: str,
        period_start: date,
        period_end: date,
    ) -> Optional[ValueResponse]:
        items = list(
            self.iterate(
                concept=concept,
                entity=entity,
                period_start=period_start,
                period_end=period_end,
                unit=None,
                changed_since=None,
            )
        )
        if not items:
            return None
        items.sort(
            key=lambda item: (
                item.period,
                item.updated_at or item.created_at,
                item.id,
            ),
            reverse=True,
        )
        return items[0]

    def iterate(
        self,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime] = None,
    ) -> Iterable[ValueResponse]:
        items = list(self._values.values())
        if concept:
            items = [v for v in items if v.concept == concept]
        if entity:
            items = [v for v in items if v.entity == entity]
        if period_start:
            items = [v for v in items if v.period >= period_start]
        if period_end:
            items = [v for v in items if v.period <= period_end]
        if unit:
            items = [v for v in items if v.unit == unit]
        if changed_since:
            items = [
                v
                for v in items
                if v.updated_at >= changed_since or v.created_at >= changed_since
            ]

        items.sort(
            key=lambda item: (item.period, item.created_at, item.id), reverse=True
        )
        for item in items:
            yield self._to_response(item)

    def list_changes(
        self,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime],
        limit: int,
        cursor: Optional[ChangeFeedCursor],
    ) -> Tuple[List[ValueResponse], Optional[str], bool]:
        items = list(self._values.values())
        if concept:
            items = [v for v in items if v.concept == concept]
        if entity:
            items = [v for v in items if v.entity == entity]
        if period_start:
            items = [v for v in items if v.period >= period_start]
        if period_end:
            items = [v for v in items if v.period <= period_end]
        if unit:
            items = [v for v in items if v.unit == unit]
        if changed_since:
            items = [
                v
                for v in items
                if v.updated_at >= changed_since or v.created_at >= changed_since
            ]

        items.sort(key=lambda item: (item.updated_at, item.id))
        if cursor:
            items = [
                item
                for item in items
                if (item.updated_at, item.id) > (cursor.occurred_at, cursor.event_id)
            ]

        page = items[: limit + 1]
        has_more = len(page) > limit
        selected = page[:limit]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = last.updated_at.isoformat(), last.id
        responses = [self._to_response(v) for v in selected]
        return responses, next_cursor, has_more

    def delete(self, value_id: str) -> bool:
        return self._values.pop(value_id, None) is not None

    @staticmethod
    def _to_response(stored: _StoredValue) -> ValueResponse:
        return ValueResponse(
            id=stored.id,
            concept=stored.concept,
            entity=stored.entity,
            period=stored.period,
            external_key=stored.external_key,
            value=stored.value,
            original_value=stored.original_value,
            value_type=stored.value_type,
            unit=stored.unit,
            original_unit=stored.original_unit,
            conversion_applied=stored.conversion_applied,
            currency=stored.currency,
            original_currency=stored.original_currency,
            currency_conversion_applied=stored.currency_conversion_applied,
            conversion_trace=stored.conversion_trace,
            value_date=stored.value_date,
            period_start=stored.period_start,
            period_end=stored.period_end,
            metadata=stored.metadata,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
        )


class DatabaseValueStore:
    """PostgreSQL-backed value store."""

    def __init__(self, db: Session, *, tenant_id: Optional[str] = None):
        self._db = db
        self._tenant_id_value = tenant_id
        self._repo = ValueRepository(db)
        self._revision_store = ValueRevisionStore(db)
        self._legacy_allowed_entities_cache: Optional[set[str]] = None

    def create(
        self,
        *,
        value_id: str,
        concept: str,
        entity: str,
        period: date,
        value: ValueScalar,
        unit: str,
        original_value: Optional[ValueScalar] = None,
        original_unit: Optional[str],
        conversion_applied: bool,
        currency: Optional[str] = None,
        original_currency: Optional[str] = None,
        currency_conversion_applied: bool = False,
        conversion_trace: Optional[List[Dict[str, Any]]] = None,
        value_date: Optional[date] = None,
        period_start: Optional[date] = None,
        period_end: Optional[date] = None,
        metadata: Optional[Dict[str, Any]],
        created_by: Optional[str],
        external_key: Optional[str] = None,
        value_type: Optional[str] = None,
        commit: bool = True,
    ) -> ValueResponse:
        if not self._revision_write_enabled():
            _raise_value_service_unavailable()

        try:
            with self._deferred_repository_rollback_guard(enabled=not commit):
                record = self._repo.create_value(
                    value_id=value_id,
                    concept=concept,
                    entity=entity,
                    period=period,
                    external_key=external_key,
                    value=value,
                    value_type=value_type,
                    unit=unit,
                    original_value=original_value,
                    original_unit=original_unit,
                    conversion_applied=conversion_applied,
                    currency=currency,
                    original_currency=original_currency,
                    currency_conversion_applied=currency_conversion_applied,
                    conversion_trace=conversion_trace,
                    value_date=value_date,
                    period_start=period_start,
                    period_end=period_end,
                    metadata=metadata,
                    created_by=created_by,
                    commit=False,
                )
            revision = self._append_revision_for_record(record, created_by=created_by)
            if commit:
                self._db.commit()
            else:
                self._db.flush()
            self._safe_refresh(record)
            self._safe_refresh(revision)
            if self._revision_read_enabled():
                return self._to_revision_response(
                    self._revisions_for_response([revision])[0]
                )
            return self._to_response(record)
        except Exception:
            if commit:
                self._db.rollback()
            raise

    def get(self, value_id: str) -> Optional[ValueResponse]:
        if self._revision_read_enabled():
            revision = self._get_current_revision(value_id)
            return self._to_revision_response(revision) if revision else None
        _raise_value_service_unavailable()

    def bulk_create(
        self,
        *,
        records: List[PreparedValueRecord],
        created_by: Optional[str],
        refresh: bool = True,
        return_responses: bool = True,
        commit: bool = True,
    ) -> List[ValueResponse]:
        if not self._revision_write_enabled():
            _raise_value_service_unavailable()

        try:
            with self._deferred_repository_rollback_guard(enabled=not commit):
                persisted = self._repo.create_values(
                    records=records,
                    created_by=created_by,
                    commit=False,
                )
            revisions = self._append_revisions_for_records(
                persisted,
                created_by=created_by,
                refresh=refresh,
            )
            if commit:
                self._commit(preserve_instances=not refresh)
            else:
                self._db.flush()
            if refresh:
                for record in persisted:
                    self._safe_refresh(record)
                for revision in revisions:
                    self._safe_refresh(revision)
            if not return_responses:
                return persisted
            if self._revision_read_enabled():
                return [
                    self._to_revision_response(revision)
                    for revision in self._revisions_for_response(revisions)
                ]
            return [self._to_response(record) for record in persisted]
        except Exception:
            if commit:
                self._db.rollback()
            raise

    def save(
        self,
        *,
        records: List[PreparedValueRecord],
        created_by: Optional[str],
        refresh: bool = True,
        return_responses: bool = True,
        commit: bool = True,
    ) -> List[ValueResponse]:
        if not self._revision_write_enabled():
            _raise_value_service_unavailable()

        try:
            with self._deferred_repository_rollback_guard(enabled=not commit):
                persisted = self._repo.save_values(
                    records=records,
                    created_by=created_by,
                    commit=False,
                )
            revisions = self._append_revisions_for_records(
                persisted,
                created_by=created_by,
                refresh=refresh,
            )
            if commit:
                self._commit(preserve_instances=not refresh)
            else:
                self._db.flush()
            if refresh:
                for record in persisted:
                    self._safe_refresh(record)
                for revision in revisions:
                    self._safe_refresh(revision)
            if not return_responses:
                return persisted
            if self._revision_read_enabled():
                return [
                    self._to_revision_response(revision)
                    for revision in self._revisions_for_response(revisions)
                ]
            return [self._to_response(record) for record in persisted]
        except Exception:
            if commit:
                self._db.rollback()
            raise

    def list(
        self,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime] = None,
        limit: int,
        offset: int,
    ) -> Tuple[List[ValueResponse], int]:
        if self._revision_read_enabled():
            revisions, total = self._list_current_revisions(
                concept=concept,
                entity=entity,
                period_start=period_start,
                period_end=period_end,
                unit=unit,
                changed_since=changed_since,
                limit=limit,
                offset=offset,
            )
            return [
                self._to_revision_response(revision) for revision in revisions
            ], total

        _raise_value_service_unavailable()

    def list_cursor(
        self,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime] = None,
        limit: int,
        cursor: Optional[str],
    ) -> Tuple[List[ValueResponse], int, Optional[str], bool]:
        if self._revision_read_enabled():
            # Revision-backed pagination keeps the existing offset/cursor API
            # stable by decoding the legacy value cursor and applying it to the
            # projected current revision ordering.
            offset = 0
            if cursor:
                decoded = decode_value_cursor(cursor)
                revisions, total = self._list_current_revisions_after_cursor(
                    concept=concept,
                    entity=entity,
                    period_start=period_start,
                    period_end=period_end,
                    unit=unit,
                    changed_since=changed_since,
                    limit=limit,
                    cursor_period=decoded.period,
                    cursor_created_at=decoded.created_at,
                    cursor_value_id=decoded.value_id,
                )
            else:
                revisions, total = self._list_current_revisions(
                    concept=concept,
                    entity=entity,
                    period_start=period_start,
                    period_end=period_end,
                    unit=unit,
                    changed_since=changed_since,
                    limit=limit + 1,
                    offset=offset,
                )
            has_more = len(revisions) > limit
            selected = revisions[:limit]
            next_cursor = None
            if has_more and selected:
                last_response = self._to_revision_response(selected[-1])
                next_cursor = encode_value_cursor(
                    period=last_response.period,
                    created_at=last_response.created_at,
                    value_id=last_response.id,
                )
            return (
                [self._to_revision_response(revision) for revision in selected],
                total,
                next_cursor,
                has_more,
            )

        _raise_value_service_unavailable()

    def latest(
        self,
        *,
        concept: str,
        entity: str,
        period_start: date,
        period_end: date,
    ) -> Optional[ValueResponse]:
        if self._revision_read_enabled():
            revision = self._latest_current_revision(
                concept=concept,
                entity=entity,
                period_start=period_start,
                period_end=period_end,
            )
            return self._to_revision_response(revision) if revision else None

        _raise_value_service_unavailable()

    def iterate(
        self,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime] = None,
    ) -> Iterable[ValueResponse]:
        if self._revision_read_enabled():
            offset = 0
            while True:
                revisions = self._list_current_revision_page(
                    concept=concept,
                    entity=entity,
                    period_start=period_start,
                    period_end=period_end,
                    unit=unit,
                    changed_since=changed_since,
                    limit=500,
                    offset=offset,
                )
                if not revisions:
                    break
                for revision in revisions:
                    yield self._to_revision_response(revision)
                offset += len(revisions)
            return

        _raise_value_service_unavailable()

    def list_changes(
        self,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime],
        limit: int,
        cursor: Optional[ChangeFeedCursor],
    ) -> Tuple[List[ValueResponse], Optional[str], bool]:
        if self._revision_read_enabled():
            revisions, _total = self._list_current_revisions_after_change_cursor(
                concept=concept,
                entity=entity,
                period_start=period_start,
                period_end=period_end,
                unit=unit,
                changed_since=changed_since,
                limit=limit + 1,
                cursor_changed_at=cursor.occurred_at if cursor else None,
                cursor_value_id=cursor.event_id if cursor else None,
            )
            has_more = len(revisions) > limit
            selected = revisions[:limit]
            next_cursor = None
            if has_more and selected:
                last_response = self._to_revision_response(selected[-1])
                next_cursor = last_response.updated_at.isoformat(), last_response.id
            return (
                [self._to_revision_response(revision) for revision in selected],
                next_cursor,
                has_more,
            )

        _raise_value_service_unavailable()

    def delete(self, value_id: str) -> bool:
        _raise_value_service_unavailable()

    def _revision_read_enabled(self) -> bool:
        return settings.value_revision_primary_read_path == "revision"

    def _revision_write_enabled(self) -> bool:
        return (
            settings.value_revision_dual_write_enabled
            or settings.value_revision_primary_read_path == "revision"
        )

    def _legacy_allowed_entities(self) -> Optional[set[str]]:
        if not settings.require_database or not self._tenant_id_value:
            return None
        if self._legacy_allowed_entities_cache is not None:
            return self._legacy_allowed_entities_cache

        records = (
            self._db.query(HierarchyConfigurationModel)
            .filter(
                HierarchyConfigurationModel.company_id == self._tenant_id_value,
                HierarchyConfigurationModel.is_active.is_(True),
            )
            .all()
        )
        allowed: set[str] = set()
        for record in records:
            try:
                payload = json.loads(record.configuration)
            except (TypeError, json.JSONDecodeError):
                continue
            for level in payload.get("levels", []) or []:
                entity_id = str(level.get("id") or "").strip()
                if entity_id:
                    allowed.add(entity_id)

        self._legacy_allowed_entities_cache = allowed
        return allowed

    def _safe_refresh(self, instance) -> None:
        try:
            self._db.refresh(instance)
        except InvalidRequestError:
            return

    @contextmanager
    def _deferred_repository_rollback_guard(self, *, enabled: bool) -> Iterator[None]:
        repository_db = getattr(self._repo, "db", None)
        if not enabled or repository_db is not self._db:
            yield
            return

        self._repo.db = _ExternallyManagedWriteSession(self._db)
        try:
            yield
        finally:
            self._repo.db = repository_db

    def _commit(self, *, preserve_instances: bool = False) -> None:
        if not preserve_instances:
            self._db.commit()
            return

        previous = getattr(self._db, "expire_on_commit", None)
        if previous is None:
            self._db.commit()
            return

        self._db.expire_on_commit = False
        try:
            self._db.commit()
        finally:
            self._db.expire_on_commit = previous

    def _revisions_for_response(
        self, revisions: list[ValueRevision]
    ) -> list[ValueRevision]:
        if not revisions:
            return []
        if any(not isinstance(revision, ValueRevision) for revision in revisions):
            return revisions
        if all(
            getattr(revision, "context", None) is not None for revision in revisions
        ):
            return revisions

        revision_ids = [revision.id for revision in revisions]
        rows = (
            self._db.query(ValueRevision)
            .join(ValueContext, ValueContext.id == ValueRevision.context_id)
            .options(contains_eager(ValueRevision.context))
            .filter(ValueRevision.id.in_(revision_ids))
            .all()
        )
        by_id = {revision.id: revision for revision in rows}
        return [
            by_id.get(revision_id, revision)
            for revision_id, revision in zip(revision_ids, revisions)
        ]

    def _tenant_id(self) -> str:
        return self._tenant_id_value or settings.value_revision_default_tenant_id

    def _append_revision_for_record(self, record, *, created_by: Optional[str]):
        revision_input = self._revision_input_for_record(record, created_by=created_by)
        current_revision = self._current_revision_with_same_payload(revision_input)
        if current_revision is not None:
            return current_revision

        return self._revision_store.append_revision(
            revision_input,
            commit=False,
        ).revision

    def _append_revisions_for_records(
        self, records: Iterable, *, created_by: Optional[str], refresh: bool = True
    ) -> list[ValueRevision]:
        revision_inputs = [
            self._revision_input_for_record(record, created_by=created_by)
            for record in records
        ]
        current_by_key = self._current_revisions_with_same_payloads(revision_inputs)

        # Separate unchanged (idempotent skip) from new revisions
        unchanged: list[ValueRevision] = []
        to_append: list[Any] = []
        for revision_input in revision_inputs:
            key = self._current_revision_payload_key(revision_input)
            current_revision = current_by_key.get(key) if key is not None else None
            if current_revision is not None:
                unchanged.append(current_revision)
            else:
                to_append.append(revision_input)

        if not to_append:
            return unchanged

        # Use bulk path for new revisions, then merge back in input order
        bulk_results = self._revision_store.append_revisions_bulk(
            to_append,
            commit=False,
            refresh=refresh,
        )
        new_revisions = [r.revision for r in bulk_results]

        # Merge unchanged and new back into original input order
        revisions: list[ValueRevision] = []
        new_iter = iter(new_revisions)
        for revision_input in revision_inputs:
            key = self._current_revision_payload_key(revision_input)
            current_revision = current_by_key.get(key) if key is not None else None
            if current_revision is not None:
                revisions.append(current_revision)
            else:
                revisions.append(next(new_iter))
        return revisions

    def _revision_input_for_record(self, record, *, created_by: Optional[str]):
        return revision_input_for_esg_value(
            record,
            tenant_id=self._tenant_id(),
            state="approved",
            source_system="sds_values",
            external_key=record.external_key,
            use_legacy_external_key_default=False,
            revision_provenance="api_write",
            created_by=created_by or "value_store",
        )

    def _current_revision_with_same_payload(self, revision_input):
        key = self._current_revision_payload_key(revision_input)
        if key is None:
            return None
        return self._current_revisions_with_same_payloads([revision_input]).get(key)

    def _current_revisions_with_same_payloads(self, revision_inputs):
        keys = [
            key
            for key in (
                self._current_revision_payload_key(revision_input)
                for revision_input in revision_inputs
            )
            if key is not None
        ]
        if not keys:
            return {}

        tenant_ids = {key[0] for key in keys}
        external_keys = {key[1] for key in keys}
        payload_hashes = {key[2] for key in keys}
        context_hashes = {key[3] for key in keys}
        rows = (
            self._db.query(ValueRevision, ValueContext.context_hash)
            .join(
                CurrentValuePointer,
                and_(
                    CurrentValuePointer.context_id == ValueRevision.context_id,
                    CurrentValuePointer.revision_id == ValueRevision.id,
                ),
            )
            .join(ValueContext, ValueContext.id == ValueRevision.context_id)
            .filter(
                ValueRevision.tenant_id.in_(tenant_ids),
                ValueRevision.source_system == "sds_values",
                ValueRevision.external_key.in_(external_keys),
                ValueRevision.source_payload_hash.in_(payload_hashes),
                ValueContext.context_hash.in_(context_hashes),
                CurrentValuePointer.tenant_id.in_(tenant_ids),
            )
            .all()
        )
        current_by_key = {}
        for revision, context_hash in rows:
            key = (
                revision.tenant_id,
                revision.external_key,
                revision.source_payload_hash,
                context_hash,
            )
            current_by_key[key] = revision
        return current_by_key

    @staticmethod
    def _current_revision_payload_key(revision_input):
        if not revision_input.external_key or not revision_input.source_payload_hash:
            return None
        context_hash = build_context_hash(revision_input.context_identity)
        return (
            revision_input.context_identity.tenant_id,
            revision_input.external_key,
            revision_input.source_payload_hash,
            context_hash,
        )

    def _current_revision_query(self):
        tenant_id = self._tenant_id()
        return (
            self._db.query(ValueRevision)
            .join(
                CurrentValuePointer,
                and_(
                    CurrentValuePointer.revision_id == ValueRevision.id,
                    CurrentValuePointer.context_id == ValueRevision.context_id,
                ),
            )
            .join(ValueContext, ValueContext.id == ValueRevision.context_id)
            .filter(
                CurrentValuePointer.tenant_id == tenant_id,
                ValueRevision.tenant_id == tenant_id,
                ValueContext.tenant_id == tenant_id,
            )
        )

    @staticmethod
    def _with_context_eager_loading(query):
        return query.options(contains_eager(ValueRevision.context))

    def _apply_revision_filters(
        self,
        query,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime],
    ):
        if concept:
            query = query.filter(_concept_or_source_standard_filter(concept))
        if entity:
            query = query.filter(ValueContext.entity_id == entity)
        if period_start:
            query = query.filter(ValueContext.period_end >= period_start)
        if period_end:
            query = query.filter(ValueContext.period_end <= period_end)
        if unit:
            query = query.filter(ValueRevision.unit == unit)
        if changed_since:
            query = query.filter(ValueRevision.created_at >= changed_since)
        return query

    def _list_current_revisions(
        self,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime],
        limit: int,
        offset: int,
    ) -> Tuple[List[ValueRevision], int]:
        query = self._apply_revision_filters(
            self._current_revision_query(),
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
            unit=unit,
            changed_since=changed_since,
        )
        total = query.count()
        cursor_value = _revision_cursor_value_expr()
        return (
            self._with_context_eager_loading(query)
            .order_by(
                ValueContext.period_end.desc(),
                ValueRevision.created_at.desc(),
                cursor_value.desc(),
            )
            .offset(offset)
            .limit(limit)
            .all(),
            total,
        )

    def _list_current_revision_page(
        self,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime],
        limit: int,
        offset: int,
    ) -> List[ValueRevision]:
        query = self._apply_revision_filters(
            self._current_revision_query(),
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
            unit=unit,
            changed_since=changed_since,
        )
        cursor_value = _revision_cursor_value_expr()
        return (
            self._with_context_eager_loading(query)
            .order_by(
                ValueContext.period_end.desc(),
                ValueRevision.created_at.desc(),
                cursor_value.desc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )

    def _latest_current_revision(
        self,
        *,
        concept: str,
        entity: str,
        period_start: date,
        period_end: date,
    ) -> Optional[ValueRevision]:
        query = self._apply_revision_filters(
            self._current_revision_query(),
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
            unit=None,
            changed_since=None,
        )
        cursor_value = _revision_cursor_value_expr()
        return (
            self._with_context_eager_loading(query)
            .order_by(
                ValueContext.period_end.desc(),
                ValueRevision.created_at.desc(),
                cursor_value.desc(),
            )
            .limit(1)
            .first()
        )

    def _list_current_revisions_after_cursor(
        self,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime],
        limit: int,
        cursor_period: date,
        cursor_created_at: datetime,
        cursor_value_id: str,
    ) -> Tuple[List[ValueRevision], int]:
        query = self._apply_revision_filters(
            self._current_revision_query(),
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
            unit=unit,
            changed_since=changed_since,
        )
        total = query.count()
        cursor_value = _revision_cursor_value_expr()
        query = query.filter(
            or_(
                ValueContext.period_end < cursor_period,
                and_(
                    ValueContext.period_end == cursor_period,
                    ValueRevision.created_at < cursor_created_at,
                ),
                and_(
                    ValueContext.period_end == cursor_period,
                    ValueRevision.created_at == cursor_created_at,
                    cursor_value < cursor_value_id,
                ),
            )
        )
        return (
            self._with_context_eager_loading(query)
            .order_by(
                ValueContext.period_end.desc(),
                ValueRevision.created_at.desc(),
                cursor_value.desc(),
            )
            .limit(limit + 1)
            .all(),
            total,
        )

    def _list_current_revisions_after_change_cursor(
        self,
        *,
        concept: Optional[str],
        entity: Optional[str],
        period_start: Optional[date],
        period_end: Optional[date],
        unit: Optional[str],
        changed_since: Optional[datetime],
        limit: int,
        cursor_changed_at: Optional[datetime],
        cursor_value_id: Optional[str],
    ) -> Tuple[List[ValueRevision], int]:
        query = self._apply_revision_filters(
            self._current_revision_query(),
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
            unit=unit,
            changed_since=changed_since,
        )
        total = query.count()
        cursor_value = _revision_cursor_value_expr()
        if cursor_changed_at is not None:
            query = query.filter(
                or_(
                    ValueRevision.created_at > cursor_changed_at,
                    and_(
                        ValueRevision.created_at == cursor_changed_at,
                        cursor_value > (cursor_value_id or ""),
                    ),
                )
            )
        return (
            self._with_context_eager_loading(query)
            .order_by(ValueRevision.created_at.asc(), cursor_value.asc())
            .limit(limit)
            .all(),
            total,
        )

    def _get_current_revision(self, value_id: str):
        legacy_external_key = f"legacy-esg-value:{value_id}"
        return (
            self._with_context_eager_loading(self._current_revision_query())
            .filter(
                or_(
                    ValueRevision.id == value_id,
                    ValueRevision.source_record_id == value_id,
                    ValueRevision.external_key == value_id,
                    ValueRevision.external_key == legacy_external_key,
                )
            )
            .order_by(
                ValueRevision.created_at.desc(),
                ValueRevision.revision_number.desc(),
                ValueRevision.id.desc(),
            )
            .first()
        )

    def _to_revision_response(self, revision: ValueRevision) -> ValueResponse:
        context = revision.context
        period = context.period_end or context.period_start
        if period is None:
            period = date.fromisoformat(context.reporting_period_id)
        metadata = dict(revision.materiality_metadata or {})
        value_response_metadata = metadata.get(VALUE_RESPONSE_METADATA_KEY)
        if not isinstance(value_response_metadata, Mapping):
            value_response_metadata = {}
        metadata["value_revision"] = {
            "revision_id": revision.id,
            "revision_number": revision.revision_number,
            "revision_state": revision.state,
            "context_id": context.id,
            "context_hash": context.context_hash,
            "canonical_concept_id": getattr(context, "canonical_concept_id", None),
            "canonical_uri": getattr(context, "canonical_uri", None),
            "source_observation_type": getattr(
                context,
                "source_observation_type",
                "standard_direct_legacy",
            ),
            "standard_release_id": context.standard_release_id,
            "standard_datapoint_id": context.standard_datapoint_id,
            "source_system": revision.source_system,
            "source_record_id": revision.source_record_id,
        }
        return ValueResponse(
            id=revision.source_record_id or revision.id,
            concept=context.indicator_identifier,
            entity=context.entity_id,
            period=period,
            external_key=revision.external_key,
            value=_revision_value(revision),
            original_value=revision.original_value,
            value_type=revision.value_kind,
            unit=revision.unit or "",
            original_unit=revision.original_unit,
            conversion_applied=_metadata_bool(
                value_response_metadata,
                "conversion_applied",
                default=bool(revision.conversion_trace),
            ),
            currency=revision.currency,
            original_currency=revision.original_currency,
            currency_conversion_applied=_metadata_bool(
                value_response_metadata,
                "currency_conversion_applied",
                default=False,
            ),
            conversion_trace=revision.conversion_trace,
            value_date=_metadata_date(value_response_metadata, "value_date"),
            period_start=context.period_start,
            period_end=context.period_end,
            metadata=metadata,
            created_at=revision.created_at,
            updated_at=revision.created_at,
        )

    @staticmethod
    def _to_response(record) -> ValueResponse:
        return ValueResponse(
            id=record.id,
            concept=record.concept,
            entity=record.entity,
            period=record.period,
            external_key=getattr(record, "external_key", None),
            value=_record_value(record),
            original_value=getattr(record, "original_value", None),
            value_type=getattr(record, "value_type", "numeric") or "numeric",
            unit=record.unit,
            original_unit=record.original_unit,
            conversion_applied=record.conversion_applied,
            currency=getattr(record, "currency", None),
            original_currency=getattr(record, "original_currency", None),
            currency_conversion_applied=getattr(
                record, "currency_conversion_applied", False
            ),
            conversion_trace=getattr(record, "conversion_trace", None),
            value_date=getattr(record, "value_date", None),
            period_start=getattr(record, "period_start", None),
            period_end=getattr(record, "period_end", None),
            metadata=getattr(record, "value_metadata", None),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


def get_value_store(
    request: Request,
    db: Optional[Session] = Depends(get_db_optional),
    tenant_id: Optional[str] = None,
):
    """Dependency that returns the configured store (DB when required)."""
    if settings.require_database:
        if db is None:
            raise HTTPException(status_code=503, detail="Database session unavailable")
        return DatabaseValueStore(db, tenant_id=tenant_id)

    store = getattr(request.app.state, "value_store", None)
    if store is None:
        store = InMemoryValueStore()
        request.app.state.value_store = store
    return store


def revision_value_store_needs_tenant() -> bool:
    return (
        settings.value_revision_primary_read_path == "revision"
        or settings.value_revision_dual_write_enabled
    )


_PLACEHOLDER_COMPANY_IDS = frozenset(
    {
        "string",
        "company_id",
        "tenant",
        "tenant_id",
        "company_001",
        "company_123",
    }
)


def _is_placeholder_company_id(value: Any) -> bool:
    return str(value or "").strip().lower() in _PLACEHOLDER_COMPANY_IDS


def resolve_value_store_tenant_id(current_user: Any) -> str:
    company_id = str(getattr(current_user, "company_id", None) or "").strip()
    if company_id and not _is_placeholder_company_id(company_id):
        return company_id
    raise HTTPException(
        status_code=403,
        detail="Access denied: user is not assigned to a tenant",
    )


def _infer_value_type(value: ValueScalar) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "narrative"
    return "numeric"


def _concept_or_source_standard_filter(concept: str):
    return or_(
        ValueContext.indicator_identifier == concept,
        ValueRevision.materiality_metadata["source_standard_datapoint_id"].astext
        == concept,
    )


def _record_value(record) -> ValueScalar:
    value_type = getattr(record, "value_type", None) or "numeric"
    if value_type == "boolean":
        return bool(getattr(record, "boolean_value", False))
    if value_type in {"narrative", "semi-narrative", "text"}:
        return getattr(record, "text_value", "") or ""
    return record.value


def _revision_value(revision: ValueRevision) -> ValueScalar:
    value_kind = getattr(revision, "value_kind", None) or "numeric"
    if value_kind == "boolean":
        return bool(getattr(revision, "boolean_value", False))
    if value_kind in {"narrative", "semi-narrative", "text"}:
        return getattr(revision, "text_value", "") or ""
    numeric_value = getattr(revision, "numeric_value", None)
    if numeric_value is not None:
        return numeric_value
    canonical_value = getattr(revision, "canonical_value", None)
    return Decimal(str(canonical_value)) if canonical_value is not None else Decimal(0)


def _revision_cursor_value_expr():
    return func.coalesce(ValueRevision.source_record_id, ValueRevision.id)


def _metadata_bool(
    metadata: Mapping[str, Any],
    key: str,
    *,
    default: bool,
) -> bool:
    value = metadata.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return default


def _metadata_date(metadata: Mapping[str, Any], key: str) -> date | None:
    value = metadata.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
