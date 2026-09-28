"""Tests for mappings router to achieve full coverage."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routers import mappings as mappings_router
from src.auth.jwt_handler import jwt_handler
from src.auth.models import UserRole
from src.config.settings import settings
from src.database.session import get_db
from src.services.change_feed import encode_change_feed_cursor


def get_mock_db():
    """Create mock DB for testing."""
    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.distinct.return_value = mock_query
    mock_query.count.return_value = 0
    mock_query.all.return_value = []
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


def auth_header(token: str) -> dict:
    """Create authorization header."""
    return {"Authorization": f"Bearer {token}"}


class TestMappingsEndpoints:
    """Tests for mappings router endpoints."""

    def test_get_mappings_returns_list(self, test_client, admin_token):
        """Test that GET /mappings returns a list."""
        response = test_client.get(
            "/api/v1/mappings",
            headers=auth_header(admin_token),
        )
        assert response.status_code == 200
        assert isinstance(response.json(), dict)

    def test_get_mappings_from(self, test_client, admin_token):
        """Test that GET /mappings/from/{standard}/{code} works."""
        response = test_client.get(
            "/api/v1/mappings/from/ESRS/E1",
            headers=auth_header(admin_token),
        )
        assert response.status_code in (200, 404)

    def test_get_mappings_to(self, test_client, admin_token):
        """Test that GET /mappings/to/{standard}/{code} works."""
        response = test_client.get(
            "/api/v1/mappings/to/GRI/305",
            headers=auth_header(admin_token),
        )
        assert response.status_code in (200, 404)

    def test_search_mappings(self, test_client, admin_token):
        """Test searching mappings."""
        response = test_client.get(
            "/api/v1/mappings/search?source_standard=ESRS&source_code=E1",
            headers=auth_header(admin_token),
        )
        assert response.status_code == 200

    def test_mappings_between(self, test_client, admin_token):
        """Test GET /mappings/between/{source}/{target} works."""
        response = test_client.get(
            "/api/v1/mappings/between/ESRS/GRI",
            headers=auth_header(admin_token),
        )
        assert response.status_code == 200

    def test_list_supported_standards(self, test_client, admin_token):
        """Test GET /mappings/standards."""
        response = test_client.get(
            "/api/v1/mappings/standards",
            headers=auth_header(admin_token),
        )
        assert response.status_code == 200


class TestMappingsAuthorization:
    """Tests for mappings authorization."""

    def test_viewer_can_read_mappings(self, test_client):
        """Test that viewer can read mappings."""
        viewer_token = jwt_handler.create_access_token(
            user_id="viewer-001",
            username="viewer_user",
            role=UserRole.VIEWER,
            company_id="company-1",
        )

        response = test_client.get(
            "/api/v1/mappings",
            headers=auth_header(viewer_token),
        )
        assert response.status_code == 200

    def test_unauthenticated_cannot_access_mappings(self, test_client):
        """Test that unauthenticated requests are rejected."""
        response = test_client.get("/api/v1/mappings")
        assert response.status_code in (401, 403)


def test_mapping_cursor_decoder_rejects_invalid_and_wrong_dataset():
    with pytest.raises(HTTPException) as invalid:
        mappings_router._decode_mapping_changes_cursor("not-a-cursor")
    assert invalid.value.status_code == 400

    values_cursor = encode_change_feed_cursor(
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        dataset="values",
        event_id="event-1",
    )
    with pytest.raises(HTTPException) as wrong_dataset:
        mappings_router._decode_mapping_changes_cursor(values_cursor)
    assert wrong_dataset.value.status_code == 400
    assert "does not belong to mappings" in wrong_dataset.value.detail


def test_stream_mapping_json_separates_multiple_items():
    items = [
        mappings_router.MappingResponse(
            id=1,
            source_standard="ESRS",
            source_code="E1",
            target_standard="GRI",
            target_code="305",
        ),
        mappings_router.MappingResponse(
            id=2,
            source_standard="GRI",
            source_code="305",
            target_standard="ESRS",
            target_code="E1",
        ),
    ]

    streamed = "".join(mappings_router._stream_mapping_json(items))

    assert streamed.startswith("[")
    assert streamed.endswith("]")
    assert "},{" in streamed


@pytest.mark.asyncio
async def test_mapping_router_direct_404_and_error_branches(monkeypatch):
    class _EmptyStore:
        def __init__(self, db=None):
            self.db = db

        def find_by_source(self, *_args, **_kwargs):
            return []

        def find_by_target(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(mappings_router, "StandardMappingStore", _EmptyStore)

    with pytest.raises(HTTPException) as from_missing:
        await mappings_router.get_mappings_from(
            standard="ESRS",
            code="E1",
            limit=10,
            db=object(),
            user=object(),
        )
    assert from_missing.value.status_code == 404

    with pytest.raises(HTTPException) as to_missing:
        await mappings_router.get_mappings_to(
            standard="GRI",
            code="305",
            limit=10,
            db=object(),
            user=object(),
        )
    assert to_missing.value.status_code == 404

    class _FailingStore(_EmptyStore):
        def search(self, *_args, **_kwargs):
            raise RuntimeError("catalog down")

        def get_all(self, *_args, **_kwargs):
            raise RuntimeError("catalog down")

        def find_by_source(self, *_args, **_kwargs):
            raise RuntimeError("catalog down")

        def find_by_target(self, *_args, **_kwargs):
            raise RuntimeError("catalog down")

    monkeypatch.setattr(mappings_router, "StandardMappingStore", _FailingStore)
    monkeypatch.setattr(settings, "require_database", True)

    with pytest.raises(HTTPException) as search_error:
        await mappings_router.search_mappings(
            source_standard="ESRS",
            source_code=None,
            target_standard=None,
            target_code=None,
            dimension=None,
            min_confidence=None,
            changed_since=None,
            limit=10,
            db=object(),
            user=object(),
        )
    assert search_error.value.status_code == 503

    with pytest.raises(HTTPException) as manifest_error:
        await mappings_router.mapping_manifest(db=object(), user=object())
    assert manifest_error.value.status_code == 503

    with pytest.raises(HTTPException) as from_error:
        await mappings_router.get_mappings_from(
            standard="ESRS",
            code="E1",
            limit=10,
            db=object(),
            user=object(),
        )
    assert from_error.value.status_code == 503

    with pytest.raises(HTTPException) as to_error:
        await mappings_router.get_mappings_to(
            standard="GRI",
            code="305",
            limit=10,
            db=object(),
            user=object(),
        )
    assert to_error.value.status_code == 503


@pytest.mark.asyncio
async def test_mapping_diff_wraps_current_catalog_errors(monkeypatch):
    baseline = MagicMock(
        id=1,
        dataset="mappings",
        manifest_hash="old",
        item_index={},
    )

    class _Repo:
        def __init__(self, _db):
            pass

        def get_by_id(self, _snapshot_id):
            return baseline

    class _FailingStore:
        def __init__(self, db=None):
            self.db = db

        def get_all(self, *_args, **_kwargs):
            raise RuntimeError("catalog down")

    monkeypatch.setattr(mappings_router, "DatasetSnapshotRepository", _Repo)
    monkeypatch.setattr(mappings_router, "StandardMappingStore", _FailingStore)
    monkeypatch.setattr(settings, "require_database", True)

    with pytest.raises(HTTPException) as exc:
        await mappings_router.mapping_diff(
            from_snapshot_id=1,
            to_snapshot_id=None,
            db=object(),
            user=object(),
        )

    assert exc.value.status_code == 503
