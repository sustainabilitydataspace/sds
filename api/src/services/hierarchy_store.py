"""Hierarchy configuration persistence backends (DB or in-memory)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.api.models import HierarchyConfiguration, HierarchyLevel
from src.config.settings import settings
from src.database.repositories.hierarchy_repository import HierarchyRepository
from src.database.session import get_db_optional


@dataclass(frozen=True)
class _StoredHierarchyConfig:
    id: str
    company_id: str
    hierarchy_type: str
    name: str
    description: Optional[str]
    levels: List[HierarchyLevel]
    active: bool
    created_at: datetime
    updated_at: datetime


class InMemoryHierarchyStore:
    """Offline-safe hierarchy store (ephemeral, per-process)."""

    def __init__(self):
        self._configs: Dict[str, _StoredHierarchyConfig] = {}

    def create(
        self,
        config: HierarchyConfiguration,
        *,
        config_id: str,
        created_by: Optional[str] = None,
    ) -> HierarchyConfiguration:
        now = datetime.now(timezone.utc)
        stored = _StoredHierarchyConfig(
            id=config_id,
            company_id=config.company_id,
            hierarchy_type=config.hierarchy_type,
            name=config.name,
            description=config.description,
            levels=config.levels,
            active=config.active,
            created_at=now,
            updated_at=now,
        )
        self._configs[config_id] = stored
        return self._to_response(stored)

    def list(
        self,
        *,
        company_id: Optional[str],
        hierarchy_type: Optional[str],
        active: Optional[bool],
        limit: int,
        offset: int,
    ) -> Tuple[List[HierarchyConfiguration], int]:
        items = list(self._configs.values())
        if company_id:
            items = [c for c in items if c.company_id == company_id]
        if hierarchy_type:
            items = [c for c in items if c.hierarchy_type == hierarchy_type]

        # Default behavior: return active only unless explicitly requested.
        if active is None or active is True:
            items = [c for c in items if c.active]
        else:
            items = [c for c in items if not c.active]

        total = len(items)
        paginated = items[offset : offset + limit]
        return [self._to_response(c) for c in paginated], total

    def get(self, config_id: str) -> Optional[HierarchyConfiguration]:
        stored = self._configs.get(config_id)
        return self._to_response(stored) if stored else None

    def update(
        self, config_id: str, config: HierarchyConfiguration
    ) -> Optional[HierarchyConfiguration]:
        existing = self._configs.get(config_id)
        if not existing:
            return None

        updated = _StoredHierarchyConfig(
            id=config_id,
            company_id=config.company_id,
            hierarchy_type=config.hierarchy_type,
            name=config.name,
            description=config.description,
            levels=config.levels,
            active=config.active,
            created_at=existing.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        self._configs[config_id] = updated
        return self._to_response(updated)

    def delete(self, config_id: str) -> bool:
        existing = self._configs.get(config_id)
        if not existing:
            return False

        # Soft-delete: mark inactive.
        deleted = _StoredHierarchyConfig(
            id=existing.id,
            company_id=existing.company_id,
            hierarchy_type=existing.hierarchy_type,
            name=existing.name,
            description=existing.description,
            levels=existing.levels,
            active=False,
            created_at=existing.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        self._configs[config_id] = deleted
        return True

    def activate(self, config_id: str) -> bool:
        target = self._configs.get(config_id)
        if not target:
            return False

        # Deactivate others (same company/type), activate target.
        for key, cfg in list(self._configs.items()):
            if (
                cfg.company_id == target.company_id
                and cfg.hierarchy_type == target.hierarchy_type
            ):
                is_target = key == config_id
                if cfg.active != is_target:
                    self._configs[key] = _StoredHierarchyConfig(
                        id=cfg.id,
                        company_id=cfg.company_id,
                        hierarchy_type=cfg.hierarchy_type,
                        name=cfg.name,
                        description=cfg.description,
                        levels=cfg.levels,
                        active=is_target,
                        created_at=cfg.created_at,
                        updated_at=datetime.now(timezone.utc),
                    )
        return True

    @staticmethod
    def _to_response(stored: _StoredHierarchyConfig) -> HierarchyConfiguration:
        return HierarchyConfiguration(
            id=stored.id,
            company_id=stored.company_id,
            hierarchy_type=stored.hierarchy_type,
            name=stored.name,
            description=stored.description,
            levels=stored.levels,
            active=stored.active,
        )


class DatabaseHierarchyStore:
    """PostgreSQL-backed hierarchy store."""

    def __init__(self, db: Session):
        self._repo = HierarchyRepository(db)

    def create(
        self,
        config: HierarchyConfiguration,
        *,
        config_id: str,
        created_by: Optional[str],
    ) -> HierarchyConfiguration:
        payload = _config_to_json(config, config_id=config_id)
        record = self._repo.create_hierarchy_configuration(
            config_id=config_id,
            company_id=config.company_id,
            hierarchy_type=config.hierarchy_type,
            name=config.name,
            configuration=payload,
            description=config.description,
            created_by=created_by,
        )
        if config.active is False:
            record = (
                self._repo.update_hierarchy_configuration(config_id, is_active=False)
                or record
            )
        return _db_to_response(record)

    def list(
        self,
        *,
        company_id: Optional[str],
        hierarchy_type: Optional[str],
        active: Optional[bool],
        limit: int,
        offset: int,
    ) -> Tuple[List[HierarchyConfiguration], int]:
        # Default behavior: active only unless explicitly requested.
        active_only = active is None or active is True
        configs = self._repo.get_hierarchy_configurations(
            company_id=company_id,
            hierarchy_type=hierarchy_type,
            active_only=active_only,
        )
        if active is False:
            configs = [c for c in configs if not c.is_active]

        total = len(configs)
        paginated = configs[offset : offset + limit]
        return [_db_to_response(c) for c in paginated], total

    def get(self, config_id: str) -> Optional[HierarchyConfiguration]:
        record = self._repo.get_hierarchy_configuration_by_id(config_id)
        return _db_to_response(record) if record else None

    def update(
        self, config_id: str, config: HierarchyConfiguration
    ) -> Optional[HierarchyConfiguration]:
        payload = _config_to_json(config, config_id=config_id)
        record = self._repo.update_hierarchy_configuration(
            config_id,
            company_id=config.company_id,
            hierarchy_type=config.hierarchy_type,
            name=config.name,
            description=config.description,
            configuration=payload,
            is_active=config.active,
        )
        return _db_to_response(record) if record else None

    def delete(self, config_id: str) -> bool:
        return self._repo.delete_hierarchy_configuration(config_id)

    def activate(self, config_id: str) -> bool:
        record = self._repo.get_hierarchy_configuration_by_id(config_id)
        if not record:
            return False
        return self._repo.activate_hierarchy_configuration(
            config_id, record.company_id, record.hierarchy_type
        )


def _config_to_json(config: HierarchyConfiguration, *, config_id: str) -> str:
    data = config.model_dump()
    data["id"] = config_id
    return json.dumps(data, ensure_ascii=False)


def _db_to_response(record) -> HierarchyConfiguration:
    levels: List[HierarchyLevel] = []
    try:
        parsed = json.loads(record.configuration)
        for level in parsed.get("levels", []) or []:
            levels.append(
                HierarchyLevel(
                    id=level["id"],
                    name=level["name"],
                    parent=level.get("parent"),
                    level=level["level"],
                    metadata=level.get("metadata"),
                )
            )
    except Exception:
        levels = []

    return HierarchyConfiguration(
        id=record.id,
        company_id=record.company_id,
        hierarchy_type=record.hierarchy_type,
        name=record.name,
        description=record.description,
        levels=levels,
        active=bool(record.is_active),
    )


def get_hierarchy_store(
    request: Request,
    db: Optional[Session] = Depends(get_db_optional),
):
    """Dependency that returns the configured store (DB when required)."""
    if settings.require_database:
        if db is None:
            raise HTTPException(status_code=503, detail="Database session unavailable")
        return DatabaseHierarchyStore(db)

    store = getattr(request.app.state, "hierarchy_store", None)
    if store is None:
        store = InMemoryHierarchyStore()
        request.app.state.hierarchy_store = store
    return store
