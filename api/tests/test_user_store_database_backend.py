"""Database user store tests (DB mocked; offline-safe)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.auth.jwt_handler import jwt_handler
from src.auth.models import UserCreate, UserRole, UserUpdate
from src.config.settings import settings
from src.services.user_store import DatabaseUserStore, InMemoryUserStore, get_user_store


def _record(**kwargs):
    base = dict(
        id="user_admin",
        username="admin",
        email="admin@example.com",
        full_name=None,
        company_id="company_001",
        role=UserRole.ADMIN.value,
        is_active=True,
        password_hash=jwt_handler.hash_password("admin123"),
        auth_version=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        last_login=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_database_user_store_authenticate_success_updates_last_login():
    store = DatabaseUserStore(MagicMock())
    store._repo = MagicMock()

    record = _record(password_hash=jwt_handler.hash_password("secret"))
    store._repo.get_user_by_username.return_value = record

    def record_login(**kwargs):
        assert kwargs["user_id"] == record.id
        assert kwargs["observed_password_hash"] == record.password_hash
        assert kwargs["observed_auth_version"] == 0
        record.last_login = kwargs["last_login"]
        return record

    store._repo.record_successful_login_if_current.side_effect = record_login

    user = store.authenticate(username="admin", password="secret")
    assert user is not None
    assert user.username == "admin"
    assert user.role == UserRole.ADMIN
    assert record.last_login is not None
    store._repo.record_successful_login_if_current.assert_called_once()


def test_database_user_store_authenticate_inactive_returns_none():
    store = DatabaseUserStore(MagicMock())
    store._repo = MagicMock()

    store._repo.get_user_by_username.return_value = _record(is_active=False)

    assert store.authenticate(username="admin", password="admin123") is None


def test_database_user_store_create_user_hashes_password_and_maps_role_value():
    store = DatabaseUserStore(MagicMock())
    store._repo = MagicMock()
    store._repo.get_user_by_username.return_value = None

    created_record = _record(
        id="user_new_user",
        username="new_user",
        email="new_user@example.com",
        role=UserRole.VIEWER.value,
        password_hash="hash",
    )
    store._repo.create_user.return_value = created_record

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
    store._repo.create_user.assert_called_once()
    assert store._repo.create_user.call_args.kwargs["role"] == UserRole.VIEWER.value
    assert (
        store._repo.create_user.call_args.kwargs["password_hash"]
        != "secure_password123"
    )


def test_database_user_store_update_user_respects_admin_fields_flag():
    store = DatabaseUserStore(MagicMock())
    store._repo = MagicMock()

    record = _record(company_id="company_001", role=UserRole.ADMIN.value)
    store._repo.get_user_by_username.return_value = record
    store._repo.update_user.return_value = record

    store.update_user(
        username="admin",
        update=UserUpdate(
            email="new@example.com", company_id="new_company", role=UserRole.VIEWER
        ),
        allow_admin_fields=False,
    )

    kwargs = store._repo.update_user.call_args.kwargs
    assert kwargs["email"] == "new@example.com"
    assert "company_id" not in kwargs
    assert "role" not in kwargs

    store._repo.update_user.reset_mock()

    store.update_user(
        username="admin",
        update=UserUpdate(
            company_id="new_company", role=UserRole.VIEWER, is_active=False
        ),
        allow_admin_fields=True,
    )
    kwargs = store._repo.update_user.call_args.kwargs
    assert kwargs["company_id"] == "new_company"
    assert kwargs["role"] == UserRole.VIEWER.value
    assert kwargs["is_active"] is False


def test_database_user_store_change_password_validates_current_password():
    store = DatabaseUserStore(MagicMock())
    store._repo = MagicMock()

    record = _record(password_hash=jwt_handler.hash_password("old"))
    store._repo.get_user_by_username.return_value = record

    assert (
        store.change_password(
            username="admin", current_password="wrong", new_password="new"
        )
        is False
    )

    store._repo.update_password_hash_if_current.return_value = True
    assert (
        store.change_password(
            username="admin", current_password="old", new_password="new"
        )
        is True
    )
    store._repo.update_password_hash_if_current.assert_called_once()
    kwargs = store._repo.update_password_hash_if_current.call_args.kwargs
    assert kwargs["observed_password_hash"] == record.password_hash
    assert kwargs["observed_auth_version"] == 0


def test_get_user_store_caches_inmemory_store_when_db_not_required(monkeypatch):
    original = settings.require_database
    monkeypatch.setattr(settings, "require_database", False)
    try:

        class DummyState:
            pass

        class DummyApp:
            state = DummyState()

        class DummyRequest:
            app = DummyApp()

        request = DummyRequest()
        store1 = get_user_store(request, db=None)
        store2 = get_user_store(request, db=None)
        assert isinstance(store1, InMemoryUserStore)
        assert store1 is store2
    finally:
        monkeypatch.setattr(settings, "require_database", original)


def test_get_user_store_requires_db_session_when_required(monkeypatch):
    original = settings.require_database
    monkeypatch.setattr(settings, "require_database", True)
    try:

        class DummyState:
            pass

        class DummyApp:
            state = DummyState()

        class DummyRequest:
            app = DummyApp()

        request = DummyRequest()
        with pytest.raises(HTTPException):
            get_user_store(request, db=None)

        store = get_user_store(request, db=MagicMock())
        assert isinstance(store, DatabaseUserStore)
    finally:
        monkeypatch.setattr(settings, "require_database", original)
