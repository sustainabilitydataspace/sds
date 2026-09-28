"""Standard mapping stores for canonical pairwise mappings and isolated fixtures."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import structlog
from src.database.repositories.standard_mapping_repository import (
    CanonicalPairwiseMappingRepository,
    MappingCandidateLimitExceeded,
)
from src.services.canonical_data import canonical_data_required, require_canonical_data

logger = structlog.get_logger(__name__)

FALLBACK_JSON_PATH = Path(__file__).parent.parent / "data" / "mappings.json"


class StandardMappingData:
    """Read-only standard mapping data structure."""

    def __init__(self, data: Dict[str, Any]):
        self.id = data.get("id", 0)
        self.source_standard = data.get("source_standard", "")
        self.source_code = data.get("source_code", "")
        self.source_label = data.get("source_label")
        self.target_standard = data.get("target_standard", "")
        self.target_code = data.get("target_code")
        self.target_label = data.get("target_label")
        self.esg_dimension = data.get("esg_dimension")
        self.relationship_type = data.get("relationship_type", "equivalent")
        self.confidence = data.get("confidence")
        self.dataset = data.get("dataset")
        self.source_row = data.get("source_row")


class InMemoryStandardMappingStore:
    """JSON-based standard mapping store for isolated fixtures.

    This store is isolated fixture/test only. It is not used for API runtime
    behavior. The public mappings surface is DB-backed by
    ``materialized_pairwise_mappings``.
    """

    def __init__(self, json_path: Optional[Path] = None):
        self._json_path = json_path or FALLBACK_JSON_PATH
        self._mappings: List[StandardMappingData] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        if self._json_path.exists():
            try:
                with open(self._json_path, "r") as f:
                    data = json.load(f)
                    items = data.get("mappings", data.get("crosswalks", []))
                    for item in items:
                        self._mappings.append(StandardMappingData(item))
                logger.info(
                    "Loaded standard mappings from JSON fallback",
                    count=len(self._mappings),
                    path=str(self._json_path),
                )
            except Exception as exc:
                logger.warning("Failed to load mappings JSON", error=str(exc))

        self._loaded = True

    def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        changed_since: Optional[datetime] = None,
    ) -> List[StandardMappingData]:
        self._ensure_loaded()
        if changed_since is not None:
            return []
        return self._mappings[offset : offset + limit]

    def count(self, changed_since: Optional[datetime] = None) -> int:
        self._ensure_loaded()
        if changed_since is not None:
            return 0
        return len(self._mappings)

    def find_by_source(
        self,
        standard: str,
        code: str,
        limit: int = 100,
    ) -> List[StandardMappingData]:
        self._ensure_loaded()
        std = standard.lower()
        code_lower = code.lower()
        results = [
            mapping
            for mapping in self._mappings
            if mapping.source_standard.lower() == std
            and mapping.source_code.lower().startswith(code_lower)
        ]
        return results[:limit]

    def find_by_target(
        self,
        standard: str,
        code: str,
        limit: int = 100,
    ) -> List[StandardMappingData]:
        self._ensure_loaded()
        std = standard.lower()
        code_lower = code.lower()
        results = [
            mapping
            for mapping in self._mappings
            if mapping.target_standard.lower() == std
            and mapping.target_code
            and mapping.target_code.lower().startswith(code_lower)
        ]
        return results[:limit]

    def search_by_dimension(
        self, dimension: str, limit: int = 100
    ) -> List[StandardMappingData]:
        self._ensure_loaded()
        results = [
            mapping for mapping in self._mappings if mapping.esg_dimension == dimension
        ]
        return results[:limit]

    def find_candidate_routes(
        self,
        *,
        source_standards: Optional[List[str]] = None,
        source_codes: Optional[List[str]] = None,
        target_standards: Optional[List[str]] = None,
        target_codes: Optional[List[str]] = None,
        limit: int = 500,
    ) -> List[StandardMappingData]:
        if limit <= 0:
            raise ValueError("candidate route limit must be positive")
        self._ensure_loaded()
        source_standards = {item.lower() for item in source_standards or [] if item}
        source_codes = [item.lower() for item in source_codes or [] if item]
        target_standards = {item.lower() for item in target_standards or [] if item}
        target_codes = [item.lower() for item in target_codes or [] if item]

        def side_matches(standard: str, code: Optional[str], standards, codes) -> bool:
            if standards and standard.lower() not in standards:
                return False
            if codes:
                code_lower = (code or "").lower()
                return any(code_lower.startswith(candidate) for candidate in codes)
            return True

        results = []
        for mapping in self._mappings:
            target_on_target = side_matches(
                mapping.target_standard,
                mapping.target_code,
                target_standards,
                target_codes,
            )
            target_on_source = side_matches(
                mapping.source_standard,
                mapping.source_code,
                target_standards,
                target_codes,
            )
            if source_standards or source_codes:
                source_on_source = side_matches(
                    mapping.source_standard,
                    mapping.source_code,
                    source_standards,
                    source_codes,
                )
                source_on_target = side_matches(
                    mapping.target_standard,
                    mapping.target_code,
                    source_standards,
                    source_codes,
                )
                if (source_on_source and target_on_target) or (
                    target_on_source and source_on_target
                ):
                    results.append(mapping)
            elif target_on_target or target_on_source:
                results.append(mapping)
            if len(results) > limit:
                raise MappingCandidateLimitExceeded(limit)
        return results

    def search(
        self,
        source_standard: Optional[str] = None,
        source_code: Optional[str] = None,
        target_standard: Optional[str] = None,
        target_code: Optional[str] = None,
        dimension: Optional[str] = None,
        min_confidence: Optional[float] = None,
        limit: int = 100,
        changed_since: Optional[datetime] = None,
    ) -> List[StandardMappingData]:
        self._ensure_loaded()
        if changed_since is not None:
            return []

        results = self._mappings
        if source_standard:
            std = source_standard.lower()
            results = [m for m in results if m.source_standard.lower() == std]
        if source_code:
            code_lower = source_code.lower()
            results = [
                m for m in results if m.source_code.lower().startswith(code_lower)
            ]
        if target_standard:
            std = target_standard.lower()
            results = [m for m in results if m.target_standard.lower() == std]
        if target_code:
            code_lower = target_code.lower()
            results = [
                m
                for m in results
                if m.target_code and m.target_code.lower().startswith(code_lower)
            ]
        if dimension:
            results = [m for m in results if m.esg_dimension == dimension]
        if min_confidence is not None:
            results = [
                m
                for m in results
                if m.confidence is not None and float(m.confidence) >= min_confidence
            ]

        return results[:limit]

    def find_between_standards(
        self,
        source_std: str,
        target_std: str,
        limit: int = 100,
    ) -> List[StandardMappingData]:
        self._ensure_loaded()
        src = source_std.lower()
        tgt = target_std.lower()
        results = [
            m
            for m in self._mappings
            if m.source_standard.lower() == src and m.target_standard.lower() == tgt
        ]
        return results[:limit]

    def get_supported_standards(self) -> List[str]:
        self._ensure_loaded()
        standards = {m.source_standard for m in self._mappings}
        standards.update({m.target_standard for m in self._mappings})
        return sorted({s for s in standards if s})


class StandardMappingStore:
    """Canonical pairwise mapping store.

    The public mappings surface is DB-backed by the canonical materialized
    pairwise read-model. The retired ``standard_mappings`` table cannot serve
    runtime requests, including when the canonical model is empty.
    The JSON fallback remains available only through ``InMemoryStandardMappingStore``
    for isolated fixture tests and is not used for API behavior.
    """

    def __init__(self, db: Optional[Session] = None):
        self._db = db
        self._use_db = db is not None

    def _get_repo(self) -> Optional[Any]:
        if self._use_db and self._db:
            return CanonicalPairwiseMappingRepository(self._db)
        return None

    def _get_repo_for_operation(self, operation: str) -> Optional[Any]:
        repo = self._get_repo()
        if repo is None and canonical_data_required():
            require_canonical_data(
                component="StandardMappingStore",
                operation=operation,
                reason="no database session is available",
            )
        return repo

    def _handle_repo_failure(self, operation: str, error: Exception) -> None:
        require_canonical_data(
            component="StandardMappingStore",
            operation=operation,
            reason="database query failed",
            error=error,
        )

    def is_db_available(self) -> bool:
        return self._use_db and self._db is not None

    def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        changed_since: Optional[datetime] = None,
    ) -> List[Any]:
        repo = self._get_repo_for_operation("get_all")
        if repo:
            try:
                return repo.get_all(
                    limit=limit, offset=offset, changed_since=changed_since
                )
            except Exception as exc:
                self._handle_repo_failure("get_all", exc)
        return []

    def count(self, changed_since: Optional[datetime] = None) -> int:
        repo = self._get_repo_for_operation("count")
        if repo:
            try:
                return repo.count(changed_since=changed_since)
            except Exception as exc:
                self._handle_repo_failure("count", exc)
        return 0

    def find_by_source(self, standard: str, code: str, limit: int = 100) -> List[Any]:
        repo = self._get_repo_for_operation("find_by_source")
        if repo:
            try:
                return repo.find_by_source(standard, code, limit=limit)
            except Exception as exc:
                self._handle_repo_failure("find_by_source", exc)
        return []

    def find_by_target(self, standard: str, code: str, limit: int = 100) -> List[Any]:
        repo = self._get_repo_for_operation("find_by_target")
        if repo:
            try:
                return repo.find_by_target(standard, code, limit=limit)
            except Exception as exc:
                self._handle_repo_failure("find_by_target", exc)
        return []

    def find_between_standards(
        self,
        source_std: str,
        target_std: str,
        limit: int = 100,
    ) -> List[Any]:
        repo = self._get_repo_for_operation("find_between_standards")
        if repo:
            try:
                return repo.find_between_standards(source_std, target_std, limit=limit)
            except Exception as exc:
                self._handle_repo_failure("find_between_standards", exc)
        return []

    def search(
        self,
        source_standard: Optional[str] = None,
        source_code: Optional[str] = None,
        target_standard: Optional[str] = None,
        target_code: Optional[str] = None,
        dimension: Optional[str] = None,
        min_confidence: Optional[float] = None,
        limit: int = 100,
        changed_since: Optional[datetime] = None,
    ) -> List[Any]:
        repo = self._get_repo_for_operation("search")
        if repo:
            try:
                return repo.search(
                    source_standard=source_standard,
                    source_code=source_code,
                    target_standard=target_standard,
                    target_code=target_code,
                    dimension=dimension,
                    min_confidence=min_confidence,
                    limit=limit,
                    changed_since=changed_since,
                )
            except Exception as exc:
                self._handle_repo_failure("search", exc)
        return []

    def get_supported_standards(self) -> List[str]:
        repo = self._get_repo_for_operation("get_supported_standards")
        if repo:
            try:
                return repo.get_supported_standards()
            except Exception as exc:
                self._handle_repo_failure("get_supported_standards", exc)
        return []

    def search_by_dimension(self, dimension: str, limit: int = 100) -> List[Any]:
        """Convenience wrapper for dimension-only searches."""
        return self.search(dimension=dimension, limit=limit)

    def find_candidate_routes(
        self,
        *,
        source_standards: Optional[List[str]] = None,
        source_codes: Optional[List[str]] = None,
        target_standards: Optional[List[str]] = None,
        target_codes: Optional[List[str]] = None,
        limit: int = 500,
    ) -> List[Any]:
        repo = self._get_repo_for_operation("find_candidate_routes")
        if repo:
            try:
                rows = repo.find_candidate_routes(
                    source_standards=source_standards,
                    source_codes=source_codes,
                    target_standards=target_standards,
                    target_codes=target_codes,
                    limit=limit,
                )
                if rows is None:
                    raise MappingCandidateLimitExceeded(limit)
                return rows
            except MappingCandidateLimitExceeded:
                raise
            except Exception as exc:
                if canonical_data_required():
                    self._handle_repo_failure("find_candidate_routes", exc)
                logger.warning(
                    "Canonical mapping candidate query failed",
                    operation="find_candidate_routes",
                    error=str(exc),
                )
                raise MappingCandidateLimitExceeded(limit) from exc
        return []


def get_standard_mapping_store(db: Optional[Session] = None) -> StandardMappingStore:
    """Factory function to get standard mapping store instance."""
    return StandardMappingStore(db=db)
