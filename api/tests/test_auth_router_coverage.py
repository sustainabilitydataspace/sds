"""Tests for auth router to achieve full coverage.

Targets uncovered lines:
- 90-92: Exception handling in login
- 128-130: Exception handling in refresh_token
- 158-160: Exception handling in logout
- 236-240: Exception handling in update_current_user
- 268, 277-281: Password change error handling
- 302, 309-313: Token info error handling
- 355-361: User creation error handling
- 388-392: API key creation error handling
- 408, 423: API key list/revoke
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routers import auth as auth_router
from src.auth.jwt_handler import jwt_handler
from src.auth.models import ROLE_PERMISSIONS, Permission, User, UserRole
from src.config.settings import settings
from src.database.session import get_db
from src.services.api_key_store import get_api_key_store
from src.services.user_store import get_user_store


def get_mock_db():
    """Create mock DB for testing."""
    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = []
    mock_query.first.return_value = None
    return mock_db


@pytest.fixture
def admin_token() -> str:
    """Create admin JWT token."""
    return jwt_handler.create_access_token(
        user_id="admin-001",
        username="admin",
        role=UserRole.ADMIN,
        company_id="company-1",
    )


@pytest.fixture
def viewer_token() -> str:
    """Create viewer JWT token."""
    return jwt_handler.create_access_token(
        user_id="viewer-001",
        username="viewer_user",
        role=UserRole.VIEWER,
        company_id="company-1",
    )


def auth_header(token: str) -> dict:
    """Create authorization header."""
    return {"Authorization": f"Bearer {token}"}


class TestLoginExceptionHandling:
    """Tests for login exception handling (lines 90-92)."""

    def test_login_internal_error_returns_500(self):
        """Test that internal errors in login return 500."""
        mock_store = MagicMock()
        mock_store.authenticate.side_effect = RuntimeError("Database connection failed")

        app.dependency_overrides[get_db] = get_mock_db
        app.dependency_overrides[get_user_store] = lambda: mock_store
        client = TestClient(app)

        try:
            response = client.post(
                "/auth/login",
                json={"username": "testuser", "password": "testpass"},
            )

            assert response.status_code == 500
            assert "internal error" in response.json()["error"]["message"].lower()
        finally:
            app.dependency_overrides.clear()


class TestRefreshExceptionHandling:
    """Tests for token refresh exception handling (lines 128-130)."""

    def test_refresh_internal_error_returns_500(self):
        """Test that internal errors in refresh return 500."""
        app.dependency_overrides[get_db] = get_mock_db
        client = TestClient(app)

        try:
            with patch.object(jwt_handler, "refresh_access_token") as mock_refresh:
                mock_refresh.side_effect = RuntimeError("Token processing failed")

                response = client.post(
                    "/auth/refresh",
                    json={"refresh_token": "valid-looking-token"},
                )

                assert response.status_code == 500
                assert "refresh failed" in response.json()["error"]["message"].lower()
        finally:
            app.dependency_overrides.clear()


class TestLogoutExceptionHandling:
    """Tests for logout exception handling (lines 158-160)."""

    def test_logout_internal_error_returns_500(self, admin_token):
        """Test that internal errors in logout return 500."""
        app.dependency_overrides[get_db] = get_mock_db
        client = TestClient(app)

        try:
            with patch.object(jwt_handler, "invalidate_token") as mock_invalidate:
                mock_invalidate.side_effect = RuntimeError("Token invalidation failed")

                response = client.post(
                    "/auth/logout",
                    headers=auth_header(admin_token),
                )

                assert response.status_code == 500
                assert "logout failed" in response.json()["error"]["message"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_logout_persists_revocation_when_db_required(
        self, admin_token, monkeypatch
    ):
        """Logout writes persistent revocation and closes the DB session in DB mode."""
        closed = {"value": False}
        revoked = {}

        class FakeDb:
            def close(self):
                closed["value"] = True

        monkeypatch.setattr(settings, "require_database", True)
        monkeypatch.setattr("src.database.session.SessionLocal", lambda: FakeDb())
        monkeypatch.setattr(
            jwt_handler,
            "invalidate_token",
            lambda token: True,
        )
        monkeypatch.setattr(
            jwt_handler,
            "get_token_expiry_info",
            lambda token: {"expires_at": "2026-01-01T00:00:00+00:00"},
        )
        monkeypatch.setattr(
            jwt_handler,
            "revoke_token_persistent",
            lambda token, expires_at, db: revoked.update(
                {"token": token, "expires_at": expires_at, "db": db}
            ),
        )
        current_user = User(
            id="admin-001",
            username="admin",
            email="admin@example.com",
            role=UserRole.ADMIN,
            auth_method="bearer",
            is_active=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            permissions=ROLE_PERMISSIONS[UserRole.ADMIN],
        )

        response = await auth_router.logout(
            credentials=HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=admin_token
            ),
            current_user=current_user,
        )

        assert response == {"message": "Successfully logged out"}
        assert revoked["token"] == admin_token
        assert closed["value"] is True

    @pytest.mark.asyncio
    async def test_logout_fails_closed_when_persistent_revocation_fails(
        self, admin_token, monkeypatch
    ):
        """Logout must fail closed (500) when persistent revocation fails (F02-C1)."""
        closed = {"value": False}

        class FakeDb:
            def close(self):
                closed["value"] = True

        monkeypatch.setattr(settings, "require_database", True)
        monkeypatch.setattr("src.database.session.SessionLocal", lambda: FakeDb())
        monkeypatch.setattr(jwt_handler, "invalidate_token", lambda _token: True)
        monkeypatch.setattr(jwt_handler, "get_token_expiry_info", lambda _token: None)
        monkeypatch.setattr(
            jwt_handler,
            "revoke_token_persistent",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        current_user = User(
            id="admin-001",
            username="admin",
            email="admin@example.com",
            role=UserRole.ADMIN,
            auth_method="bearer",
            is_active=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            permissions=ROLE_PERMISSIONS[UserRole.ADMIN],
        )

        with pytest.raises(HTTPException) as exc_info:
            await auth_router.logout(
                credentials=HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials=admin_token
                ),
                current_user=current_user,
            )

        assert exc_info.value.status_code == 500
        assert closed["value"] is True


class TestUpdateUserExceptionHandling:
    """Tests for update user exception handling (lines 236-240)."""

    def test_update_user_internal_error_returns_500(self, admin_token):
        """Test that internal errors in update user return 500."""
        mock_store = MagicMock()
        mock_store.update_user.side_effect = RuntimeError("Database update failed")

        app.dependency_overrides[get_db] = get_mock_db
        app.dependency_overrides[get_user_store] = lambda: mock_store
        client = TestClient(app)

        try:
            response = client.put(
                "/auth/me",
                json={"full_name": "New Name"},
                headers=auth_header(admin_token),
            )

            assert response.status_code == 500
            assert "update failed" in response.json()["error"]["message"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_current_user_info_reports_effective_permissions(self):
        current_user = User(
            id="admin-001",
            username="admin",
            email="admin@example.com",
            role=UserRole.ADMIN,
            auth_method="bearer",
            is_active=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            permissions=[Permission.READ_VALUES],
        )
        persisted_user = current_user.model_copy(
            update={"permissions": ROLE_PERMISSIONS[UserRole.ADMIN]}
        )
        store = MagicMock()
        store.get_user.return_value = persisted_user

        response = await auth_router.get_current_user_info(
            current_user=current_user,
            store=store,
        )

        assert response.role == UserRole.ADMIN
        assert response.permissions == [Permission.READ_VALUES]

    @pytest.mark.asyncio
    async def test_update_user_preserves_domain_http_exception(self):
        current_user = User(
            id="viewer-001",
            username="viewer",
            email="viewer@example.com",
            role=UserRole.VIEWER,
            auth_method="bearer",
            is_active=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            permissions=ROLE_PERMISSIONS[UserRole.VIEWER],
        )
        store = MagicMock()
        store.update_user.side_effect = HTTPException(status_code=409, detail="dupe")

        with pytest.raises(HTTPException) as exc:
            await auth_router.update_current_user(
                auth_router.UserUpdate(full_name="Viewer"),
                current_user=current_user,
                store=store,
            )

        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_bearer_admin_without_manage_users_denies_admin_self_update(self):
        current_user = User(
            id="admin-001",
            username="admin",
            email="admin@example.com",
            role=UserRole.ADMIN,
            auth_method="bearer",
            is_active=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            permissions=[Permission.READ_VALUES],
        )
        store = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await auth_router.update_current_user(
                auth_router.UserUpdate(role=UserRole.VIEWER),
                current_user=current_user,
                store=store,
            )

        assert exc.value.status_code == 403
        store.update_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_bearer_admin_read_only_permissions_allow_self_service_update(self):
        current_user = User(
            id="admin-001",
            username="admin",
            email="admin@example.com",
            role=UserRole.ADMIN,
            auth_method="bearer",
            is_active=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            permissions=[Permission.READ_VALUES],
        )
        updated_user = current_user.model_copy(update={"full_name": "Admin User"})
        store = MagicMock()
        store.update_user.return_value = updated_user

        response = await auth_router.update_current_user(
            auth_router.UserUpdate(full_name="Admin User"),
            current_user=current_user,
            store=store,
        )

        assert response.full_name == "Admin User"
        assert response.permissions == [Permission.READ_VALUES]
        _, kwargs = store.update_user.call_args
        assert kwargs["allow_admin_fields"] is False

    @pytest.mark.asyncio
    async def test_bearer_admin_manage_users_allows_admin_self_update(self):
        current_user = User(
            id="admin-001",
            username="admin",
            email="admin@example.com",
            role=UserRole.ADMIN,
            auth_method="bearer",
            is_active=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            permissions=[Permission.MANAGE_USERS],
        )
        updated_user = current_user.model_copy(update={"company_id": "company-2"})
        store = MagicMock()
        store.update_user.return_value = updated_user

        response = await auth_router.update_current_user(
            auth_router.UserUpdate(company_id="company-2"),
            current_user=current_user,
            store=store,
        )

        assert response.company_id == "company-2"
        _, kwargs = store.update_user.call_args
        assert kwargs["allow_admin_fields"] is True


class TestPasswordChangeExceptionHandling:
    """Tests for password change exception handling (lines 268, 277-281)."""

    def test_password_change_wrong_current_password(self, admin_token):
        """Test that wrong current password returns 401."""
        mock_store = MagicMock()
        mock_store.change_password.return_value = False  # Wrong password

        app.dependency_overrides[get_db] = get_mock_db
        app.dependency_overrides[get_user_store] = lambda: mock_store
        client = TestClient(app)

        try:
            response = client.post(
                "/auth/change-password",
                json={"current_password": "wrong", "new_password": "newpass123"},
                headers=auth_header(admin_token),
            )

            assert response.status_code == 401
            assert (
                "invalid current password"
                in response.json()["error"]["message"].lower()
            )
        finally:
            app.dependency_overrides.clear()

    def test_password_change_internal_error_returns_500(self, admin_token):
        """Test that internal errors in password change return 500."""
        mock_store = MagicMock()
        mock_store.change_password.side_effect = RuntimeError("Database error")

        app.dependency_overrides[get_db] = get_mock_db
        app.dependency_overrides[get_user_store] = lambda: mock_store
        client = TestClient(app)

        try:
            response = client.post(
                "/auth/change-password",
                json={"current_password": "current", "new_password": "newpass123"},
                headers=auth_header(admin_token),
            )

            assert response.status_code == 500
            assert "failed" in response.json()["error"]["message"].lower()
        finally:
            app.dependency_overrides.clear()


class TestTokenInfoExceptionHandling:
    """Tests for token info exception handling (lines 302, 309-313)."""

    def test_token_info_invalid_token_returns_401(self, admin_token):
        """Test that invalid token returns 401."""
        app.dependency_overrides[get_db] = get_mock_db
        client = TestClient(app)

        try:
            with patch.object(jwt_handler, "get_token_expiry_info") as mock_info:
                mock_info.return_value = None  # Invalid token

                response = client.get(
                    "/auth/token/info",
                    headers=auth_header(admin_token),
                )

                assert response.status_code == 401
                assert "invalid token" in response.json()["error"]["message"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_token_info_internal_error_returns_500(self, admin_token):
        """Test that internal errors in token info return 500."""
        app.dependency_overrides[get_db] = get_mock_db
        client = TestClient(app)

        try:
            with patch.object(jwt_handler, "get_token_expiry_info") as mock_info:
                mock_info.side_effect = RuntimeError("Token processing error")

                response = client.get(
                    "/auth/token/info",
                    headers=auth_header(admin_token),
                )

                assert response.status_code == 500
                assert "failed" in response.json()["error"]["message"].lower()
        finally:
            app.dependency_overrides.clear()


class TestUserCreationExceptionHandling:
    """Tests for user creation exception handling (lines 355-361)."""

    def test_create_user_value_error_returns_400(self, admin_token):
        """Test that ValueError in create user returns 400."""
        mock_store = MagicMock()
        mock_store.create_user.side_effect = ValueError("Username already exists")

        app.dependency_overrides[get_db] = get_mock_db
        app.dependency_overrides[get_user_store] = lambda: mock_store
        client = TestClient(app)

        try:
            response = client.post(
                "/auth/users",
                json={
                    "username": "newuser",
                    "password": "password123",
                    "email": "new@example.com",
                    "role": "viewer",
                },
                headers=auth_header(admin_token),
            )

            assert response.status_code == 400
            assert "already exists" in response.json()["error"]["message"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_create_user_internal_error_returns_500(self, admin_token):
        """Test that internal errors in create user return 500."""
        mock_store = MagicMock()
        mock_store.create_user.side_effect = RuntimeError("Database error")

        app.dependency_overrides[get_db] = get_mock_db
        app.dependency_overrides[get_user_store] = lambda: mock_store
        client = TestClient(app)

        try:
            response = client.post(
                "/auth/users",
                json={
                    "username": "newuser",
                    "password": "password123",
                    "email": "new@example.com",
                    "role": "viewer",
                },
                headers=auth_header(admin_token),
            )

            assert response.status_code == 500
            assert "creation failed" in response.json()["error"]["message"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_create_user_preserves_domain_http_exception(self):
        current_user = User(
            id="admin-001",
            username="admin",
            email="admin@example.com",
            role=UserRole.ADMIN,
            auth_method="bearer",
            is_active=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            permissions=ROLE_PERMISSIONS[UserRole.ADMIN],
        )
        store = MagicMock()
        store.create_user.side_effect = HTTPException(status_code=409, detail="dupe")

        with pytest.raises(HTTPException) as exc:
            await auth_router.create_user(
                auth_router.UserCreate(
                    username="newuser",
                    password="password123",
                    email="new@example.com",
                    role=UserRole.VIEWER,
                ),
                current_user=current_user,
                store=store,
            )

        assert exc.value.status_code == 409


class TestAPIKeyExceptionHandling:
    """Tests for API key exception handling (lines 388-392, 408, 423)."""

    def test_create_api_key_internal_error_returns_500(self, admin_token):
        """Test that internal errors in API key creation return 500."""
        mock_store = MagicMock()
        mock_store.create_api_key.side_effect = RuntimeError("Key generation failed")

        app.dependency_overrides[get_db] = get_mock_db
        app.dependency_overrides[get_api_key_store] = lambda: mock_store
        client = TestClient(app)

        try:
            response = client.post(
                "/auth/api-keys",
                json={"name": "test-key", "description": "A test key"},
                headers=auth_header(admin_token),
            )

            assert response.status_code == 500
            assert "creation failed" in response.json()["error"]["message"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_create_api_key_reraises_http_exception(self):
        """Direct route call preserves domain HTTPException from the store."""
        current_user = User(
            id="admin-001",
            username="admin",
            email="admin@example.com",
            role=UserRole.ADMIN,
            auth_method="bearer",
            is_active=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            permissions=ROLE_PERMISSIONS[UserRole.ADMIN],
        )
        store = MagicMock()
        store.create_api_key.side_effect = HTTPException(status_code=409, detail="dupe")

        from src.auth.models import APIKeyCreate

        with pytest.raises(HTTPException) as exc:
            await auth_router.create_api_key(
                APIKeyCreate(name="duplicate", description=None),
                current_user=current_user,
                store=store,
            )

        assert exc.value.status_code == 409

    def test_list_api_keys_returns_list(self, admin_token):
        """Test that list API keys returns a list."""
        mock_store = MagicMock()
        mock_store.list_api_keys.return_value = []

        app.dependency_overrides[get_db] = get_mock_db
        app.dependency_overrides[get_api_key_store] = lambda: mock_store
        client = TestClient(app)

        try:
            response = client.get(
                "/auth/api-keys",
                headers=auth_header(admin_token),
            )

            assert response.status_code == 200
            assert isinstance(response.json(), list)
        finally:
            app.dependency_overrides.clear()

    def test_revoke_api_key_not_found_returns_404(self, admin_token):
        """Test that revoking non-existent API key returns 404."""
        mock_store = MagicMock()
        mock_store.revoke_api_key.return_value = False  # Not found

        app.dependency_overrides[get_db] = get_mock_db
        app.dependency_overrides[get_api_key_store] = lambda: mock_store
        client = TestClient(app)

        try:
            response = client.delete(
                "/auth/api-keys/nonexistent-key-id",
                headers=auth_header(admin_token),
            )

            assert response.status_code == 404
            assert "not found" in response.json()["error"]["message"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_revoke_api_key_success(self, admin_token):
        """Test successful API key revocation."""
        mock_store = MagicMock()
        mock_store.revoke_api_key.return_value = True  # Success

        app.dependency_overrides[get_db] = get_mock_db
        app.dependency_overrides[get_api_key_store] = lambda: mock_store
        client = TestClient(app)

        try:
            response = client.delete(
                "/auth/api-keys/existing-key-id",
                headers=auth_header(admin_token),
            )

            assert response.status_code == 200
            assert "revoked" in response.json()["message"].lower()
        finally:
            app.dependency_overrides.clear()
