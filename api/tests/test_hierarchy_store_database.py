"""DatabaseHierarchyStore unit tests (repository mocked)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.models import HierarchyConfiguration, HierarchyLevel
from src.config.settings import settings
from src.services.hierarchy_store import (
    DatabaseHierarchyStore,
    InMemoryHierarchyStore,
    _config_to_json,
    _db_to_response,
    get_hierarchy_store,
)


def _make_config(*, active: bool = True) -> HierarchyConfiguration:
    return HierarchyConfiguration(
        company_id="c1",
        hierarchy_type="organizational",
        name="Org",
        description="d",
        levels=[
            HierarchyLevel(
                id="l1",
                name="Plant",
                parent=None,
                level=1,
                metadata={"k": "v"},
            )
        ],
        active=active,
    )


def _make_record(*, config_id: str = "cfg1", active: bool = True):
    cfg = _make_config(active=active)
    payload = _config_to_json(cfg, config_id=config_id)

    record = MagicMock()
    record.id = config_id
    record.company_id = cfg.company_id
    record.hierarchy_type = cfg.hierarchy_type
    record.name = cfg.name
    record.description = cfg.description
    record.configuration = payload
    record.is_active = active
    return record


def test_config_to_json_injects_id():
    cfg = _make_config()
    out = _config_to_json(cfg, config_id="cfg1")
    assert '"id": "cfg1"' in out


def test_db_to_response_parses_levels():
    record = _make_record(config_id="cfg1", active=True)
    out = _db_to_response(record)
    assert out.id == "cfg1"
    assert len(out.levels) == 1
    assert out.levels[0].id == "l1"


def test_db_to_response_handles_invalid_json():
    record = MagicMock()
    record.id = "x"
    record.company_id = "c1"
    record.hierarchy_type = "organizational"
    record.name = "n"
    record.description = None
    record.configuration = "not-json"
    record.is_active = True

    with pytest.raises(ValidationError):
        _db_to_response(record)


def test_database_hierarchy_store_crud_smoke():
    db = MagicMock()
    store = DatabaseHierarchyStore(db)
    store._repo = MagicMock()

    record = _make_record(config_id="cfg1", active=True)
    cfg = _make_config(active=True)

    store._repo.create_hierarchy_configuration.return_value = record
    created = store.create(cfg, config_id="cfg1", created_by="u")
    assert created.id == "cfg1"

    store._repo.get_hierarchy_configurations.return_value = [record]
    items, total = store.list(
        company_id="c1",
        hierarchy_type="organizational",
        active=None,
        limit=100,
        offset=0,
    )
    assert total == 1
    assert items[0].id == "cfg1"

    store._repo.get_hierarchy_configuration_by_id.return_value = record
    got = store.get("cfg1")
    assert got is not None
    assert got.id == "cfg1"

    store._repo.update_hierarchy_configuration.return_value = record
    updated = store.update("cfg1", cfg)
    assert updated is not None
    assert updated.id == "cfg1"

    store._repo.delete_hierarchy_configuration.return_value = True
    assert store.delete("cfg1") is True

    store._repo.activate_hierarchy_configuration.return_value = True
    assert store.activate("cfg1") is True
    store._repo.activate_hierarchy_configuration.assert_called()


def test_database_hierarchy_store_create_inactive_marks_inactive():
    db = MagicMock()
    store = DatabaseHierarchyStore(db)
    store._repo = MagicMock()

    record = _make_record(config_id="cfg2", active=True)
    store._repo.create_hierarchy_configuration.return_value = record
    store._repo.update_hierarchy_configuration.return_value = record

    cfg = _make_config(active=False)
    out = store.create(cfg, config_id="cfg2", created_by="u")
    assert out.id == "cfg2"
    store._repo.update_hierarchy_configuration.assert_called_with(
        "cfg2", is_active=False
    )


def test_in_memory_hierarchy_store_filters_update_and_activation_edges():
    store = InMemoryHierarchyStore()
    active = store.create(_make_config(active=True), config_id="cfg-active")
    inactive = store.create(_make_config(active=False), config_id="cfg-inactive")
    other_type = HierarchyConfiguration(
        company_id="c1",
        hierarchy_type="facility",
        name="Facility",
        levels=_make_config().levels,
        active=True,
    )
    store.create(other_type, config_id="cfg-facility")

    items, total = store.list(
        company_id="c1",
        hierarchy_type="facility",
        active=True,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert items[0].id == "cfg-facility"

    inactive_items, inactive_total = store.list(
        company_id="c1",
        hierarchy_type="organizational",
        active=False,
        limit=10,
        offset=0,
    )
    assert inactive_total == 1
    assert inactive_items[0].id == inactive.id

    assert store.update("missing", _make_config()) is None
    assert store.activate("missing") is False

    assert store.activate(inactive.id) is True
    assert store.get(inactive.id).active is True
    assert store.get(active.id).active is False


def test_database_hierarchy_store_inactive_list_and_missing_activate():
    db = MagicMock()
    store = DatabaseHierarchyStore(db)
    store._repo = MagicMock()
    active = _make_record(config_id="active", active=True)
    inactive = _make_record(config_id="inactive", active=False)
    store._repo.get_hierarchy_configurations.return_value = [active, inactive]

    items, total = store.list(
        company_id="c1",
        hierarchy_type="organizational",
        active=False,
        limit=10,
        offset=0,
    )

    assert total == 1
    assert items[0].id == "inactive"

    store._repo.get_hierarchy_configuration_by_id.return_value = None
    assert store.activate("missing") is False


def test_get_hierarchy_store_dependency_database_and_offline_paths(monkeypatch):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    monkeypatch.setattr(settings, "require_database", True)
    with pytest.raises(HTTPException) as exc:
        get_hierarchy_store(request=request, db=None)
    assert exc.value.status_code == 503

    db = MagicMock()
    assert isinstance(
        get_hierarchy_store(request=request, db=db), DatabaseHierarchyStore
    )

    monkeypatch.setattr(settings, "require_database", False)
    first = get_hierarchy_store(request=request, db=None)
    second = get_hierarchy_store(request=request, db=None)
    assert isinstance(first, InMemoryHierarchyStore)
    assert first is second
