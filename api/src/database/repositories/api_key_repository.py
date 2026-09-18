"""Repository for API key persistence operations."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from ..models import APIKeyRecord


class APIKeyRepository:
    """Repository for APIKeyRecord CRUD operations."""

    def __init__(self, db: Session):
        self.db = db

    def create_api_key(
        self,
        *,
        key_id: str,
        user_id: str,
        name: str,
        description: Optional[str],
        secret_hash: str,
        permissions: Sequence[str],
        expires_at: Optional[datetime],
    ) -> APIKeyRecord:
        record = APIKeyRecord(
            id=key_id,
            user_id=user_id,
            name=name,
            description=description,
            secret_hash=secret_hash,
            permissions=list(permissions),
            expires_at=expires_at,
            is_active=True,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def get_api_key_by_id(self, key_id: str) -> Optional[APIKeyRecord]:
        return self.db.query(APIKeyRecord).filter(APIKeyRecord.id == key_id).first()

    def list_api_keys_for_user(self, user_id: str) -> list[APIKeyRecord]:
        return list(
            self.db.query(APIKeyRecord).filter(APIKeyRecord.user_id == user_id).all()
        )

    def revoke_api_key(self, key_id: str) -> bool:
        record = self.get_api_key_by_id(key_id)
        if not record:
            return False
        record.is_active = False
        self.db.commit()
        return True

    def set_last_used(self, key_id: str, *, last_used: datetime) -> bool:
        record = self.get_api_key_by_id(key_id)
        if not record:
            return False
        record.last_used = last_used
        self.db.commit()
        return True
