"""API key store unit tests (offline-safe)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.auth.jwt_handler import jwt_handler
from src.auth.models import APIKeyCreate, Permission
from src.config.settings import settings
from src.services.api_key_store import (
    DatabaseAPIKeyStore,
    InMemoryAPIKeyStore,
    ResolvedAPIKey,
    _split_api_key,
    get_api_key_store,
)


def test_split_api_key_parses_expected_format():
    assert _split_api_key("sds_deadbeef.secret") == ("deadbeef", "secret")
    assert _split_api_key("not_sds") is None
    assert _split_api_key("sds_no_dot") is None
    assert _split_api_key("sds_.secret") is None
    assert _split_api_key("sds_deadbeef.") is None


def test_inmemory_api_key_store_create_list_authenticate_revoke_roundtrip():
    store = InMemoryAPIKeyStore()

    created = store.create_api_key(
        user_id="admin",
        request=APIKeyCreate(
            name="ci",
            description="test",
            expires_at=None,
            permissions=[Permission.CONVERT_UNITS],
        ),
    )
    assert created.key.startswith(f"sds_{created.id}.")

    listed = store.list_api_keys(user_id="admin")
    assert len(listed) == 1
    assert listed[0].permissions == [Permission.CONVERT_UNITS]

    resolved = store.authenticate(created.key)
    assert resolved == ResolvedAPIKey(
        key_id=created.id, user_id="admin", permissions=[Permission.CONVERT_UNITS]
    )

    assert store.revoke_api_key(user_id="admin", key_id=created.id) is True
    assert store.authenticate(created.key) is None
    assert store.revoke_api_key(user_id="other", key_id=created.id) is False
    assert store.revoke_api_key(user_id="admin", key_id="missing") is False


def test_inmemory_api_key_store_applies_default_expiry_when_omitted(monkeypatch):
    monkeypatch.setattr(settings, "api_key_expire_days", 30)
    store = InMemoryAPIKeyStore()

    before = datetime.now(timezone.utc) + timedelta(days=29, hours=23)
    created = store.create_api_key(
        user_id="admin",
        request=APIKeyCreate(
            name="ci",
            description="test",
            expires_at=None,
            permissions=[Permission.CONVERT_UNITS],
        ),
    )
    after = datetime.now(timezone.utc) + timedelta(days=30, minutes=1)

    assert created.expires_at is not None
    assert before < created.expires_at < after


def test_inmemory_api_key_store_preserves_explicit_expiry(monkeypatch):
    monkeypatch.setattr(settings, "api_key_expire_days", 30)
    store = InMemoryAPIKeyStore()
    explicit = datetime.now(timezone.utc) + timedelta(days=5)

    created = store.create_api_key(
        user_id="admin",
        request=APIKeyCreate(
            name="ci",
            description="test",
            expires_at=explicit,
            permissions=[Permission.CONVERT_UNITS],
        ),
    )

    assert created.expires_at == explicit


def test_inmemory_api_key_store_rejects_bad_inactive_and_wrong_secret():
    store = InMemoryAPIKeyStore()
    created = store.create_api_key(
        user_id="admin",
        request=APIKeyCreate(
            name="ci",
            description=None,
            expires_at=None,
            permissions=[Permission.CONVERT_UNITS],
        ),
    )

    assert store.authenticate("not-a-key") is None
    assert store.authenticate(f"sds_{created.id}.wrong") is None
    store._keys[created.id].is_active = False
    assert store.authenticate(created.key) is None


def test_inmemory_api_key_store_rejects_expired_key():
    store = InMemoryAPIKeyStore()
    created = store.create_api_key(
        user_id="admin",
        request=APIKeyCreate(
            name="ci",
            description="test",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            permissions=[Permission.CONVERT_UNITS],
        ),
    )
    assert store.authenticate(created.key) is None


def test_database_api_key_store_authenticate_and_revoke():
    store = DatabaseAPIKeyStore(MagicMock())
    store._repo = MagicMock()

    key_id = "deadbeef"
    secret = "secret"
    secret_hash = jwt_handler.hash_api_key(secret)
    record = SimpleNamespace(
        id=key_id,
        user_id="admin",
        name="ci",
        description="test",
        secret_hash=secret_hash,
        permissions=[Permission.CONVERT_UNITS.value],
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        last_used=None,
        is_active=True,
    )

    store._repo.get_api_key_by_id.return_value = record
    store._repo.set_last_used.return_value = True

    resolved = store.authenticate(f"sds_{key_id}.{secret}")
    assert resolved is not None
    assert resolved.user_id == "admin"
    assert Permission.CONVERT_UNITS in resolved.permissions
    store._repo.set_last_used.assert_called_once()

    store._repo.revoke_api_key.return_value = True
    assert store.revoke_api_key(user_id="admin", key_id=key_id) is True


def test_database_api_key_store_create_and_rejects_inactive_expired_bad_secret():
    store = DatabaseAPIKeyStore(MagicMock())
    store._repo = MagicMock()
    now = datetime.now(timezone.utc)
    store._repo.create_api_key.return_value = SimpleNamespace(
        id="created",
        name="ci",
        description="created",
        created_at=now,
        expires_at=None,
    )

    created = store.create_api_key(
        user_id="admin",
        request=APIKeyCreate(
            name="ci",
            description="created",
            expires_at=None,
            permissions=[Permission.CONVERT_UNITS],
        ),
    )
    assert created.id == "created"
    assert created.key.startswith("sds_")
    assert store._repo.create_api_key.call_args.kwargs["expires_at"] is not None

    record = SimpleNamespace(
        id="deadbeef",
        user_id="admin",
        secret_hash=jwt_handler.hash_api_key("secret"),
        permissions=["not_a_permission"],
        expires_at=None,
        is_active=False,
    )
    store._repo.get_api_key_by_id.return_value = None
    assert store.authenticate("bad") is None
    assert store.authenticate("sds_deadbeef.secret") is None

    store._repo.get_api_key_by_id.return_value = record
    assert store.authenticate("sds_deadbeef.secret") is None
    record.is_active = True
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert store.authenticate("sds_deadbeef.secret") is None
    record.expires_at = None
    assert store.authenticate("sds_deadbeef.wrong") is None
    resolved = store.authenticate("sds_deadbeef.secret")
    assert resolved == ResolvedAPIKey(
        key_id="deadbeef", user_id="admin", permissions=[]
    )


def test_database_api_key_store_authenticates_naive_db_expiry_timestamp():
    store = DatabaseAPIKeyStore(MagicMock())
    store._repo = MagicMock()

    key_id = "deadbeef"
    secret = "secret"
    record = SimpleNamespace(
        id=key_id,
        user_id="admin",
        secret_hash=jwt_handler.hash_api_key(secret),
        permissions=[Permission.CONVERT_UNITS.value],
        expires_at=datetime.utcnow() + timedelta(days=1),
        is_active=True,
    )
    store._repo.get_api_key_by_id.return_value = record
    store._repo.set_last_used.return_value = True

    resolved = store.authenticate(f"sds_{key_id}.{secret}")

    assert resolved == ResolvedAPIKey(
        key_id=key_id, user_id="admin", permissions=[Permission.CONVERT_UNITS]
    )
    store._repo.set_last_used.assert_called_once()

    record.expires_at = datetime.utcnow() - timedelta(seconds=1)
    assert store.authenticate(f"sds_{key_id}.{secret}") is None


def test_database_api_key_store_list_maps_permissions_and_skips_invalid_values():
    store = DatabaseAPIKeyStore(MagicMock())
    store._repo = MagicMock()

    record = SimpleNamespace(
        id="k1",
        user_id="admin",
        name="ci",
        description=None,
        permissions=[Permission.CONVERT_UNITS.value, "not_a_permission"],
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        last_used=None,
        is_active=True,
    )
    store._repo.list_api_keys_for_user.return_value = [record]

    listed = store.list_api_keys(user_id="admin")
    assert len(listed) == 1
    assert listed[0].permissions == [Permission.CONVERT_UNITS]

    record.permissions = "not-a-list"
    listed = store.list_api_keys(user_id="admin")
    assert listed[0].permissions == []


def test_database_api_key_store_revoke_requires_owned_key():
    store = DatabaseAPIKeyStore(MagicMock())
    store._repo = MagicMock()
    store._repo.get_api_key_by_id.return_value = None
    assert store.revoke_api_key(user_id="admin", key_id="missing") is False
    store._repo.get_api_key_by_id.return_value = SimpleNamespace(user_id="other")
    assert store.revoke_api_key(user_id="admin", key_id="other") is False


def test_get_api_key_store_requires_db_session_when_required(monkeypatch):
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
            get_api_key_store(request, db=None)

        store = get_api_key_store(request, db=MagicMock())
        assert isinstance(store, DatabaseAPIKeyStore)
    finally:
        monkeypatch.setattr(settings, "require_database", original)


def test_get_api_key_store_caches_inmemory_store_when_db_not_required(monkeypatch):
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
        store1 = get_api_key_store(request, db=None)
        store2 = get_api_key_store(request, db=None)
        assert isinstance(store1, InMemoryAPIKeyStore)
        assert store1 is store2
    finally:
        monkeypatch.setattr(settings, "require_database", original)
