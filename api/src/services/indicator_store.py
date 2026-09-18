"""Indicator store with DB primary and optional JSON fallback (E1 integration)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import structlog
from src.database.repositories.indicator_repository import IndicatorRepository
from src.services.canonical_data import canonical_data_required, require_canonical_data

logger = structlog.get_logger(__name__)

# Path to JSON fallback file
FALLBACK_JSON_PATH = Path(__file__).parent.parent / "data" / "indicators.json"


class IndicatorData:
    """Read-only indicator data structure."""

    def __init__(self, data: Dict[str, Any]):
        self.id = data.get("id", "")
        self.identifier = data.get("identifier", "")
        self.title = data.get("title", "")
        self.indicator_name = data.get("indicator_name")
        self.description = data.get("description")
        self.dimension = data.get("dimension", "")
        self.unit_name = data.get("unit_name")
        self.unit_type = data.get("unit_type")
        self.periodicity = data.get("periodicity")
        self.period_type = data.get("period_type")
        self.source_ref = data.get("source_ref")
        self.code_esrs = data.get("code_esrs")
        self.code_gri = data.get("code_gri")
        self.code_gri_expanded = data.get("code_gri_expanded")
        self.evidence_path = data.get("evidence_path")
        self.source_row = data.get("source_row")
        self.owner = data.get("owner")
        self.access_rights = data.get("access_rights")
        self.validation_method = data.get("validation_method")
        self.double_materiality = data.get("double_materiality")
        self.value_type = data.get("value_type")


class InMemoryIndicatorStore:
    """JSON-based indicator store for offline mode."""

    def __init__(self, json_path: Optional[Path] = None):
        self._json_path = json_path or FALLBACK_JSON_PATH
        self._indicators: Dict[str, IndicatorData] = {}
        self._loaded = False

    def _ensure_loaded(self):
        """Lazy load indicators from JSON."""
        if self._loaded:
            return

        if self._json_path.exists():
            try:
                with open(self._json_path, "r") as f:
                    data = json.load(f)
                    for item in data.get("indicators", []):
                        ind = IndicatorData(item)
                        self._indicators[ind.identifier] = ind
                logger.info(
                    "Loaded indicators from JSON fallback",
                    count=len(self._indicators),
                    path=str(self._json_path),
                )
            except Exception as e:
                logger.warning("Failed to load indicators JSON", error=str(e))

        self._loaded = True

    def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        changed_since: Optional[datetime] = None,
    ) -> List[IndicatorData]:
        self._ensure_loaded()
        if changed_since is not None:
            return []
        items = list(self._indicators.values())
        return items[offset : offset + limit]

    def count(self, changed_since: Optional[datetime] = None) -> int:
        self._ensure_loaded()
        if changed_since is not None:
            return 0
        return len(self._indicators)

    def get_by_identifier(self, identifier: str) -> Optional[IndicatorData]:
        self._ensure_loaded()
        return self._indicators.get(identifier)

    def get_calculation_value_concept(self, concept: str) -> Optional[Any]:
        return None

    def search_by_dimension(
        self, dimension: str, limit: int = 100
    ) -> List[IndicatorData]:
        self._ensure_loaded()
        results = [
            ind for ind in self._indicators.values() if ind.dimension == dimension
        ]
        return results[:limit]

    def search_by_esrs(self, code: str, limit: int = 100) -> List[IndicatorData]:
        self._ensure_loaded()
        code_lower = code.lower()
        results = [
            ind
            for ind in self._indicators.values()
            if ind.code_esrs and ind.code_esrs.lower().startswith(code_lower)
        ]
        return results[:limit]

    def search_by_gri(self, code: str, limit: int = 100) -> List[IndicatorData]:
        self._ensure_loaded()
        code_lower = code.lower()
        results = [
            ind
            for ind in self._indicators.values()
            if ind.code_gri and ind.code_gri.lower().startswith(code_lower)
        ]
        return results[:limit]

    def search(
        self,
        *,
        dimension: Optional[str] = None,
        esrs: Optional[str] = None,
        gri: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 100,
        changed_since: Optional[datetime] = None,
    ) -> List[IndicatorData]:
        self._ensure_loaded()
        if changed_since is not None:
            return []
        results = list(self._indicators.values())
        if dimension:
            results = [ind for ind in results if ind.dimension == dimension]
        if esrs:
            code_lower = esrs.lower()
            results = [
                ind
                for ind in results
                if ind.code_esrs and ind.code_esrs.lower().startswith(code_lower)
            ]
        if gri:
            code_lower = gri.lower()
            results = [
                ind
                for ind in results
                if ind.code_gri and ind.code_gri.lower().startswith(code_lower)
            ]
        if query:
            query_lower = query.lower()
            results = [
                ind
                for ind in results
                if query_lower in (ind.title or "").lower()
                or query_lower in (ind.description or "").lower()
            ]
        return results[:limit]


class IndicatorStore:
    """
    Indicator store with PostgreSQL primary and optional JSON fallback.

    Uses database when available.
    In DB-first mode (`require_database=true`), failures are surfaced instead of
    silently serving stale JSON fallback data.
    """

    def __init__(self, db: Optional[Session] = None):
        self._db = db
        self._fallback = InMemoryIndicatorStore()
        self._use_db = db is not None

    def _get_repo(self) -> Optional[IndicatorRepository]:
        if self._use_db and self._db:
            return IndicatorRepository(self._db)
        return None

    def _get_repo_for_operation(self, operation: str) -> Optional[IndicatorRepository]:
        repo = self._get_repo()
        if repo is None and canonical_data_required():
            require_canonical_data(
                component="IndicatorStore",
                operation=operation,
                reason="no database session is available",
            )
        return repo

    def _handle_repo_failure(self, operation: str, error: Exception) -> None:
        if canonical_data_required():
            require_canonical_data(
                component="IndicatorStore",
                operation=operation,
                reason="database query failed",
                error=error,
            )
        logger.warning(
            "DB query failed, using fallback", operation=operation, error=str(error)
        )

    def is_db_available(self) -> bool:
        """Check if database is available."""
        return self._use_db and self._db is not None

    def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        changed_since: Optional[datetime] = None,
    ) -> List[Any]:
        """Get all indicators with pagination."""
        repo = self._get_repo_for_operation("get_all")
        if repo:
            try:
                return repo.get_all(
                    limit=limit, offset=offset, changed_since=changed_since
                )
            except Exception as e:
                self._handle_repo_failure("get_all", e)

        return self._fallback.get_all(
            limit=limit, offset=offset, changed_since=changed_since
        )

    def count(self, changed_since: Optional[datetime] = None) -> int:
        """Count total indicators."""
        repo = self._get_repo_for_operation("count")
        if repo:
            try:
                return repo.count(changed_since=changed_since)
            except Exception as e:
                self._handle_repo_failure("count", e)

        return self._fallback.count(changed_since=changed_since)

    def get_by_identifier(self, identifier: str) -> Optional[Any]:
        """Get indicator by URN identifier."""
        repo = self._get_repo_for_operation("get_by_identifier")
        if repo:
            try:
                return repo.get_by_identifier(identifier)
            except Exception as e:
                self._handle_repo_failure("get_by_identifier", e)

        return self._fallback.get_by_identifier(identifier)

    def get_calculation_value_concept(self, concept: str) -> Optional[Any]:
        """Resolve value concepts declared by imported calculation contracts."""
        if not concept:
            return None

        if self._use_db and self._db:
            try:
                from sqlalchemy import or_

                from src.database.models import (
                    CanonicalCalculationComponent,
                    CanonicalCalculationContract,
                )

                contract = (
                    self._db.query(CanonicalCalculationContract)
                    .filter(CanonicalCalculationContract.is_active.is_(True))
                    .filter(
                        or_(
                            CanonicalCalculationContract.canonical_datapoint_id
                            == concept,
                            CanonicalCalculationContract.node_id == concept,
                            CanonicalCalculationContract.indicator_identifier
                            == concept,
                        )
                    )
                    .first()
                )
                if contract is not None:
                    return contract

                return (
                    self._db.query(CanonicalCalculationComponent)
                    .join(CanonicalCalculationComponent.contract)
                    .filter(CanonicalCalculationContract.is_active.is_(True))
                    .filter(
                        or_(
                            CanonicalCalculationComponent.component_id == concept,
                            CanonicalCalculationComponent.component_node_id == concept,
                            CanonicalCalculationComponent.indicator_identifier
                            == concept,
                            CanonicalCalculationComponent.variable_uri == concept,
                        )
                    )
                    .first()
                )
            except Exception as e:
                logger.warning(
                    "Calculation value concept lookup failed",
                    operation="get_calculation_value_concept",
                    error=str(e),
                )

        return self._fallback.get_calculation_value_concept(concept)

    def search_by_dimension(self, dimension: str, limit: int = 100) -> List[Any]:
        """Search indicators by ESG dimension."""
        repo = self._get_repo_for_operation("search_by_dimension")
        if repo:
            try:
                return repo.search_by_dimension(dimension, limit=limit)
            except Exception as e:
                self._handle_repo_failure("search_by_dimension", e)

        return self._fallback.search_by_dimension(dimension, limit=limit)

    def search_by_esrs(self, code: str, limit: int = 100) -> List[Any]:
        """Search indicators by ESRS code."""
        repo = self._get_repo_for_operation("search_by_esrs")
        if repo:
            try:
                return repo.search_by_esrs(code, limit=limit)
            except Exception as e:
                self._handle_repo_failure("search_by_esrs", e)

        return self._fallback.search_by_esrs(code, limit=limit)

    def search_by_gri(self, code: str, limit: int = 100) -> List[Any]:
        """Search indicators by GRI code."""
        repo = self._get_repo_for_operation("search_by_gri")
        if repo:
            try:
                return repo.search_by_gri(code, limit=limit)
            except Exception as e:
                self._handle_repo_failure("search_by_gri", e)

        return self._fallback.search_by_gri(code, limit=limit)

    def search(
        self,
        *,
        dimension: Optional[str] = None,
        esrs: Optional[str] = None,
        gri: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 100,
        changed_since: Optional[datetime] = None,
    ) -> List[Any]:
        repo = self._get_repo_for_operation("search")
        if repo:
            try:
                return repo.search(
                    dimension=dimension,
                    esrs=esrs,
                    gri=gri,
                    query=query,
                    limit=limit,
                    changed_since=changed_since,
                )
            except Exception as e:
                self._handle_repo_failure("search", e)

        return self._fallback.search(
            dimension=dimension,
            esrs=esrs,
            gri=gri,
            query=query,
            limit=limit,
            changed_since=changed_since,
        )


def get_indicator_store(db: Optional[Session] = None) -> IndicatorStore:
    """Factory function to get indicator store instance."""
    return IndicatorStore(db=db)
