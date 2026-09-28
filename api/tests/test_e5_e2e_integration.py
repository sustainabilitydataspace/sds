"""End-to-end integration tests for E5 (E1/E2/E6 integration).

These tests verify the full stack: API endpoints with policy enforcement,
repository access, and store fallback behavior.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.jwt_handler import jwt_handler
from src.auth.models import ROLE_PERMISSIONS, Permission, User, UserRole
from src.database.session import get_db
from src.services.canonical_data import CanonicalDataUnavailableError

# =============================================================================
# Fixtures
# =============================================================================


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
    # Override the get_db dependency
    app.dependency_overrides[get_db] = get_mock_db
    client = TestClient(app)
    yield client
    # Clear overrides after test
    app.dependency_overrides.clear()


@pytest.fixture
def admin_token():
    """Create admin JWT token."""
    return jwt_handler.create_access_token(
        user_id="admin-001",
        username="admin",
        role=UserRole.ADMIN,
        company_id="company-1",
    )


@pytest.fixture
def viewer_token():
    """Create viewer JWT token (has read permissions from role)."""
    return jwt_handler.create_access_token(
        user_id="viewer-001",
        username="viewer",
        role=UserRole.VIEWER,
        company_id="company-1",
    )


@pytest.fixture
def analyst_token():
    """Create analyst JWT token."""
    return jwt_handler.create_access_token(
        user_id="analyst-001",
        username="analyst",
        role=UserRole.ANALYST,
        company_id="company-1",
    )


@pytest.fixture
def data_manager_token():
    """Create data manager JWT token."""
    return jwt_handler.create_access_token(
        user_id="dm-001",
        username="data_manager",
        role=UserRole.DATA_MANAGER,
        company_id="company-1",
    )


# =============================================================================
# E1 Indicator API Integration Tests
# =============================================================================


class TestIndicatorAPIIntegration:
    """Integration tests for E1 Indicator API endpoints."""

    def test_list_indicators_requires_auth(self, test_client):
        """Test that /api/v1/indicators requires authentication."""
        response = test_client.get("/api/v1/indicators")
        assert response.status_code in (401, 403)

    def test_list_indicators_with_valid_token(self, test_client, viewer_token):
        """Test listing indicators with valid token and permissions."""
        response = test_client.get(
            "/api/v1/indicators",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )

        # Should work since viewer has READ_INDICATORS permission
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert data["items"] == []
        assert data["total"] == 0

    def test_search_indicators_with_filters(self, test_client, admin_token):
        """Test indicator search with dimension/ESRS/GRI filters."""
        response = test_client.get(
            "/api/v1/indicators/search?dimension=E&esrs=E1",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200

    def test_get_indicator_by_id_not_found(self, test_client, viewer_token):
        """Test getting a non-existent indicator by ID."""
        response = test_client.get(
            "/api/v1/indicators/nonexistent",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )

        # Should return 404 when not found
        assert response.status_code == 404


# =============================================================================
# E2 Standard Mapping API Integration Tests
# =============================================================================


class TestMappingsAPIIntegration:
    """Integration tests for E2 standard mapping API endpoints."""

    def test_list_mappings_requires_auth(self, test_client):
        """Test that /api/v1/mappings requires authentication."""
        response = test_client.get("/api/v1/mappings")
        assert response.status_code in (401, 403)

    def test_list_mappings_with_valid_token(self, test_client, viewer_token):
        """Test listing mappings with valid token and permissions."""
        response = test_client.get(
            "/api/v1/mappings",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_search_mappings_by_source(self, test_client, admin_token):
        """Test mapping search by source standard/code."""
        response = test_client.get(
            "/api/v1/mappings/search?source_standard=ESRS&source_code=E1&dimension=E",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200


# =============================================================================
# E6 Policy Integration Tests
# =============================================================================


class TestPolicyIntegration:
    """Integration tests for E6 policy enforcement across API."""

    def test_admin_has_full_access(self, test_client, admin_token):
        """Test that admin role has access to all indicator/mapping endpoints."""
        # Indicators
        response = test_client.get(
            "/api/v1/indicators",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

        # Mappings
        response = test_client.get(
            "/api/v1/mappings",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

    def test_viewer_has_read_access(self, test_client, viewer_token):
        """Test that viewer role has read access."""
        # Read operations should succeed
        response = test_client.get(
            "/api/v1/indicators",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 200

        response = test_client.get(
            "/api/v1/mappings",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 200

    def test_all_standard_roles_have_read_access(
        self, test_client, viewer_token, analyst_token, data_manager_token, admin_token
    ):
        """Test that all standard roles have read access to indicators and mappings."""
        tokens = [viewer_token, analyst_token, data_manager_token, admin_token]

        for token in tokens:
            # Indicators
            response = test_client.get(
                "/api/v1/indicators",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200

            # Mappings
            response = test_client.get(
                "/api/v1/mappings",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200


# =============================================================================
# Store Fallback Integration Tests
# =============================================================================


class TestStoreFallbackIntegration:
    """Integration tests for DB-primary stores."""

    def test_indicator_store_fallback(self):
        """Test indicator store falls back to JSON on DB failure."""
        from src.services.indicator_store import IndicatorData, IndicatorStore

        # Create store with failing DB
        mock_db = MagicMock()
        mock_db.query.side_effect = Exception("DB connection failed")

        store = IndicatorStore(db=mock_db)

        # Inject fallback data
        store._fallback._loaded = True
        store._fallback._indicators = {
            "test-1": IndicatorData(
                {
                    "identifier": "test-1",
                    "title": "Test Indicator",
                    "dimension": "E",
                }
            )
        }

        # Should use fallback
        result = store.get_all(limit=10)
        assert len(result) == 1
        assert result[0].identifier == "test-1"

    def test_mapping_store_no_legacy_fallback(self):
        """Canonical DB failure cannot masquerade as absence or serve legacy rows."""
        from src.services.standard_mapping_store import StandardMappingStore

        mock_db = MagicMock()
        mock_db.query.side_effect = Exception("DB connection failed")
        store = StandardMappingStore(db=mock_db)
        with pytest.raises(CanonicalDataUnavailableError):
            store.get_all(limit=10)


# =============================================================================
# Response Structure Tests
# =============================================================================


class TestResponseStructure:
    """Tests verifying API response structure."""

    def test_indicator_list_response_structure(self, test_client, viewer_token):
        """Test that indicator list response has correct structure."""
        response = test_client.get(
            "/api/v1/indicators?limit=10&offset=0",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )

        assert response.status_code == 200
        data = response.json()

        # Check required fields
        assert "items" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)
        assert data["limit"] == 10
        assert data["offset"] == 0

    def test_mapping_list_response_structure(self, test_client, viewer_token):
        """Test that mapping list response has correct structure."""
        response = test_client.get(
            "/api/v1/mappings?limit=50&offset=100",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )

        assert response.status_code == 200
        data = response.json()

        # Check required fields
        assert "items" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)
        assert data["limit"] == 50
        assert data["offset"] == 100

    def test_indicator_search_response_structure(self, test_client, admin_token):
        """Test that indicator search response has correct structure."""
        response = test_client.get(
            "/api/v1/indicators/search?dimension=E",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()

        # Check required fields
        assert "items" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data

    def test_mapping_search_response_structure(self, test_client, admin_token):
        """Test that mapping search response has correct structure."""
        response = test_client.get(
            "/api/v1/mappings/search?source_standard=ESRS&source_code=E1",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()

        # Check required fields
        assert "items" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for API error handling."""

    def test_invalid_token(self, test_client):
        """Test that invalid token returns 401/403."""
        response = test_client.get(
            "/api/v1/indicators",
            headers={"Authorization": "Bearer invalid_token"},
        )
        assert response.status_code in (401, 403)

    def test_expired_token(self, test_client):
        """Test that expired token returns 401/403."""
        # Create expired token by manipulating exp
        from datetime import timedelta

        expired_token = jwt_handler.create_access_token(
            user_id="test-001",
            username="test",
            role=UserRole.VIEWER,
            company_id="company-1",
            expires_delta=timedelta(seconds=-10),  # Already expired
        )

        response = test_client.get(
            "/api/v1/indicators",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert response.status_code in (401, 403)

    def test_missing_authorization_header(self, test_client):
        """Test that missing auth header returns 401/403."""
        response = test_client.get("/api/v1/indicators")
        assert response.status_code in (401, 403)

    def test_invalid_pagination_params(self, test_client, viewer_token):
        """Test that invalid pagination params are handled."""
        # Limit too high
        response = test_client.get(
            "/api/v1/indicators?limit=10000",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        # FastAPI validation should reject this
        assert response.status_code == 422

        # Negative offset
        response = test_client.get(
            "/api/v1/indicators?offset=-1",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 422
