"""User store tests (offline-safe)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.auth.jwt_handler import jwt_handler
from src.auth.models import UserCreate, UserRole, UserUpdate
from src.config.settings import settings
from src.database.models import UserAccount
from src.database.repositories.user_repository import UserRepository
from src.services.user_store import DatabaseUserStore, InMemoryUserStore, get_user_store


def _seeded_store() -> InMemoryUserStore:
    store = InMemoryUserStore()
    for username, password, role in (
        ("admin", "admin123", UserRole.ADMIN),
        ("viewer", "viewer123", UserRole.VIEWER),
    ):
        store.create_user(
            UserCreate(
                username=username,
                email=f"{username}@example.com",
                full_name=f"Test {role.value}",
                company_id="sds_company",
                role=role,
                password=password,
                is_active=True,
            ),
            created_by="pytest",
        )
        store._users[username].id = "admin-001" if username == "admin" else "viewer-001"
    return store


def test_inmemory_user_store_has_no_embedded_default_credentials():
    store = InMemoryUserStore()

    assert store.authenticate(username="admin", password="admin123") is None
    assert store.get_user(username="admin") is None


def test_database_session_invalidations_increment_epoch_atomically(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'users.db'}")
    UserAccount.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)
    seed = SessionLocal()
    seed.add(
        UserAccount(
            id="user-1",
            username="user",
            email="user@example.invalid",
            role=UserRole.VIEWER.value,
            password_hash="unused",
            is_active=True,
            auth_version=0,
        )
    )
    seed.commit()
    seed.close()

    first_session = SessionLocal()
    second_session = SessionLocal()
    first = UserRepository(first_session)
    second = UserRepository(second_session)
    first_record = first.get_user_by_id("user-1")
    second_record = second.get_user_by_id("user-1")
    assert first_record is not None and first_record.auth_version == 0
    assert second_record is not None and second_record.auth_version == 0
    # Hold both reads at the same epoch to reproduce two overlapping requests.
    first.get_user_by_id = lambda _user_id: first_record
    second.get_user_by_id = lambda _user_id: second_record

    assert first.invalidate_sessions("user-1") is True
    assert second.invalidate_sessions("user-1") is True

    verify = SessionLocal()
    assert verify.get(UserAccount, "user-1").auth_version == 2
    first_session.close()
    second_session.close()
    verify.close()


def test_successful_login_cas_rejects_a_stale_password_epoch(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'login-race.db'}")
    UserAccount.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)
    seed = SessionLocal()
    seed.add(
        UserAccount(
            id="user-1",
            username="user",
            email="user@example.invalid",
            role=UserRole.VIEWER.value,
            password_hash="old-hash",
            is_active=True,
            auth_version=0,
        )
    )
    seed.commit()
    seed.close()

    login_session = SessionLocal()
    password_session = SessionLocal()
    login_repo = UserRepository(login_session)
    password_repo = UserRepository(password_session)
    observed = login_repo.get_user_by_id("user-1")
    assert observed is not None

    assert (
        password_repo.update_password_hash_if_current(
            user_id="user-1",
            observed_password_hash="old-hash",
            observed_auth_version=0,
            password_hash="new-hash",
        )
        is True
    )
    authenticated = login_repo.record_successful_login_if_current(
        user_id=observed.id,
        observed_password_hash="old-hash",
        observed_auth_version=0,
        last_login=datetime.now(timezone.utc),
    )

    assert authenticated is None
    verify = SessionLocal()
    current = verify.get(UserAccount, "user-1")
    assert current is not None
    assert current.password_hash == "new-hash"
    assert current.auth_version == 1
    assert current.last_login is None
    login_session.close()
    password_session.close()
    verify.close()


def test_login_receipt_cannot_adopt_a_later_password_epoch(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'login-receipt.db'}")
    UserAccount.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)
    seed = SessionLocal()
    seed.add(
        UserAccount(
            id="user-1",
            username="user",
            email="user@example.invalid",
            role=UserRole.VIEWER.value,
            password_hash="old-hash",
            is_active=True,
            auth_version=0,
        )
    )
    seed.commit()
    seed.close()

    login = SessionLocal()
    changer = SessionLocal()
    repo = UserRepository(login)
    original_commit = login.commit

    def rotate_after_login_commit():
        original_commit()
        assert UserRepository(changer).update_password_hash_if_current(
            user_id="user-1",
            observed_password_hash="old-hash",
            observed_auth_version=0,
            password_hash="new-hash",
        )

    monkeypatch.setattr(login, "commit", rotate_after_login_commit)
    authenticated = repo.record_successful_login_if_current(
        user_id="user-1",
        observed_password_hash="old-hash",
        observed_auth_version=0,
        last_login=datetime.now(timezone.utc),
    )
    assert authenticated is not None
    assert authenticated.auth_version == 0
    assert authenticated.password_hash == "old-hash"
    login.close()
    changer.close()


def test_password_change_cas_rejects_a_stale_credential_epoch(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'password-race.db'}")
    UserAccount.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)
    seed = SessionLocal()
    seed.add(
        UserAccount(
            id="user-1",
            username="user",
            email="user@example.invalid",
            role=UserRole.VIEWER.value,
            password_hash="old-hash",
            is_active=True,
            auth_version=0,
        )
    )
    seed.commit()
    seed.close()

    password_session = SessionLocal()
    invalidation_session = SessionLocal()
    password_repo = UserRepository(password_session)
    invalidation_repo = UserRepository(invalidation_session)
    observed = password_repo.get_user_by_id("user-1")
    assert observed is not None

    assert invalidation_repo.invalidate_sessions("user-1") is True
    assert (
        password_repo.update_password_hash_if_current(
            user_id="user-1",
            observed_password_hash="old-hash",
            observed_auth_version=0,
            password_hash="new-hash",
        )
        is False
    )
    verify = SessionLocal()
    current = verify.get(UserAccount, "user-1")
    assert current is not None
    assert current.password_hash == "old-hash"
    assert current.auth_version == 1
    password_session.close()
    invalidation_session.close()
    verify.close()


def test_inmemory_user_store_authenticates_explicit_test_user():
    store = _seeded_store()

    admin = store.authenticate(username="admin", password="admin123")
    assert admin is not None
    assert admin.username == "admin"
    assert admin.role == UserRole.ADMIN
    assert admin.is_active is True

    assert store.authenticate(username="admin", password="wrong") is None


def test_inmemory_user_store_change_password_roundtrip():
    store = _seeded_store()

    assert (
        store.change_password(
            username="admin",
            current_password="admin123",
            new_password="new_password_123",
        )
        is True
    )
    assert store.authenticate(username="admin", password="admin123") is None
    assert store.authenticate(username="admin", password="new_password_123") is not None


def test_inmemory_user_store_create_user_and_login():
    store = InMemoryUserStore()

    created = store.create_user(
        UserCreate(
            username="new_user",
            email="new_user@example.com",
            full_name="New User",
            company_id="company_001",
            role=UserRole.VIEWER,
            password="secure_password123",
            is_active=True,
        ),
        created_by="admin",
    )
    assert created.username == "new_user"

    assert (
        store.authenticate(username="new_user", password="secure_password123")
        is not None
    )


def test_inmemory_user_store_update_user_respects_admin_fields_flag():
    store = _seeded_store()

    updated = store.update_user(
        username="admin",
        update=UserUpdate(
            email="updated_admin@example.com",
            full_name="Updated Admin",
            company_id="new_company",
            role=UserRole.VIEWER,
            is_active=False,
        ),
        allow_admin_fields=False,
    )
    assert updated is not None
    assert updated.email == "updated_admin@example.com"
    assert updated.full_name == "Updated Admin"
    assert updated.company_id == "sds_company"
    assert updated.role == UserRole.ADMIN
    assert updated.is_active is True

    updated_admin = store.update_user(
        username="admin",
        update=UserUpdate(
            email="updated_admin2@example.com",
            full_name="Updated Admin 2",
            company_id="new_company",
            role=UserRole.VIEWER,
            is_active=False,
        ),
        allow_admin_fields=True,
    )
    assert updated_admin is not None
    assert updated_admin.company_id == "new_company"
    assert updated_admin.role == UserRole.VIEWER
    assert updated_admin.is_active is False


def test_inmemory_user_store_create_user_duplicate_username_raises():
    store = InMemoryUserStore()

    store.create_user(
        UserCreate(
            username="dupe",
            email="dupe@example.com",
            full_name="Dupe",
            company_id="company_001",
            role=UserRole.VIEWER,
            password="secure_password123",
            is_active=True,
        ),
        created_by="admin",
    )

    try:
        store.create_user(
            UserCreate(
                username="dupe",
                email="dupe2@example.com",
                full_name="Dupe 2",
                company_id="company_001",
                role=UserRole.VIEWER,
                password="secure_password123",
                is_active=True,
            ),
            created_by="admin",
        )
        raise AssertionError("Expected ValueError for duplicate username")
    except ValueError:
        pass


def test_inmemory_user_store_change_password_wrong_current_returns_false():
    store = _seeded_store()

    assert (
        store.change_password(
            username="admin", current_password="wrong", new_password="new_password_123"
        )
        is False
    )


def test_inmemory_user_store_missing_lookup_and_inactive_authentication():
    store = _seeded_store()

    assert store.get_user_by_id(user_id="admin-001").username == "admin"
    assert store.get_user_by_id(user_id="missing") is None
    assert (
        store.update_user(
            username="missing",
            update=UserUpdate(full_name="No One"),
            allow_admin_fields=True,
        )
        is None
    )
    assert (
        store.change_password(
            username="missing",
            current_password="old",
            new_password="new_password_123",
        )
        is False
    )

    store.update_user(
        username="viewer",
        update=UserUpdate(is_active=False),
        allow_admin_fields=True,
    )
    assert store.authenticate(username="viewer", password="viewer123") is None


class _FakeUserRepo:
    def __init__(self, record=None):
        self.record = record
        self.created = None
        self.updated = None
        self.password_cas_result = True
        self.password_cas_calls = []
        self.login_cas_result = record
        self.login_cas_calls = []

    def get_user_by_username(self, username):
        return self.record if self.record and self.record.username == username else None

    def get_user_by_id(self, user_id):
        return self.record if self.record and self.record.id == user_id else None

    def record_successful_login_if_current(self, **kwargs):
        self.login_cas_calls.append(kwargs)
        return self.login_cas_result

    def create_user(self, **kwargs):
        self.created = kwargs
        now = datetime.now(timezone.utc)
        return SimpleNamespace(
            id=kwargs["user_id"],
            username=kwargs["username"],
            email=kwargs["email"],
            full_name=kwargs["full_name"],
            company_id=kwargs["company_id"],
            role=kwargs["role"],
            password_hash=kwargs["password_hash"],
            is_active=kwargs["is_active"],
            created_at=now,
            updated_at=now,
            last_login=None,
        )

    def update_user(self, user_id, **updates):
        self.updated = (user_id, updates)
        for key, value in updates.items():
            setattr(self.record, key, value)
        return self.record

    def update_password_hash_if_current(self, **kwargs):
        self.password_cas_calls.append(kwargs)
        if self.password_cas_result:
            assert self.record is not None
            self.record.password_hash = kwargs["password_hash"]
        return self.password_cas_result


def _db_store(record=None):
    store = DatabaseUserStore.__new__(DatabaseUserStore)
    store._repo = _FakeUserRepo(record)
    return store


def _record(**overrides):
    values = {
        "id": "user-1",
        "username": "db_user",
        "email": "db_user@example.com",
        "full_name": "DB User",
        "company_id": "company-1",
        "role": UserRole.VIEWER.value,
        "password_hash": jwt_handler.hash_password("secret123"),
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "last_login": None,
        "auth_version": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_database_authentication_refuses_stale_verified_credentials():
    record = _record(auth_version=7)
    store = _db_store(record)
    fake_repo = cast(_FakeUserRepo, store._repo)
    fake_repo.login_cas_result = None

    assert store.authenticate(username="db_user", password="secret123") is None
    assert fake_repo.login_cas_calls == [
        {
            "user_id": "user-1",
            "observed_password_hash": record.password_hash,
            "observed_auth_version": 7,
            "last_login": fake_repo.login_cas_calls[0]["last_login"],
        }
    ]


def test_database_password_change_refuses_stale_verified_credentials():
    record = _record(auth_version=5)
    store = _db_store(record)
    fake_repo = cast(_FakeUserRepo, store._repo)
    fake_repo.password_cas_result = False

    assert (
        store.change_password(
            username="db_user",
            current_password="secret123",
            new_password="new_secret123",
        )
        is False
    )
    call = fake_repo.password_cas_calls[0]
    assert call["user_id"] == "user-1"
    assert call["observed_password_hash"] == record.password_hash
    assert call["observed_auth_version"] == 5
    assert call["password_hash"] != record.password_hash


def test_database_user_store_auth_create_update_and_password_paths():
    record = _record()
    store = _db_store(record)

    assert store.authenticate(username="db_user", password="wrong") is None
    authenticated = store.authenticate(username="db_user", password="secret123")
    assert authenticated is not None
    assert authenticated.username == "db_user"
    assert cast(_FakeUserRepo, store._repo).login_cas_calls[0]["user_id"] == "user-1"
    assert store.get_user(username="db_user").id == "user-1"
    assert store.get_user_by_id(user_id="user-1").username == "db_user"
    assert store.get_user(username="missing") is None
    assert store.get_user_by_id(user_id="missing") is None

    updated = store.update_user(
        username="db_user",
        update=UserUpdate(
            email="changed@example.com",
            full_name="Changed",
            company_id="company-2",
            role=UserRole.ADMIN,
            is_active=False,
        ),
        allow_admin_fields=True,
    )
    assert updated.email == "changed@example.com"
    assert updated.role == UserRole.ADMIN
    assert store._repo.updated[1]["company_id"] == "company-2"

    assert (
        store.change_password(
            username="db_user",
            current_password="secret123",
            new_password="new_secret123",
        )
        is True
    )
    assert cast(_FakeUserRepo, store._repo).password_cas_calls[0]["user_id"] == "user-1"

    inactive_store = _db_store(_record(is_active=False))
    assert inactive_store.authenticate(username="db_user", password="secret123") is None
    assert (
        inactive_store.change_password(
            username="missing", current_password="x", new_password="new_secret123"
        )
        is False
    )
    assert (
        inactive_store.update_user(
            username="missing",
            update=UserUpdate(full_name="Missing"),
            allow_admin_fields=True,
        )
        is None
    )


def test_database_user_store_duplicate_create_and_wrong_password():
    store = _db_store(_record())

    with pytest.raises(ValueError, match="username already exists"):
        store.create_user(
            UserCreate(
                username="db_user",
                email="dupe@example.com",
                full_name="Dupe",
                company_id="company-1",
                role=UserRole.VIEWER,
                password="secret123",
                is_active=True,
            )
        )

    fresh_store = _db_store(None)
    created = fresh_store.create_user(
        UserCreate(
            username="fresh",
            email="fresh@example.com",
            full_name="Fresh",
            company_id="company-1",
            role=UserRole.ANALYST,
            password="secret123",
            is_active=True,
        )
    )
    assert created.username == "fresh"
    assert fresh_store._repo.created["role"] == UserRole.ANALYST.value

    assert (
        store.change_password(
            username="db_user",
            current_password="wrong",
            new_password="new_secret123",
        )
        is False
    )


def test_get_user_store_respects_database_mode_and_caches_inmemory(monkeypatch):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    monkeypatch.setattr(settings, "require_database", False)
    first = get_user_store(request=request, db=None)
    second = get_user_store(request=request, db=None)
    assert first is second

    monkeypatch.setattr(settings, "require_database", True)
    with pytest.raises(HTTPException) as exc:
        get_user_store(request=request, db=None)
    assert exc.value.status_code == 503

    db_store = get_user_store(request=request, db=object())
    assert isinstance(db_store, DatabaseUserStore)
