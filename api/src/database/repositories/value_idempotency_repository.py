"""Repository for request-level idempotency on value writes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from ..models import ValueIdempotencyKey


class ValueIdempotencyRepository:
    """Persistence operations for value-write idempotency keys."""

    def __init__(self, db: Session):
        self.db = db

    def get(
        self,
        *,
        tenant_id: str,
        user_id: str,
        scope: str,
        idempotency_key: str,
    ) -> Optional[ValueIdempotencyKey]:
        _require_identity("tenant_id", tenant_id)
        _require_identity("user_id", user_id)
        return (
            self.db.query(ValueIdempotencyKey)
            .filter(
                ValueIdempotencyKey.tenant_id == tenant_id,
                ValueIdempotencyKey.user_id == user_id,
                ValueIdempotencyKey.scope == scope,
                ValueIdempotencyKey.idempotency_key == idempotency_key,
                ValueIdempotencyKey.ownership_state == "resolved",
            )
            .first()
        )

    def create_claim(
        self,
        *,
        tenant_id: str,
        user_id: str,
        scope: str,
        idempotency_key: str,
        request_hash: str,
        ttl_seconds: int,
        commit: bool = True,
    ) -> ValueIdempotencyKey:
        _require_identity("tenant_id", tenant_id)
        _require_identity("user_id", user_id)
        now = datetime.now(timezone.utc)
        record = ValueIdempotencyKey(
            tenant_id=tenant_id,
            ownership_state="resolved",
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

    def get_quarantined(
        self,
        *,
        user_id: str,
        scope: str,
        idempotency_key: str,
    ) -> Optional[ValueIdempotencyKey]:
        _require_identity("user_id", user_id)
        return (
            self.db.query(ValueIdempotencyKey)
            .filter(
                ValueIdempotencyKey.tenant_id.is_(None),
                ValueIdempotencyKey.user_id == user_id,
                ValueIdempotencyKey.scope == scope,
                ValueIdempotencyKey.idempotency_key == idempotency_key,
                ValueIdempotencyKey.ownership_state == "quarantined",
            )
            .order_by(
                ValueIdempotencyKey.expires_at.desc(),
                ValueIdempotencyKey.id.desc(),
            )
            .first()
        )

    def complete(
        self,
        *,
        tenant_id: str,
        user_id: str,
        scope: str,
        idempotency_key: str,
        record_id: int,
        response_status: int,
        response_body: dict,
        commit: bool = True,
    ) -> bool:
        _require_identity("tenant_id", tenant_id)
        _require_identity("user_id", user_id)
        result = self.db.execute(
            update(ValueIdempotencyKey)
            .where(
                ValueIdempotencyKey.id == record_id,
                ValueIdempotencyKey.tenant_id == tenant_id,
                ValueIdempotencyKey.user_id == user_id,
                ValueIdempotencyKey.scope == scope,
                ValueIdempotencyKey.idempotency_key == idempotency_key,
                ValueIdempotencyKey.ownership_state == "resolved",
                ValueIdempotencyKey.state == "in_progress",
            )
            .values(
                state="completed",
                response_status=response_status,
                response_body=response_body,
                updated_at=datetime.now(timezone.utc),
            )
        )
        if result.rowcount != 1:
            raise ValueError("Idempotency claim not in progress for this authority")
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return True

    def abandon(
        self,
        *,
        tenant_id: str,
        user_id: str,
        scope: str,
        idempotency_key: str,
        record_id: int,
        commit: bool = True,
    ) -> bool:
        _require_identity("tenant_id", tenant_id)
        _require_identity("user_id", user_id)
        result = self.db.execute(
            delete(ValueIdempotencyKey).where(
                ValueIdempotencyKey.id == record_id,
                ValueIdempotencyKey.tenant_id == tenant_id,
                ValueIdempotencyKey.user_id == user_id,
                ValueIdempotencyKey.scope == scope,
                ValueIdempotencyKey.idempotency_key == idempotency_key,
                ValueIdempotencyKey.ownership_state == "resolved",
                ValueIdempotencyKey.state == "in_progress",
            )
        )
        if result.rowcount != 1:
            return False
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return True

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


def _require_identity(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
