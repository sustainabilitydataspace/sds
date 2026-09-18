"""Repository for user account persistence operations."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models import UserAccount


class UserRepository:
    """Repository for UserAccount CRUD operations."""

    def __init__(self, db: Session):
        self.db = db

    def create_user(
        self,
        *,
        user_id: str,
        username: str,
        email: str,
        full_name: Optional[str],
        company_id: Optional[str],
        role: str,
        password_hash: str,
        is_active: bool,
    ) -> UserAccount:
        record = UserAccount(
            id=user_id,
            username=username,
            email=email,
            full_name=full_name,
            company_id=company_id,
            role=role,
            password_hash=password_hash,
            is_active=is_active,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def get_user_by_username(self, username: str) -> Optional[UserAccount]:
        return (
            self.db.query(UserAccount).filter(UserAccount.username == username).first()
        )

    def get_user_by_id(self, user_id: str) -> Optional[UserAccount]:
        return self.db.query(UserAccount).filter(UserAccount.id == user_id).first()

    def update_user(self, user_id: str, **kwargs) -> Optional[UserAccount]:
        record = self.get_user_by_id(user_id)
        if not record:
            return None
        for key, value in kwargs.items():
            if hasattr(record, key) and value is not None:
                setattr(record, key, value)
        self.db.commit()
        self.db.refresh(record)
        return record

    def update_password_hash(self, user_id: str, password_hash: str) -> bool:
        record = self.get_user_by_id(user_id)
        if not record:
            return False
        record.password_hash = password_hash
        self.db.commit()
        return True

    def set_last_login(self, user_id: str, *, last_login: datetime) -> bool:
        record = self.get_user_by_id(user_id)
        if not record:
            return False
        record.last_login = last_login
        self.db.commit()
        return True
