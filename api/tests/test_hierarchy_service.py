"""Tests for hierarchy service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from src.services.hierarchy_service import HierarchyService


@dataclass
class _HierarchyConfig:
    id: str
    company_id: str
    hierarchy_type: str
    name: str
    description: str
    is_active: bool
    created_at: datetime | None


@patch("src.services.hierarchy_service.HierarchyRepository")
def test_hierarchy_service_maps_repository_models(mock_repo_cls):
    db = Mock()
    repo = Mock()
    mock_repo_cls.return_value = repo

    repo.get_hierarchy_configurations.return_value = [
        _HierarchyConfig(
            id="1",
            company_id="c1",
            hierarchy_type="organizational",
            name="Org",
            description="d",
            is_active=True,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        _HierarchyConfig(
            id="2",
            company_id="c1",
            hierarchy_type="organizational",
            name="Org2",
            description="d2",
            is_active=False,
            created_at=None,
        ),
    ]

    svc = HierarchyService(db)
    result = svc.get_hierarchy_configurations(
        company_id="c1", hierarchy_type="organizational"
    )

    repo.get_hierarchy_configurations.assert_called_once_with(
        company_id="c1", hierarchy_type="organizational"
    )
    assert result[0]["created_at"] == "2026-01-01T00:00:00+00:00"
    assert result[1]["created_at"] is None
