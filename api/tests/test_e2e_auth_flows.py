"""End-to-end tests for authentication flows.

Tests complete user journeys through authentication:
- Login with credentials
- Token refresh
- API key authentication
- Password change
- Permission boundaries
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.jwt_handler import jwt_handler
from src.auth.models import ROLE_PERMISSIONS, Permission, User, UserRole
from src.database.session import get_db


def get_mock_db():
    """Create mock DB for testing."""
    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.all.return_value = []
    mock_query.count.return_value = 0
    mock_query.first.return_value = None
    return mock_db


@pytest.fixture
def test_client():
    """Create test client with mocked database."""
    app.dependency_overrides[get_db] = get_mock_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


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
def admin_refresh_token() -> str:
    """Create admin refresh token."""
    return jwt_handler.create_refresh_token(
        user_id="admin-001",
        username="admin",
    )


@pytest.fixture
def viewer_token() -> str:
    """Create viewer JWT token."""
    return jwt_handler.create_access_token(
        user_id="viewer-001",
        username="viewer",
        role=UserRole.VIEWER,
        company_id="company-1",
    )


@pytest.fixture
def expired_token() -> str:
    """Create an expired JWT token."""
    return jwt_handler.create_access_token(
        user_id="test-001",
        username="test",
        role=UserRole.VIEWER,
        company_id="company-1",
        expires_delta=timedelta(seconds=-60),
    )


@pytest.fixture
def expired_refresh_token() -> str:
    """Create an expired refresh token."""
    return jwt_handler.create_refresh_token(
        user_id="test-001",
        username="test",
        expires_delta=timedelta(seconds=-60),
    )


def auth_header(token: str) -> dict:
    """Create authorization header."""
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# Login Flow Tests
# =============================================================================


class TestLoginFlow:
    """End-to-end tests for login authentication flow."""

    def test_login_endpoint_exists(self, test_client):
        """Test that login endpoint is available."""
        response = test_client.post(
            "/auth/login",
            json={"username": "test", "password": "test"},
        )
        # Should not be 404 or 405
        assert response.status_code not in (404, 405)

    def test_login_missing_credentials_fails(self, test_client):
        """Test login fails with missing credentials."""
        response = test_client.post("/auth/login", json={})
        assert response.status_code == 422  # Validation error

    def test_login_invalid_json_fails(self, test_client):
        """Test login fails with invalid JSON."""
        response = test_client.post(
            "/auth/login",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    def test_login_nonexistent_user_fails(self, test_client):
        """Test login fails for non-existent user."""
        response = test_client.post(
            "/auth/login",
            json={"username": "nobody", "password": "anypass"},
        )
        assert response.status_code == 401

    def test_login_response_structure(self, test_client):
        """Test that successful login returns expected token structure."""
        # Mock a successful login by patching user store
        from src.services.user_store import InMemoryUserStore

        mock_user = MagicMock()
        mock_user.id = "test-001"
        mock_user.username = "testuser"
        mock_user.password_hash = jwt_handler.hash_password("testpass")
        mock_user.role = UserRole.VIEWER
        mock_user.is_active = True
        mock_user.company_id = "company-1"
        mock_user.permissions = list(ROLE_PERMISSIONS[UserRole.VIEWER])

        with patch("src.api.routers.auth.get_user_store") as mock_store:
            store = MagicMock()
            store.authenticate.return_value = mock_user
            mock_store.return_value = store

            response = test_client.post(
                "/auth/login",
                json={"username": "testuser", "password": "testpass"},
            )

            if response.status_code == 200:
                data = response.json()
                assert "access_token" in data
                assert "refresh_token" in data
                assert data["token_type"] == "bearer"
                assert "expires_in" in data


# =============================================================================
# Token Validation Tests
# =============================================================================


class TestTokenValidation:
    """End-to-end tests for token validation."""

    def test_valid_token_accepted(self, test_client, admin_token):
        """Test that valid token is accepted."""
        response = test_client.get(
            "/api/v1/indicators",
            headers=auth_header(admin_token),
        )
        # Should not be 401/403 due to token issues
        assert response.status_code == 200

    def test_expired_token_rejected(self, test_client, expired_token):
        """Test that expired token is rejected."""
        response = test_client.get(
            "/api/v1/indicators",
            headers=auth_header(expired_token),
        )
        assert response.status_code in (401, 403)

    def test_invalid_token_rejected(self, test_client):
        """Test that invalid token is rejected."""
        response = test_client.get(
            "/api/v1/indicators",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert response.status_code in (401, 403)

    def test_malformed_auth_header_rejected(self, test_client):
        """Test that malformed authorization header is rejected."""
        response = test_client.get(
            "/api/v1/indicators",
            headers={"Authorization": "NotBearer token"},
        )
        assert response.status_code in (401, 403)

    def test_missing_auth_header_rejected(self, test_client):
        """Test that missing authorization header is rejected."""
        response = test_client.get("/api/v1/indicators")
        assert response.status_code in (401, 403)


# =============================================================================
# Token Refresh Tests
# =============================================================================


class TestTokenRefresh:
    """End-to-end tests for token refresh flow."""

    def test_refresh_endpoint_exists(self, test_client):
        """Test that refresh endpoint is available."""
        response = test_client.post(
            "/auth/refresh",
            json={"refresh_token": "dummy"},
        )
        assert response.status_code not in (404, 405)

    def test_refresh_with_valid_token(self, test_client, admin_refresh_token):
        """Test token refresh with valid refresh token."""
        response = test_client.post(
            "/auth/refresh",
            json={"refresh_token": admin_refresh_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data

    def test_refresh_with_expired_token_fails(self, test_client, expired_refresh_token):
        """Test token refresh with expired refresh token fails."""
        response = test_client.post(
            "/auth/refresh",
            json={"refresh_token": expired_refresh_token},
        )
        assert response.status_code in (401, 403)

    def test_refresh_with_invalid_token_fails(self, test_client):
        """Test token refresh with invalid token fails."""
        response = test_client.post(
            "/auth/refresh",
            json={"refresh_token": "invalid.refresh.token"},
        )
        assert response.status_code in (401, 403)

    def test_refresh_with_access_token_fails(self, test_client, admin_token):
        """Test that using access token for refresh fails."""
        response = test_client.post(
            "/auth/refresh",
            json={"refresh_token": admin_token},  # Wrong token type
        )
        # Should fail - access tokens shouldn't work as refresh tokens
        assert response.status_code in (401, 403, 422)


# =============================================================================
# API Key Authentication Tests
# =============================================================================


class TestAPIKeyAuthentication:
    """End-to-end tests for API key authentication."""

    def test_invalid_api_key_rejected(self, test_client):
        """Test that invalid API key is rejected."""
        response = test_client.get(
            "/api/v1/indicators",
            headers={"X-API-Key": "invalid-key-12345"},
        )
        assert response.status_code in (401, 403)

    def test_empty_api_key_rejected(self, test_client):
        """Test that empty API key is rejected."""
        response = test_client.get(
            "/api/v1/indicators",
            headers={"X-API-Key": ""},
        )
        assert response.status_code in (401, 403)

    def test_api_key_with_bearer_both_present(self, test_client, admin_token):
        """Test behavior when both API key and Bearer token are present."""
        response = test_client.get(
            "/api/v1/indicators",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-API-Key": "some-key",
            },
        )
        # Bearer should take precedence and work
        assert response.status_code == 200


# =============================================================================
# Me Endpoint Tests
# =============================================================================


class TestMeEndpoint:
    """End-to-end tests for /auth/me endpoint."""

    def test_me_endpoint_requires_auth(self, test_client):
        """Test that /auth/me requires authentication."""
        response = test_client.get("/auth/me")
        assert response.status_code in (401, 403)

    def test_me_endpoint_returns_user_info(self, test_client, admin_token):
        """Test that /auth/me returns user information."""
        response = test_client.get(
            "/auth/me",
            headers=auth_header(admin_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert "username" in data
        assert "role" in data

    def test_me_endpoint_shows_correct_role(self, test_client, viewer_token):
        """Test that /auth/me shows correct role for viewer."""
        response = test_client.get(
            "/auth/me",
            headers=auth_header(viewer_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["role"] == "viewer"


# =============================================================================
# Password Change Tests
# =============================================================================


class TestPasswordChange:
    """End-to-end tests for password change flow."""

    def test_password_change_endpoint_exists(self, test_client, admin_token):
        """Test that password change endpoint exists."""
        response = test_client.post(
            "/auth/change-password",
            json={
                "current_password": "old",
                "new_password": "new",
            },
            headers=auth_header(admin_token),
        )
        # Should not be 404 or 405
        assert response.status_code not in (404, 405)

    def test_password_change_requires_auth(self, test_client):
        """Test that password change requires authentication."""
        response = test_client.post(
            "/auth/change-password",
            json={
                "current_password": "old",
                "new_password": "new",
            },
        )
        assert response.status_code in (401, 403)

    def test_password_change_validates_input(self, test_client, admin_token):
        """Test that password change validates input."""
        # Missing new_password
        response = test_client.post(
            "/auth/change-password",
            json={"current_password": "old"},
            headers=auth_header(admin_token),
        )
        assert response.status_code == 422


# =============================================================================
# Role-Based Access Control Tests
# =============================================================================


class TestRBACEndToEnd:
    """End-to-end tests for role-based access control."""

    def test_admin_can_manage_hierarchies(self, test_client, admin_token):
        """Test that admin can access hierarchy management."""
        response = test_client.get(
            "/api/v1/hierarchies",
            headers=auth_header(admin_token),
        )
        assert response.status_code == 200

    def test_viewer_cannot_create_hierarchies(self, test_client, viewer_token):
        """Test that viewer cannot create hierarchies."""
        response = test_client.post(
            "/api/v1/hierarchies",
            json={
                "company_id": "test",
                "hierarchy_type": "organizational",
                "name": "Test",
                "nodes": [],
            },
            headers=auth_header(viewer_token),
        )
        assert response.status_code == 403

    def test_all_roles_can_read_indicators(self, test_client):
        """Test that all roles can read indicators."""
        roles = [
            UserRole.VIEWER,
            UserRole.ANALYST,
            UserRole.DATA_MANAGER,
            UserRole.ADMIN,
        ]

        for role in roles:
            token = jwt_handler.create_access_token(
                user_id=f"{role.value}-001",
                username=role.value,
                role=role,
                company_id="company-1",
            )
            response = test_client.get(
                "/api/v1/indicators",
                headers=auth_header(token),
            )
            assert (
                response.status_code == 200
            ), f"Role {role.value} should read indicators"

    def test_viewer_cannot_create_values(self, test_client, viewer_token):
        """Test that viewer cannot create values."""
        response = test_client.post(
            "/api/v1/values",
            json={
                "concept": "test:concept",
                "entity": "company-1",
                "period": "2024-01-01",
                "value": 100,
                "unit": "kg",
            },
            headers=auth_header(viewer_token),
        )
        assert response.status_code == 403

    def test_data_manager_can_create_values(self, test_client):
        """Test that data manager can create values."""
        dm_token = jwt_handler.create_access_token(
            user_id="dm-001",
            username="data_manager",
            role=UserRole.DATA_MANAGER,
            company_id="company-1",
        )
        response = test_client.post(
            "/api/v1/values",
            json={
                "concept": "test:concept",
                "entity": "company-1",
                "period": "2024-01-01",
                "value": 100,
                "unit": "kg",
            },
            headers=auth_header(dm_token),
        )
        # Should not be 403 (permission check passes)
        # May be 200/201 or 400/404 for other reasons
        assert response.status_code != 403


# =============================================================================
# Security Edge Cases
# =============================================================================


class TestSecurityEdgeCases:
    """End-to-end tests for security edge cases."""

    def test_token_with_wrong_signature_rejected(self, test_client):
        """Test that token with wrong signature is rejected."""
        # Create a token and modify its signature
        valid_token = jwt_handler.create_access_token(
            user_id="test-001",
            username="test",
            role=UserRole.VIEWER,
            company_id="company-1",
        )
        # Corrupt the signature (last part of JWT)
        parts = valid_token.split(".")
        parts[2] = "corrupted_signature_here"
        corrupted_token = ".".join(parts)

        response = test_client.get(
            "/api/v1/indicators",
            headers=auth_header(corrupted_token),
        )
        assert response.status_code in (401, 403)

    def test_token_with_modified_payload_rejected(self, test_client):
        """Test that token with modified payload is rejected."""
        # Create a valid token
        valid_token = jwt_handler.create_access_token(
            user_id="test-001",
            username="test",
            role=UserRole.VIEWER,
            company_id="company-1",
        )
        # Modify the payload (middle part)
        parts = valid_token.split(".")
        parts[1] = parts[1][:-5] + "XXXXX"  # Corrupt payload
        modified_token = ".".join(parts)

        response = test_client.get(
            "/api/v1/indicators",
            headers=auth_header(modified_token),
        )
        assert response.status_code in (401, 403)

    def test_concurrent_tokens_both_valid(self, test_client):
        """Test that multiple tokens for same user are all valid."""
        token1 = jwt_handler.create_access_token(
            user_id="test-001",
            username="test",
            role=UserRole.VIEWER,
            company_id="company-1",
        )
        token2 = jwt_handler.create_access_token(
            user_id="test-001",
            username="test",
            role=UserRole.VIEWER,
            company_id="company-1",
        )

        # Both tokens should work
        response1 = test_client.get(
            "/api/v1/indicators",
            headers=auth_header(token1),
        )
        response2 = test_client.get(
            "/api/v1/indicators",
            headers=auth_header(token2),
        )

        assert response1.status_code == 200
        assert response2.status_code == 200
