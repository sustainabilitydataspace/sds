"""Repository for persisted ESG values."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from sqlalchemy import and_, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.services.value_ingest import PreparedValueRecord
from src.services.value_pagination import decode_value_cursor, encode_value_cursor

from ..models import ESGValue
from ..semantic_guard import ensure_value_operation_is_isolated

ValueScalar = Union[bool, float, int, Decimal, str]


class ValueRepository:
    """Repository for ESG value persistence operations."""

    def __init__(self, db: Session):
        self.db = db

    def create_value(
        self,
        *,
        value_id: str,
        concept: str,
        entity: str,
        period: date,
        value: ValueScalar,
        unit: str,
        value_type: Optional[str] = None,
        original_value: Optional[ValueScalar] = None,
        original_unit: Optional[str] = None,
        conversion_applied: bool = False,
        currency: Optional[str] = None,
        original_currency: Optional[str] = None,
        currency_conversion_applied: bool = False,
        conversion_trace: Optional[list[dict[str, Any]]] = None,
        value_date: Optional[date] = None,
        period_start: Optional[date] = None,
        period_end: Optional[date] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_by: Optional[str] = None,
        external_key: Optional[str] = None,
        commit: bool = True,
    ) -> ESGValue:
        record = ESGValue(
            id=value_id,
            concept=concept,
            entity=entity,
            period=period,
            external_key=external_key,
            **_value_columns(value, value_type),
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
            value_metadata=metadata or {},
            created_by=created_by,
        )
        try:
            self.db.add(record)
            ensure_value_operation_is_isolated(self.db, operation="create_value")
            if commit:
                self.db.commit()
                self.db.refresh(record)
            else:
                self.db.flush()
            return record
        except Exception:
            self.db.rollback()
            raise

    def create_values(
        self,
        *,
        records: List[PreparedValueRecord],
        created_by: Optional[str] = None,
        commit: bool = True,
    ) -> List[ESGValue]:
        persisted: List[ESGValue] = []
        try:
            for item in records:
                record = ESGValue(
                    id=item.value_id,
                    concept=item.concept,
                    entity=item.entity,
                    period=item.period,
                    external_key=item.external_key,
                    **_value_columns(item.value, item.value_type),
                    unit=item.unit,
                    original_value=item.original_value,
                    original_unit=item.original_unit,
                    conversion_applied=item.conversion_applied,
                    currency=item.currency,
                    original_currency=item.original_currency,
                    currency_conversion_applied=item.currency_conversion_applied,
                    conversion_trace=item.conversion_trace,
                    value_date=item.value_date,
                    period_start=item.period_start,
                    period_end=item.period_end,
                    value_metadata=item.metadata or {},
                    created_by=created_by,
                )
                persisted.append(record)
                self.db.add(record)

            ensure_value_operation_is_isolated(self.db, operation="create_values")
            if commit:
                self.db.commit()
                for record in persisted:
                    self.db.refresh(record)
            else:
                self.db.flush()
            return persisted
        except Exception:
            self.db.rollback()
            raise

    def save_values(
        self,
        *,
        records: List[PreparedValueRecord],
        created_by: Optional[str] = None,
        commit: bool = True,
        refresh: bool = True,
    ) -> List[ESGValue]:
        if not records:
            return []

        insert_only = [record for record in records if not record.external_key]
        upsert_records = [record for record in records if record.external_key]
        persisted: List[ESGValue] = []
        persisted_by_input_key: Dict[str, ESGValue] = {}

        try:
            for item in insert_only:
                record = ESGValue(
                    id=item.value_id,
                    concept=item.concept,
                    entity=item.entity,
                    period=item.period,
                    external_key=None,
                    **_value_columns(item.value, item.value_type),
                    unit=item.unit,
                    original_value=item.original_value,
                    original_unit=item.original_unit,
                    conversion_applied=item.conversion_applied,
                    currency=item.currency,
                    original_currency=item.original_currency,
                    currency_conversion_applied=item.currency_conversion_applied,
                    conversion_trace=item.conversion_trace,
                    value_date=item.value_date,
                    period_start=item.period_start,
                    period_end=item.period_end,
                    value_metadata=item.metadata or {},
                    created_by=created_by,
                )
                persisted.append(record)
                persisted_by_input_key[item.value_id] = record
                self.db.add(record)

            if upsert_records:
                upserted = self._save_values_bulk_upsert(
                    upsert_records,
                    created_by=created_by,
                )
                persisted.extend(upserted)
                for record in upserted:
                    persisted_by_input_key[record.external_key] = record

            ensure_value_operation_is_isolated(self.db, operation="save_values")
            if commit:
                self.db.commit()
            else:
                self.db.flush()
            if refresh:
                for record in persisted:
                    self.db.refresh(record)
            return [
                persisted_by_input_key[item.external_key or item.value_id]
                for item in records
            ]
        except Exception:
            self.db.rollback()
            raise

    def _save_values_bulk_upsert(
        self,
        records: List[PreparedValueRecord],
        created_by: Optional[str] = None,
    ) -> List[ESGValue]:
        """Bulk insert ESG values with external_key using a single SQL statement.

        Returns hydrated ESGValue instances ordered to match *records*.
        """
        if not records:
            return []

        value_rows: List[dict[str, Any]] = []
        for item in records:
            value_columns = _value_columns(item.value, item.value_type)
            value_rows.append(
                {
                    "id": item.value_id,
                    "concept": item.concept,
                    "entity": item.entity,
                    "period": item.period,
                    "external_key": item.external_key,
                    **value_columns,
                    "unit": item.unit,
                    "original_value": item.original_value,
                    "original_unit": item.original_unit,
                    "conversion_applied": item.conversion_applied,
                    "currency": item.currency,
                    "original_currency": item.original_currency,
                    "currency_conversion_applied": item.currency_conversion_applied,
                    "conversion_trace": item.conversion_trace,
                    "value_date": item.value_date,
                    "period_start": item.period_start,
                    "period_end": item.period_end,
                    "value_metadata": item.metadata or {},
                    "created_by": created_by,
                }
            )

        insert_stmt = pg_insert(ESGValue).values(value_rows)
        stmt = insert_stmt.on_conflict_do_nothing(
            index_elements=["external_key"],
            index_where=ESGValue.external_key.isnot(None),
        ).returning(ESGValue)
        result = self.db.execute(stmt)
        returned = list(result.scalars().all())
        if len(returned) != len(records):
            raise RuntimeError("External key collision during value write")

        # Map returned records by external_key so we can preserve input order.
        by_external_key = {r.external_key: r for r in returned}
        ordered: List[ESGValue] = []
        for item in records:
            record = by_external_key.get(item.external_key)
            if record is None:
                raise RuntimeError("External key collision during value write")
            ordered.append(record)
        return ordered

    def get_value_by_id(
        self, value_id: str, *, allowed_entities: Optional[set[str]] = None
    ) -> Optional[ESGValue]:
        query = self.db.query(ESGValue).filter(ESGValue.id == value_id)
        query = self._apply_allowed_entities(query, allowed_entities)
        return query.first()

    def list_values(
        self,
        *,
        concept: Optional[str] = None,
        entity: Optional[str] = None,
        period_start: Optional[date] = None,
        period_end: Optional[date] = None,
        unit: Optional[str] = None,
        changed_since: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
        allowed_entities: Optional[set[str]] = None,
    ) -> Tuple[List[ESGValue], int]:
        query = self._build_filtered_query(
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
            unit=unit,
            changed_since=changed_since,
            allowed_entities=allowed_entities,
        )

        total = query.count()
        items = (
            query.order_by(
                ESGValue.period.desc(), ESGValue.created_at.desc(), ESGValue.id.desc()
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        return items, total

    def list_values_cursor(
        self,
        *,
        concept: Optional[str] = None,
        entity: Optional[str] = None,
        period_start: Optional[date] = None,
        period_end: Optional[date] = None,
        unit: Optional[str] = None,
        changed_since: Optional[datetime] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        allowed_entities: Optional[set[str]] = None,
    ) -> Tuple[List[ESGValue], int, Optional[str], bool]:
        base_query = self._build_filtered_query(
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
            unit=unit,
            changed_since=changed_since,
            allowed_entities=allowed_entities,
        )
        total = base_query.count()
        query = base_query

        if cursor:
            decoded = decode_value_cursor(cursor)
            query = query.filter(
                or_(
                    ESGValue.period < decoded.period,
                    and_(
                        ESGValue.period == decoded.period,
                        ESGValue.created_at < decoded.created_at,
                    ),
                    and_(
                        ESGValue.period == decoded.period,
                        ESGValue.created_at == decoded.created_at,
                        ESGValue.id < decoded.value_id,
                    ),
                )
            )

        items = (
            query.order_by(
                ESGValue.period.desc(), ESGValue.created_at.desc(), ESGValue.id.desc()
            )
            .limit(limit + 1)
            .all()
        )
        has_more = len(items) > limit
        selected = items[:limit]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = encode_value_cursor(
                period=last.period, created_at=last.created_at, value_id=last.id
            )
        return selected, total, next_cursor, has_more

    def get_latest_value(
        self,
        *,
        concept: str,
        entity: str,
        period_start: date,
        period_end: date,
        allowed_entities: Optional[set[str]] = None,
    ) -> Optional[ESGValue]:
        return (
            self._build_filtered_query(
                concept=concept,
                entity=entity,
                period_start=period_start,
                period_end=period_end,
                unit=None,
                changed_since=None,
                allowed_entities=allowed_entities,
            )
            .order_by(
                ESGValue.period.desc(),
                ESGValue.updated_at.desc(),
                ESGValue.created_at.desc(),
                ESGValue.id.desc(),
            )
            .limit(1)
            .first()
        )

    def iter_values(
        self,
        *,
        concept: Optional[str] = None,
        entity: Optional[str] = None,
        period_start: Optional[date] = None,
        period_end: Optional[date] = None,
        unit: Optional[str] = None,
        changed_since: Optional[datetime] = None,
        batch_size: int = 500,
        allowed_entities: Optional[set[str]] = None,
    ) -> Iterable[ESGValue]:
        query = (
            self._build_filtered_query(
                concept=concept,
                entity=entity,
                period_start=period_start,
                period_end=period_end,
                unit=unit,
                changed_since=changed_since,
                allowed_entities=allowed_entities,
            )
            .order_by(
                ESGValue.period.desc(), ESGValue.created_at.desc(), ESGValue.id.desc()
            )
            .yield_per(batch_size)
        )
        yield from query

    def list_value_changes(
        self,
        *,
        concept: Optional[str] = None,
        entity: Optional[str] = None,
        period_start: Optional[date] = None,
        period_end: Optional[date] = None,
        unit: Optional[str] = None,
        changed_since: Optional[datetime] = None,
        limit: int = 100,
        cursor_changed_at: Optional[datetime] = None,
        cursor_value_id: Optional[str] = None,
        allowed_entities: Optional[set[str]] = None,
    ) -> Tuple[List[ESGValue], Optional[tuple[str, str]], bool]:
        query = self._build_filtered_query(
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
            unit=unit,
            changed_since=changed_since,
            allowed_entities=allowed_entities,
        )

        if cursor_changed_at is not None:
            query = query.filter(
                or_(
                    ESGValue.updated_at > cursor_changed_at,
                    and_(
                        ESGValue.updated_at == cursor_changed_at,
                        ESGValue.id > (cursor_value_id or ""),
                    ),
                )
            )

        items = (
            query.order_by(ESGValue.updated_at.asc(), ESGValue.id.asc())
            .limit(limit + 1)
            .all()
        )
        has_more = len(items) > limit
        selected = items[:limit]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = last.updated_at.isoformat(), last.id
        return selected, next_cursor, has_more

    def delete_value(
        self, value_id: str, *, allowed_entities: Optional[set[str]] = None
    ) -> bool:
        record = self.get_value_by_id(value_id, allowed_entities=allowed_entities)
        if not record:
            return False
        try:
            self.db.delete(record)
            ensure_value_operation_is_isolated(self.db, operation="delete_value")
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise

    def _build_filtered_query(
        self,
        *,
        concept: Optional[str] = None,
        entity: Optional[str] = None,
        period_start: Optional[date] = None,
        period_end: Optional[date] = None,
        unit: Optional[str] = None,
        changed_since: Optional[datetime] = None,
        allowed_entities: Optional[set[str]] = None,
    ):
        query = self.db.query(ESGValue)
        query = self._apply_allowed_entities(query, allowed_entities)

        if concept:
            query = query.filter(ESGValue.concept == concept)
        if entity:
            query = query.filter(ESGValue.entity == entity)
        if period_start:
            query = query.filter(ESGValue.period >= period_start)
        if period_end:
            query = query.filter(ESGValue.period <= period_end)
        if unit:
            query = query.filter(ESGValue.unit == unit)
        if changed_since:
            query = query.filter(
                or_(
                    ESGValue.updated_at >= changed_since,
                    ESGValue.created_at >= changed_since,
                )
            )

        return query

    @staticmethod
    def _apply_allowed_entities(query, allowed_entities: Optional[set[str]]):
        if allowed_entities is None:
            return query
        return query.filter(ESGValue.entity.in_(sorted(allowed_entities)))


def _value_columns(
    value: ValueScalar, value_type: Optional[str] = None
) -> dict[str, Any]:
    normalized_type = _normalize_value_type(value_type, value)
    if normalized_type == "boolean":
        return {
            "value": None,
            "value_type": "boolean",
            "text_value": None,
            "boolean_value": bool(value),
        }
    if normalized_type in {"narrative", "semi-narrative"}:
        return {
            "value": None,
            "value_type": normalized_type,
            "text_value": str(value),
            "boolean_value": None,
        }
    return {
        "value": value,
        "value_type": "numeric",
        "text_value": None,
        "boolean_value": None,
    }


def _normalize_value_type(raw_value_type: Optional[str], value: ValueScalar) -> str:
    value_type = (raw_value_type or "").strip().lower().replace("_", "-")
    if value_type in {"number", "numeric", "decimal"}:
        return "numeric"
    if value_type in {"boolean", "bool"}:
        return "boolean"
    if value_type in {"text", "string", "narrative"}:
        return "narrative"
    if value_type in {"semi-narrative", "seminarrative"}:
        return "semi-narrative"
    if value_type:
        return value_type
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "narrative"
    return "numeric"
