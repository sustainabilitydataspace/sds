"""Direct unit tests for FastAPI auth dependency helpers."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from src.auth.dependencies import (
    get_current_active_user,
    get_current_user,
    get_optional_user,
    get_token_data,
    require_any_permission,
    require_company_access,
    require_permission,
    require_permissions,
    require_role,
)
from src.auth.jwt_handler import jwt_handler
from src.auth.models import Permission, TokenData, User, UserRole
from src.config.settings import settings


def _make_user(
    role: UserRole,
    permissions: list[Permission] | None = None,
    company_id: str | None = "company_1",
    is_active: bool = True,
) -> User:
    now = datetime.now(timezone.utc)
    return User(
        id="user_1",
        username="user",
        email="user@example.com",
        full_name=None,
        company_id=company_id,
        role=role,
        auth_method="bearer",
        is_active=is_active,
        created_at=now,
        updated_at=now,
        last_login=None,
        permissions=permissions or [],
    )


@pytest.mark.asyncio
async def test_get_current_user_from_token_data():
    token_data = TokenData(
        user_id="u1",
        username="alice",
        role=UserRole.ANALYST,
        company_id="c1",
        permissions=[Permission.READ_VALUES],
        exp=datetime.now(timezone.utc) + timedelta(minutes=5),
        iat=datetime.now(timezone.utc),
    )

    user = await get_current_user(token_data=token_data)
    assert user.id == "u1"
    assert user.username == "alice"
    assert user.role == UserRole.ANALYST
    assert Permission.READ_VALUES in user.permissions


@pytest.mark.asyncio
async def test_get_current_user_database_mode_preserves_api_key_permissions(
    monkeypatch,
):
    token_data = TokenData(
        user_id="admin-001",
        username="admin",
        role=UserRole.ADMIN,
        company_id="tenant",
        permissions=[Permission.CONVERT_UNITS],
        auth_method="api_key",
    )
    active = _make_user(
        UserRole.ADMIN,
        permissions=[Permission.MANAGE_USERS, Permission.CONVERT_UNITS],
        company_id="tenant",
    )
    active.id = "admin-001"
    monkeypatch.setattr(settings, "require_database", True)

    user = await get_current_user(
        token_data=token_data,
        users=SimpleNamespace(get_user_by_id=lambda user_id: active),
    )

    assert user.role == UserRole.ADMIN
    assert user.permissions == [Permission.CONVERT_UNITS]

    denied = require_permission(Permission.MANAGE_USERS)
    with pytest.raises(HTTPException) as exc:
        await denied(current_user=user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_current_user_offline_mode_preserves_api_key_permissions(
    monkeypatch,
):
    token_data = TokenData(
        user_id="admin-001",
        username="admin",
        role=UserRole.ADMIN,
        company_id="tenant",
        permissions=[Permission.CONVERT_UNITS],
        auth_method="api_key",
    )
    monkeypatch.setattr(settings, "require_database", False)

    user = await get_current_user(token_data=token_data)

    assert user.role == UserRole.ADMIN
    assert user.permissions == [Permission.CONVERT_UNITS]


@pytest.mark.asyncio
async def test_get_current_active_user_inactive_raises():
    inactive = _make_user(
        UserRole.VIEWER, permissions=[Permission.READ_VALUES], is_active=False
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_active_user(current_user=inactive)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_require_role_dependency():
    checker = require_role(UserRole.DATA_MANAGER)
    viewer = _make_user(UserRole.VIEWER)
    dm = _make_user(UserRole.DATA_MANAGER)

    with pytest.raises(HTTPException) as exc:
        await checker(current_user=viewer)
    assert exc.value.status_code == 403
    assert await checker(current_user=dm) == dm


@pytest.mark.asyncio
async def test_require_permission_dependency():
    checker = require_permission(Permission.READ_VALUES)
    denied = _make_user(UserRole.VIEWER, permissions=[])
    allowed = _make_user(UserRole.VIEWER, permissions=[Permission.READ_VALUES])

    with pytest.raises(HTTPException) as exc:
        await checker(current_user=denied)
    assert exc.value.status_code == 403
    assert await checker(current_user=allowed) == allowed


@pytest.mark.asyncio
async def test_require_permissions_all_required():
    checker = require_permissions(Permission.READ_VALUES, Permission.CONVERT_UNITS)
    user = _make_user(UserRole.VIEWER, permissions=[Permission.READ_VALUES])

    with pytest.raises(HTTPException) as exc:
        await checker(current_user=user)
    assert exc.value.status_code == 403

    user.permissions.append(Permission.CONVERT_UNITS)
    assert await checker(current_user=user) == user


@pytest.mark.asyncio
async def test_require_any_permission_or_logic():
    checker = require_any_permission(
        Permission.MANAGE_USERS, Permission.VIEW_SYSTEM_HEALTH
    )
    user = _make_user(UserRole.VIEWER, permissions=[])

    with pytest.raises(HTTPException) as exc:
        await checker(current_user=user)
    assert exc.value.status_code == 403

    user.permissions.append(Permission.VIEW_SYSTEM_HEALTH)
    assert await checker(current_user=user) == user


@pytest.mark.asyncio
async def test_require_company_access_dependency():
    checker = require_company_access(allow_admin_override=True)
    user = _make_user(UserRole.VIEWER, company_id="c1")
    admin = _make_user(UserRole.ADMIN, company_id="admin_co")

    with pytest.raises(HTTPException) as exc:
        await checker(company_id="c2", current_user=user)
    assert exc.value.status_code == 403

    assert await checker(company_id="c2", current_user=admin) == admin


@pytest.mark.asyncio
async def test_get_optional_user_none_without_credentials():
    assert await get_optional_user(credentials=None) is None


@pytest.mark.asyncio
async def test_get_optional_user_returns_user_for_valid_token():
    token = jwt_handler.create_access_token(
        user_id="u1",
        username="bob",
        role=UserRole.VIEWER,
        company_id="c1",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    user = await get_optional_user(credentials=creds)
    assert user is not None
    assert user.username == "bob"


@pytest.mark.asyncio
async def test_get_token_data_invalid_token_raises_401():
    creds = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials="invalid.token.value"
    )
    with pytest.raises(HTTPException) as exc:
        await get_token_data(credentials=creds)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_token_data_database_mode_validates_persistent_user(monkeypatch):
    token = jwt_handler.create_access_token(
        user_id="u-db",
        username="dbuser",
        role=UserRole.VIEWER,
        company_id="tenant-db",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    user = _make_user(
        UserRole.DATA_MANAGER,
        permissions=[Permission.READ_VALUES],
        company_id="tenant-db",
    )
    user.id = "u-db"
    user.username = "dbuser"
    users = SimpleNamespace(get_user_by_id=lambda user_id: user)

    class _Session:
        closed = False

        def query(self, _model):
            return SimpleNamespace(
                filter_by=lambda **_kw: SimpleNamespace(first=lambda: None)
            )

        def close(self):
            self.closed = True

    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr("src.database.session.SessionLocal", lambda: _Session())

    data = await get_token_data(
        request=SimpleNamespace(
            scope={}, method="GET", url=SimpleNamespace(path="/auth/me")
        ),
        credentials=creds,
        api_key=None,
        users=users,
    )

    assert data.user_id == "u-db"
    assert data.role == UserRole.DATA_MANAGER
    assert data.permissions == [Permission.READ_VALUES]


@pytest.mark.asyncio
async def test_get_token_data_database_mode_rejects_persistent_revocation(monkeypatch):
    token = jwt_handler.create_access_token(
        user_id="u-db",
        username="dbuser",
        role=UserRole.VIEWER,
        company_id="tenant-db",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    class _Session:
        def close(self):
            self.closed = True

    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr("src.database.session.SessionLocal", lambda: _Session())
    monkeypatch.setattr(jwt_handler, "is_revoked_persistent", lambda _token, _db: True)

    with pytest.raises(HTTPException) as exc:
        await get_token_data(
            credentials=creds,
            api_key=None,
            users=SimpleNamespace(get_user_by_id=lambda user_id: None),
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_token_data_database_mode_revocation_check_error_fails_closed(
    monkeypatch,
):
    token = jwt_handler.create_access_token(
        user_id="u-db",
        username="dbuser",
        role=UserRole.VIEWER,
        company_id="tenant-db",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    user = _make_user(
        UserRole.VIEWER,
        permissions=[Permission.READ_VALUES],
        company_id="tenant-db",
    )
    user.id = "u-db"
    user.username = "dbuser"

    class _Session:
        def close(self):
            self.closed = True

    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr("src.database.session.SessionLocal", lambda: _Session())
    monkeypatch.setattr(
        jwt_handler,
        "is_revoked_persistent",
        lambda _token, _db: (_ for _ in ()).throw(RuntimeError("revocation DB down")),
    )

    with pytest.raises(HTTPException) as exc:
        await get_token_data(
            credentials=creds,
            api_key=None,
            users=SimpleNamespace(get_user_by_id=lambda user_id: user),
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_token_data_database_mode_rejects_missing_user(monkeypatch):
    token = jwt_handler.create_access_token(
        user_id="missing",
        username="missing",
        role=UserRole.VIEWER,
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(
        "src.database.session.SessionLocal",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    with pytest.raises(HTTPException) as exc:
        await get_token_data(
            credentials=creds,
            api_key=None,
            users=SimpleNamespace(get_user_by_id=lambda user_id: None),
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_token_data_api_key_branches():
    resolved = SimpleNamespace(
        user_id="api-user",
        key_id="test-key-id",
        permissions=[Permission.READ_VALUES, Permission.CONVERT_UNITS],
    )
    user = _make_user(UserRole.ANALYST, permissions=[Permission.READ_VALUES])
    user.id = "api-user"
    api_keys = SimpleNamespace(
        authenticate=lambda key: resolved if key == "good" else None
    )
    users = SimpleNamespace(get_user_by_id=lambda user_id: user)

    token_data = await get_token_data(
        credentials=None,
        api_key="good",
        api_keys=api_keys,
        users=users,
    )
    assert token_data.user_id == "api-user"
    assert token_data.permissions == [Permission.READ_VALUES]

    with pytest.raises(HTTPException) as exc:
        await get_token_data(
            credentials=None,
            api_key="bad",
            api_keys=api_keys,
            users=users,
        )
    assert exc.value.status_code == 401

    inactive = _make_user(UserRole.ANALYST, permissions=[Permission.READ_VALUES])
    inactive.id = "api-user"
    inactive.is_active = False
    inactive_users = SimpleNamespace(get_user_by_id=lambda user_id: inactive)
    with pytest.raises(HTTPException) as exc:
        await get_token_data(
            credentials=None,
            api_key="good",
            api_keys=api_keys,
            users=inactive_users,
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_token_data_wraps_unexpected_validation_errors(monkeypatch):
    monkeypatch.setattr(
        "src.auth.dependencies.jwt_handler.verify_token",
        lambda _token: (_ for _ in ()).throw(RuntimeError("decoder exploded")),
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")

    with pytest.raises(HTTPException) as exc:
        await get_token_data(credentials=creds, api_key=None)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_database_mode_uses_store_and_rejects_inactive(
    monkeypatch,
):
    token_data = TokenData(
        user_id="u1",
        username="alice",
        role=UserRole.VIEWER,
        company_id="tenant",
        permissions=[],
    )
    active = _make_user(UserRole.VIEWER)
    monkeypatch.setattr(settings, "require_database", True)
    assert (
        await get_current_user(
            token_data=token_data,
            users=SimpleNamespace(get_user_by_id=lambda user_id: active),
        )
    ).model_dump() == active.model_dump()

    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            token_data=token_data,
            users=SimpleNamespace(get_user_by_id=lambda user_id: None),
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_company_access_allows_matching_company_without_admin():
    checker = require_company_access(allow_admin_override=False)
    user = _make_user(UserRole.VIEWER, company_id="c1")

    assert await checker(company_id="c1", current_user=user) == user


@pytest.mark.asyncio
async def test_get_optional_user_silently_ignores_verify_failure(monkeypatch):
    monkeypatch.setattr(
        "src.auth.dependencies.jwt_handler.verify_token",
        lambda _token: (_ for _ in ()).throw(RuntimeError("decoder exploded")),
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    assert await get_optional_user(credentials=creds) is None
