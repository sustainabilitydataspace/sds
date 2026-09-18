"""Repository for request-level idempotency on value writes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models import ValueIdempotencyKey


class ValueIdempotencyRepository:
    """Persistence operations for value-write idempotency keys."""

    def __init__(self, db: Session):
        self.db = db

    def get(
        self,
        *,
        user_id: str,
        scope: str,
        idempotency_key: str,
    ) -> Optional[ValueIdempotencyKey]:
        return (
            self.db.query(ValueIdempotencyKey)
            .filter(
                ValueIdempotencyKey.user_id == user_id,
                ValueIdempotencyKey.scope == scope,
                ValueIdempotencyKey.idempotency_key == idempotency_key,
            )
            .first()
        )

    def create_claim(
        self,
        *,
        user_id: str,
        scope: str,
        idempotency_key: str,
        request_hash: str,
        ttl_seconds: int,
        commit: bool = True,
    ) -> ValueIdempotencyKey:
        now = datetime.now(timezone.utc)
        record = ValueIdempotencyKey(
            user_id=user_id,
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            state="in_progress",
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self.db.add(record)
        if commit:
            self.db.commit()
            self.db.refresh(record)
        else:
            self.db.flush()
        return record

    def complete(
        self,
        *,
        record_id: int,
        response_status: int,
        response_body: dict,
        commit: bool = True,
    ) -> ValueIdempotencyKey:
        record = (
            self.db.query(ValueIdempotencyKey)
            .filter(ValueIdempotencyKey.id == record_id)
            .first()
        )
        if record is None:
            raise ValueError(f"Idempotency record not found: {record_id}")

        record.state = "completed"
        record.response_status = response_status
        record.response_body = response_body
        if commit:
            self.db.commit()
            self.db.refresh(record)
        else:
            self.db.flush()
        return record

    def delete(self, *, record_id: int, commit: bool = True) -> None:
        record = (
            self.db.query(ValueIdempotencyKey)
            .filter(ValueIdempotencyKey.id == record_id)
            .first()
        )
        if record is None:
            return
        self.db.delete(record)
        if commit:
            self.db.commit()
        else:
            self.db.flush()

    def rollback(self) -> None:
        self.db.rollback()
