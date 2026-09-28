"""Repository for user account persistence operations."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Optional

from sqlalchemy import func
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

    def update_user(
        self, user_id: str, *, increment_auth_version: bool = False, **kwargs
    ) -> Optional[UserAccount]:
        values = {
            key: value
            for key, value in kwargs.items()
            if hasattr(UserAccount, key) and value is not None
        }
        if increment_auth_version:
            values["auth_version"] = func.coalesce(UserAccount.auth_version, 0) + 1
        if not values:
            return self.get_user_by_id(user_id)
        updated = (
            self.db.query(UserAccount)
            .filter(UserAccount.id == user_id)
            .update(values, synchronize_session=False)
        )
        if updated != 1:
            self.db.rollback()
            return None
        self.db.commit()
        record = self.get_user_by_id(user_id)
        if record is None:
            return None
        self.db.refresh(record)
        return record

    def update_password_hash_if_current(
        self,
        *,
        user_id: str,
        observed_password_hash: str,
        observed_auth_version: int,
        password_hash: str,
    ) -> bool:
        """Replace a password only while the verified credential state is current."""
        updated = (
            self.db.query(UserAccount)
            .filter(
                UserAccount.id == user_id,
                UserAccount.password_hash == observed_password_hash,
                UserAccount.auth_version == observed_auth_version,
                UserAccount.is_active.is_(True),
            )
            .update(
                {
                    "password_hash": password_hash,
                    "auth_version": UserAccount.auth_version + 1,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            self.db.rollback()
            return False
        self.db.commit()
        return True

    def invalidate_sessions(self, user_id: str) -> bool:
        updated = (
            self.db.query(UserAccount)
            .filter(UserAccount.id == user_id)
            .update(
                {"auth_version": func.coalesce(UserAccount.auth_version, 0) + 1},
                synchronize_session=False,
            )
        )
        if updated != 1:
            self.db.rollback()
            return False
        self.db.commit()
        return True

    def record_successful_login_if_current(
        self,
        *,
        user_id: str,
        observed_password_hash: str,
        observed_auth_version: int,
        last_login: datetime,
    ) -> Optional[SimpleNamespace]:
        """Record login only if the verified credential state is still current."""
        updated = (
            self.db.query(UserAccount)
            .filter(
                UserAccount.id == user_id,
                UserAccount.password_hash == observed_password_hash,
                UserAccount.auth_version == observed_auth_version,
                UserAccount.is_active.is_(True),
            )
            .update({"last_login": last_login}, synchronize_session=False)
        )
        if updated != 1:
            self.db.rollback()
            return None
        record = self.get_user_by_id(user_id)
        if record is None:
            self.db.rollback()
            return None
        self.db.refresh(record)
        if (
            record.password_hash != observed_password_hash
            or record.auth_version != observed_auth_version
            or not record.is_active
        ):
            self.db.rollback()
            return None
        # Snapshot before commit while the UPDATE still holds the row lock;
        # never re-read and adopt a later password epoch for this login.
        receipt = SimpleNamespace(
            **{
                name: getattr(record, name)
                for name in (
                    "id",
                    "username",
                    "email",
                    "full_name",
                    "company_id",
                    "role",
                    "is_active",
                    "created_at",
                    "updated_at",
                    "last_login",
                    "auth_version",
                    "password_hash",
                )
            }
        )
        self.db.commit()
        return receipt
