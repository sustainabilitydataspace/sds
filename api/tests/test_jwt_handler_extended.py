"""Extra JWTHandler branch coverage tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest

from src.auth.jwt_handler import jwt_handler
from src.auth.models import Permission, UserRole


def test_create_refresh_token_and_refresh_access_token_success():
    refresh = jwt_handler.create_refresh_token(user_id="u1", username="alice")
    refreshed = jwt_handler.refresh_access_token(refresh)

    assert refreshed is not None
    assert refreshed["token_type"] == "bearer"
    assert "access_token" in refreshed

    token_data = jwt_handler.verify_token(refreshed["access_token"])
    assert token_data is not None
    assert token_data.user_id == "u1"
    assert token_data.username == "alice"


def test_refresh_access_token_rejects_non_refresh_token():
    access = jwt_handler.create_access_token(
        user_id="u1",
        username="alice",
        role=UserRole.VIEWER,
        company_id="c1",
    )
    assert jwt_handler.refresh_access_token(access) is None


def test_verify_token_invalid_role_and_permissions_falls_back_to_viewer():
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "u1",
        "username": "alice",
        "role": "not-a-role",
        "company_id": "c1",
        "permissions": ["not-a-permission"],
        "auth_version": 0,
        "exp": now + timedelta(minutes=10),
        "iat": now,
        "type": "access",
    }
    token = jwt.encode(payload, jwt_handler.secret_key, algorithm=jwt_handler.algorithm)

    token_data = jwt_handler.verify_token(token)
    assert token_data is not None
    assert token_data.role == UserRole.VIEWER
    assert token_data.permissions == []

    payload.pop("auth_version")
    unversioned = jwt.encode(
        payload, jwt_handler.secret_key, algorithm=jwt_handler.algorithm
    )
    assert jwt_handler.verify_token(unversioned) is None


def test_verify_token_expired_returns_none():
    expired = jwt_handler.create_access_token(
        user_id="u1",
        username="alice",
        role=UserRole.VIEWER,
        company_id="c1",
        expires_delta=timedelta(minutes=-1),
    )
    assert jwt_handler.verify_token(expired) is None


def test_get_token_expiry_info_and_invalidate_token():
    token = jwt_handler.create_access_token(
        user_id="u1",
        username="alice",
        role=UserRole.ANALYST,
        company_id="c1",
    )
    info = jwt_handler.get_token_expiry_info(token)
    assert info is not None
    assert info["token_type"] == "access"
    assert isinstance(info["expires_in_seconds"], int)

    assert jwt_handler.invalidate_token(token) is True
    assert jwt_handler.invalidate_token("invalid.token") is False


def test_api_key_hash_and_verify_truncation_compatible():
    api_key = jwt_handler.create_api_key()
    hashed = jwt_handler.hash_api_key(api_key)
    assert jwt_handler.verify_api_key(api_key, hashed) is True

    long_key = "sds_" + ("a" * 400)
    long_hash = jwt_handler.hash_api_key(long_key)
    assert jwt_handler.verify_api_key(long_key, long_hash) is True


def test_password_hash_and_verify():
    hashed = jwt_handler.hash_password("password123")
    assert jwt_handler.verify_password("password123", hashed) is True
    assert jwt_handler.verify_password("wrong", hashed) is False
    assert jwt_handler.verify_password("password123", "not-a-bcrypt-hash") is False


def test_get_token_expiry_info_invalid_token_returns_none():
    assert jwt_handler.get_token_expiry_info("not-a-token") is None


def test_get_token_expiry_info_missing_exp_returns_none():
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "u1",
        "username": "alice",
        "role": UserRole.VIEWER.value,
        "iat": now,
        "type": "access",
    }
    token = jwt.encode(payload, jwt_handler.secret_key, algorithm=jwt_handler.algorithm)
    assert jwt_handler.get_token_expiry_info(token) is None


def test_verify_token_rejects_revoked_non_access_and_missing_subject():
    access = jwt_handler.create_access_token(
        user_id="u1",
        username="alice",
        role=UserRole.VIEWER,
        company_id="c1",
    )
    fingerprint = jwt_handler._token_fingerprint(access)
    jwt_handler._revoked_tokens.add(fingerprint)
    try:
        assert jwt_handler.verify_token(access) is None
    finally:
        jwt_handler._revoked_tokens.discard(fingerprint)

    refresh = jwt_handler.create_refresh_token(user_id="u1", username="alice")
    assert jwt_handler.verify_token(refresh) is None

    now = datetime.now(timezone.utc)
    missing_sub = jwt.encode(
        {
            "username": "alice",
            "role": UserRole.VIEWER.value,
            "exp": now + timedelta(minutes=10),
            "iat": now,
            "type": "access",
        },
        jwt_handler.secret_key,
        algorithm=jwt_handler.algorithm,
    )
    assert jwt_handler.verify_token(missing_sub) is None


def test_refresh_access_token_user_lookup_branches_and_failures():
    token = jwt_handler.create_refresh_token(
        user_id="u1",
        username="alice",
        role=UserRole.ANALYST,
        company_id="c1",
    )

    def user_lookup(user_id: str):
        assert user_id == "u1"
        return SimpleNamespace(
            is_active=True,
            role=UserRole.DATA_MANAGER,
            company_id="tenant-from-store",
            auth_version=0,
        )

    refreshed = jwt_handler.refresh_access_token(token, user_lookup=user_lookup)
    assert refreshed is not None
    token_data = jwt_handler.verify_token(refreshed["access_token"])
    assert token_data.role == UserRole.DATA_MANAGER
    assert token_data.company_id == "tenant-from-store"

    assert (
        jwt_handler.refresh_access_token(token, user_lookup=lambda _user_id: None)
        is None
    )
    assert (
        jwt_handler.refresh_access_token(
            token,
            user_lookup=lambda _user_id: SimpleNamespace(is_active=False),
        )
        is None
    )

    expired = jwt_handler.create_refresh_token(
        user_id="u1",
        username="alice",
        expires_delta=timedelta(minutes=-1),
    )
    assert jwt_handler.refresh_access_token(expired) is None
    assert jwt_handler.refresh_access_token("not-a-refresh-token") is None


def test_refresh_access_token_handles_revoked_and_invalid_embedded_role():
    token = jwt_handler.create_refresh_token(
        user_id="u1",
        username="alice",
        role=UserRole.ANALYST,
    )
    fingerprint = jwt_handler._token_fingerprint(token)
    jwt_handler._revoked_tokens.add(fingerprint)
    try:
        assert jwt_handler.refresh_access_token(token) is None
    finally:
        jwt_handler._revoked_tokens.discard(fingerprint)

    now = datetime.now(timezone.utc)
    payload = {
        "sub": "u2",
        "username": "bob",
        "role": "not-a-role",
        "auth_version": 0,
        "exp": now + timedelta(minutes=10),
        "iat": now,
        "type": "refresh",
    }
    token = jwt.encode(payload, jwt_handler.secret_key, algorithm=jwt_handler.algorithm)

    refreshed = jwt_handler.refresh_access_token(token)
    assert refreshed is not None
    token_data = jwt_handler.verify_token(refreshed["access_token"])
    assert token_data.role == UserRole.VIEWER


def test_persistent_revocation_checks_memory_and_database_cache():
    token = jwt_handler.create_access_token(
        user_id="u1",
        username="alice",
        role=UserRole.VIEWER,
    )
    fingerprint = jwt_handler._token_fingerprint(token)

    class _Query:
        def __init__(self, row):
            self.row = row

        def filter_by(self, **kwargs):
            assert kwargs == {"token_hash": fingerprint}
            return self

        def first(self):
            return self.row

    class _Db:
        def __init__(self, row):
            self.row = row

        def query(self, _model):
            return _Query(self.row)

    jwt_handler._revoked_tokens.discard(fingerprint)
    assert jwt_handler.is_revoked_persistent(token, _Db(row=None)) is False
    assert jwt_handler.is_revoked_persistent(token, _Db(row=object())) is True
    assert jwt_handler.is_revoked_persistent(token, _Db(row=None)) is True
    jwt_handler._revoked_tokens.discard(fingerprint)


def test_verify_and_refresh_handle_decoder_edge_branches(monkeypatch):
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    future = datetime.now(timezone.utc) + timedelta(minutes=5)

    access_payload = {
        "sub": "u1",
        "username": "alice",
        "role": UserRole.VIEWER.value,
        "permissions": [Permission.READ_INDICATORS.value],
        "exp": past.timestamp(),
        "iat": future.timestamp(),
        "type": "access",
    }
    monkeypatch.setattr(jwt, "decode", lambda *args, **kwargs: access_payload)
    assert jwt_handler.verify_token("token-with-past-exp") is None

    monkeypatch.setattr(
        jwt,
        "decode",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("decode exploded")),
    )
    assert jwt_handler.verify_token("token-error") is None
    assert jwt_handler.refresh_access_token("token-error") is None

    refresh_payload = {
        "sub": "u1",
        "username": "alice",
        "role": UserRole.VIEWER.value,
        "exp": past.timestamp(),
        "iat": future.timestamp(),
        "type": "refresh",
    }
    monkeypatch.setattr(jwt, "decode", lambda *args, **kwargs: refresh_payload)
    assert jwt_handler.refresh_access_token("expired-refresh") is None


def test_invalidate_token_is_idempotent_for_already_revoked_tokens():
    token = jwt_handler.create_access_token(
        user_id="u1",
        username="alice",
        role=UserRole.VIEWER,
    )
    fingerprint = jwt_handler._token_fingerprint(token)
    jwt_handler._revoked_tokens.add(fingerprint)
    try:
        assert jwt_handler.invalidate_token(token) is True
    finally:
        jwt_handler._revoked_tokens.discard(fingerprint)
