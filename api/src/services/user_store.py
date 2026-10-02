"""User account persistence backends (DB or in-memory)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth.jwt_handler import jwt_handler
from src.auth.models import ROLE_PERMISSIONS, User, UserCreate, UserRole, UserUpdate
from src.config.settings import settings
from src.database.repositories.user_repository import UserRepository
from src.database.session import get_db_optional


@dataclass
class _StoredUser:
    id: str
    username: str
    email: str
    full_name: Optional[str]
    company_id: Optional[str]
    role: UserRole
    is_active: bool
    password_hash: str
    created_at: datetime
    updated_at: datetime
    last_login: Optional[datetime]
    auth_version: int = 0


class InMemoryUserStore:
    """Offline-safe user store (ephemeral, per-process)."""

    def __init__(self):
        self._users: Dict[str, _StoredUser] = {}

    def authenticate(self, *, username: str, password: str) -> Optional[User]:
        stored = self._users.get(username)
        if not stored or not stored.is_active:
            return None
        if not jwt_handler.verify_password(password, stored.password_hash):
            return None

        now = datetime.now(timezone.utc)
        stored.last_login = now
        stored.updated_at = now
        return self._to_user(stored)

    def get_user(self, *, username: str) -> Optional[User]:
        stored = self._users.get(username)
        return self._to_user(stored) if stored else None

    def get_user_by_id(self, *, user_id: str) -> Optional[User]:
        for stored in self._users.values():
            if stored.id == user_id:
                return self._to_user(stored)
        return None

    def create_user(
        self, user: UserCreate, *, created_by: Optional[str] = None
    ) -> User:
        if user.username in self._users:
            raise ValueError("username already exists")

        now = datetime.now(timezone.utc)
        stored = _StoredUser(
            id=f"user_{user.username}",
            username=user.username,
            email=str(user.email),
            full_name=user.full_name,
            company_id=user.company_id,
            role=user.role,
            is_active=user.is_active,
            password_hash=jwt_handler.hash_password(user.password),
            created_at=now,
            updated_at=now,
            last_login=None,
            auth_version=0,
        )
        self._users[user.username] = stored
        return self._to_user(stored)

    def update_user(
        self,
        *,
        username: str,
        update: UserUpdate,
        allow_admin_fields: bool,
        revoke_sessions: bool = False,
    ) -> Optional[User]:
        stored = self._users.get(username)
        if not stored:
            return None

        if update.email and any(
            other.email == str(update.email) and other.username != username
            for other in self._users.values()
        ):
            raise ValueError("email already in use")

        changed = False
        if update.email:
            stored.email = str(update.email)
            changed = True
        if update.full_name is not None:
            stored.full_name = update.full_name
            changed = True

        security_changed = False
        if allow_admin_fields:
            if update.company_id:
                stored.company_id = update.company_id
                security_changed = True
            if update.role:
                stored.role = update.role
                security_changed = True
            if update.is_active is not None:
                stored.is_active = update.is_active
                security_changed = True
        if security_changed or (revoke_sessions and changed):
            stored.auth_version += 1

        stored.updated_at = datetime.now(timezone.utc)
        return self._to_user(stored)

    def admin_set_password(self, *, username: str, new_password: str) -> Optional[User]:
        stored = self._users.get(username)
        if not stored:
            return None
        stored.password_hash = jwt_handler.hash_password(new_password)
        stored.auth_version += 1
        stored.updated_at = datetime.now(timezone.utc)
        return self._to_user(stored)

    def change_password(
        self, *, username: str, current_password: str, new_password: str
    ) -> bool:
        stored = self._users.get(username)
        if not stored:
            return False
        if not jwt_handler.verify_password(current_password, stored.password_hash):
            return False
        stored.password_hash = jwt_handler.hash_password(new_password)
        stored.auth_version += 1
        stored.updated_at = datetime.now(timezone.utc)
        return True

    def invalidate_sessions(self, *, username: str) -> bool:
        stored = self._users.get(username)
        if not stored:
            return False
        stored.auth_version += 1
        stored.updated_at = datetime.now(timezone.utc)
        return True

    @staticmethod
    def _to_user(stored: _StoredUser) -> User:
        return User(
            id=stored.id,
            username=stored.username,
            email=stored.email,
            full_name=stored.full_name,
            company_id=stored.company_id,
            role=stored.role,
            is_active=stored.is_active,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
            last_login=stored.last_login,
            permissions=ROLE_PERMISSIONS.get(stored.role, []),
            auth_version=stored.auth_version,
        )


class DatabaseUserStore:
    """PostgreSQL-backed user store."""

    def __init__(self, db: Session):
        self._repo = UserRepository(db)

    def authenticate(self, *, username: str, password: str) -> Optional[User]:
        record = self._repo.get_user_by_username(username)
        if not record or not bool(record.is_active):
            return None
        if not jwt_handler.verify_password(password, record.password_hash):
            return None

        now = datetime.now(timezone.utc)
        current_record = self._repo.record_successful_login_if_current(
            user_id=record.id,
            observed_password_hash=record.password_hash,
            observed_auth_version=int(getattr(record, "auth_version", 0)),
            last_login=now,
        )
        if current_record is None:
            return None
        return self._to_user(current_record)

    def get_user(self, *, username: str) -> Optional[User]:
        record = self._repo.get_user_by_username(username)
        return self._to_user(record) if record else None

    def get_user_by_id(self, *, user_id: str) -> Optional[User]:
        record = self._repo.get_user_by_id(user_id)
        return self._to_user(record) if record else None

    def create_user(
        self, user: UserCreate, *, created_by: Optional[str] = None
    ) -> User:
        existing = self._repo.get_user_by_username(user.username)
        if existing:
            raise ValueError("username already exists")

        user_id = f"user_{user.username}"
        password_hash = jwt_handler.hash_password(user.password)
        record = self._repo.create_user(
            user_id=user_id,
            username=user.username,
            email=str(user.email),
            full_name=user.full_name,
            company_id=user.company_id,
            role=user.role.value,
            password_hash=password_hash,
            is_active=user.is_active,
        )
        return self._to_user(record)

    def update_user(
        self,
        *,
        username: str,
        update: UserUpdate,
        allow_admin_fields: bool,
        revoke_sessions: bool = False,
    ) -> Optional[User]:
        record = self._repo.get_user_by_username(username)
        if not record:
            return None

        updates: dict[str, object] = {}
        if update.email:
            email = str(update.email)
            owner = self._repo.get_user_by_email(email)
            if owner is not None and owner.id != record.id:
                raise ValueError("email already in use")
            updates["email"] = email
        if update.full_name is not None:
            updates["full_name"] = update.full_name
        security_changed = False
        if allow_admin_fields:
            if update.company_id:
                updates["company_id"] = update.company_id
                security_changed = True
            if update.role:
                updates["role"] = update.role.value
                security_changed = True
            if update.is_active is not None:
                updates["is_active"] = update.is_active
                security_changed = True

        try:
            updated = self._repo.update_user(
                record.id,
                increment_auth_version=security_changed
                or (revoke_sessions and bool(updates)),
                **updates,
            )
        except IntegrityError:
            # The unique email constraint is the race-safe backstop.
            self._repo.db.rollback()
            raise ValueError("email already in use") from None
        # The repository rolled back; never report the pre-update record as saved.
        if updated is None:
            return None
        return self._to_user(updated)

    def admin_set_password(self, *, username: str, new_password: str) -> Optional[User]:
        record = self._repo.get_user_by_username(username)
        if not record:
            return None
        updated = self._repo.set_password_hash_if_version(
            user_id=record.id,
            observed_auth_version=int(getattr(record, "auth_version", 0)),
            password_hash=jwt_handler.hash_password(new_password),
        )
        return self._to_user(updated) if updated is not None else None

    def change_password(
        self, *, username: str, current_password: str, new_password: str
    ) -> bool:
        record = self._repo.get_user_by_username(username)
        if not record:
            return False
        if not jwt_handler.verify_password(current_password, record.password_hash):
            return False
        return self._repo.update_password_hash_if_current(
            user_id=record.id,
            observed_password_hash=record.password_hash,
            observed_auth_version=int(getattr(record, "auth_version", 0)),
            password_hash=jwt_handler.hash_password(new_password),
        )

    def invalidate_sessions(self, *, username: str) -> bool:
        record = self._repo.get_user_by_username(username)
        if not record:
            return False
        return self._repo.invalidate_sessions(record.id)

    @staticmethod
    def _to_user(record) -> User:
        role = UserRole(getattr(record, "role", UserRole.VIEWER.value))
        return User(
            id=record.id,
            username=record.username,
            email=record.email,
            full_name=getattr(record, "full_name", None),
            company_id=getattr(record, "company_id", None),
            role=role,
            is_active=bool(getattr(record, "is_active", True)),
            created_at=record.created_at,
            updated_at=record.updated_at,
            last_login=getattr(record, "last_login", None),
            permissions=ROLE_PERMISSIONS.get(role, []),
            auth_version=int(getattr(record, "auth_version", 0)),
        )


def get_user_store(
    request: Request,
    db: Optional[Session] = Depends(get_db_optional),
):
    """Dependency that returns the configured user store (DB when required)."""
    if settings.require_database:
        if db is None:
            raise HTTPException(status_code=503, detail="Database session unavailable")
        return DatabaseUserStore(db)

    store = getattr(request.app.state, "user_store", None)
    if store is None:
        store = InMemoryUserStore()
        request.app.state.user_store = store
    return store
