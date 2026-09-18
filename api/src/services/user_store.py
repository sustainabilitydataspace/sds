"""User account persistence backends (DB or in-memory)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Request
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


class InMemoryUserStore:
    """Offline-safe user store (ephemeral, per-process)."""

    def __init__(self, *, seed_defaults: bool = True):
        self._users: Dict[str, _StoredUser] = {}
        if seed_defaults:
            self._seed_defaults()

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
        )
        self._users[user.username] = stored
        return self._to_user(stored)

    def update_user(
        self, *, username: str, update: UserUpdate, allow_admin_fields: bool
    ) -> Optional[User]:
        stored = self._users.get(username)
        if not stored:
            return None

        if update.email:
            stored.email = str(update.email)
        if update.full_name is not None:
            stored.full_name = update.full_name

        if allow_admin_fields:
            if update.company_id:
                stored.company_id = update.company_id
            if update.role:
                stored.role = update.role
            if update.is_active is not None:
                stored.is_active = update.is_active

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
        stored.updated_at = datetime.now(timezone.utc)
        return True

    def _seed_defaults(self) -> None:
        # Mirror the previous MOCK_USERS so the offline test suite remains stable.
        now = datetime.now(timezone.utc)

        def add(
            *,
            user_id: str,
            username: str,
            password: str,
            email: str,
            full_name: str,
            role: UserRole,
            company_id: str,
            is_active: bool = True,
        ) -> None:
            self._users[username] = _StoredUser(
                id=user_id,
                username=username,
                email=email,
                full_name=full_name,
                company_id=company_id,
                role=role,
                is_active=is_active,
                password_hash=jwt_handler.hash_password(password),
                created_at=now,
                updated_at=now,
                last_login=None,
            )

        add(
            user_id="admin-001",
            username="admin",
            password="admin123",
            email="admin@sustainabilitydata.space",
            full_name="System Administrator",
            role=UserRole.ADMIN,
            company_id="sds_company",
        )
        add(
            user_id="dm-001",
            username="data_manager",
            password="manager123",
            email="manager@sustainabilitydata.space",
            full_name="Data Manager",
            role=UserRole.DATA_MANAGER,
            company_id="sds_company",
        )
        add(
            user_id="analyst-001",
            username="analyst",
            password="analyst123",
            email="analyst@sustainabilitydata.space",
            full_name="ESG Analyst",
            role=UserRole.ANALYST,
            company_id="sds_company",
        )
        add(
            user_id="viewer-001",
            username="viewer",
            password="viewer123",
            email="viewer@sustainabilitydata.space",
            full_name="Data Viewer",
            role=UserRole.VIEWER,
            company_id="sds_company",
        )

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
        self._repo.set_last_login(record.id, last_login=now)
        record.last_login = now
        return self._to_user(record)

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
        self, *, username: str, update: UserUpdate, allow_admin_fields: bool
    ) -> Optional[User]:
        record = self._repo.get_user_by_username(username)
        if not record:
            return None

        updates: dict[str, object] = {}
        if update.email:
            updates["email"] = str(update.email)
        if update.full_name is not None:
            updates["full_name"] = update.full_name
        if allow_admin_fields:
            if update.company_id:
                updates["company_id"] = update.company_id
            if update.role:
                updates["role"] = update.role.value
            if update.is_active is not None:
                updates["is_active"] = update.is_active

        record = self._repo.update_user(record.id, **updates) or record
        return self._to_user(record)

    def change_password(
        self, *, username: str, current_password: str, new_password: str
    ) -> bool:
        record = self._repo.get_user_by_username(username)
        if not record:
            return False
        if not jwt_handler.verify_password(current_password, record.password_hash):
            return False
        return self._repo.update_password_hash(
            record.id, jwt_handler.hash_password(new_password)
        )

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
