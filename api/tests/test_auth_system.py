"""
Tests for authentication and authorization system.
"""

from datetime import datetime, timedelta

import pytest

from src.auth.jwt_handler import jwt_handler
from src.auth.models import Permission, UserRole
from src.auth.rbac import rbac


class TestAuthentication:
    """Test authentication functionality."""

    def test_login_success(self, client):
        """Test successful login."""
        login_data = {"username": "admin", "password": "admin123"}

        response = client.post("/auth/login", json=login_data)
        assert response.status_code == 200

        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

        # Verify token is valid
        token_data = jwt_handler.verify_token(data["access_token"])
        assert token_data is not None
        assert token_data.username == "admin"
        assert token_data.role == UserRole.ADMIN

    def test_login_invalid_credentials(self, client):
        """Test login with invalid credentials."""
        login_data = {"username": "admin", "password": "wrong_password"}

        response = client.post("/auth/login", json=login_data)
        assert response.status_code == 401
        response_data = response.json()
        # Check both possible response formats
        if "detail" in response_data:
            assert "Invalid username or password" in response_data["detail"]
        elif "error" in response_data:
            assert "Invalid username or password" in response_data["error"]["message"]
        else:
            assert False, f"Unexpected response format: {response_data}"

    def test_get_current_user(self, client):
        """Test getting current user info."""
        # Login first
        login_data = {"username": "admin", "password": "admin123"}
        login_response = client.post("/auth/login", json=login_data)
        token = login_response.json()["access_token"]

        # Get user info
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get("/auth/me", headers=headers)

        assert response.status_code == 200
        user_data = response.json()
        assert user_data["username"] == "admin"
        assert user_data["role"] == UserRole.ADMIN.value

    def test_unauthorized_access(self, client):
        """Test accessing protected endpoint without token."""
        response = client.get("/auth/me")
        # Accept both 401 (Unauthorized) and 403 (Forbidden) as valid responses
        assert response.status_code in [401, 403]


class TestJWTHandler:
    """Test JWT token handling."""

    def test_create_access_token(self):
        """Test access token creation."""
        token = jwt_handler.create_access_token(
            user_id="test_user",
            username="testuser",
            role=UserRole.ANALYST,
            company_id="test_company",
        )

        assert isinstance(token, str)
        assert len(token) > 0

        # Verify token content
        token_data = jwt_handler.verify_token(token)
        assert token_data.user_id == "test_user"
        assert token_data.username == "testuser"
        assert token_data.role == UserRole.ANALYST
        assert token_data.company_id == "test_company"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
