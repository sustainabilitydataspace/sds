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
    StandardMappingRepository,
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
        return results[:limit]

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
    pairwise read-model. Fresh local DBs also seed the legacy ``standard_mappings``
    table from bundled reference data; when the canonical read-model has not yet
    been materialized, this store reads that DB table as a compatibility source.
    The JSON fallback remains available only through ``InMemoryStandardMappingStore``
    for isolated fixture tests and is not used for API behavior.
    """

    def __init__(self, db: Optional[Session] = None):
        self._db = db
        self._fallback = InMemoryStandardMappingStore()
        self._use_db = db is not None

    def _get_repo(self) -> Optional[Any]:
        if self._use_db and self._db:
            return CanonicalPairwiseMappingRepository(self._db)
        return None

    def _get_legacy_repo(self) -> Optional[Any]:
        if self._use_db and self._db:
            return StandardMappingRepository(self._db)
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

    def _canonical_read_model_is_empty(self, repo: Any, operation: str) -> bool:
        try:
            return repo.count() == 0
        except Exception as exc:
            self._handle_repo_failure(f"{operation}.canonical_count", exc)
            return False

    def _handle_repo_failure(self, operation: str, error: Exception) -> None:
        if canonical_data_required():
            require_canonical_data(
                component="StandardMappingStore",
                operation=operation,
                reason="database query failed",
                error=error,
            )
        logger.warning(
            "Canonical mapping DB query failed",
            operation=operation,
            error=str(error),
        )

    @staticmethod
    def _add_unique(rows_by_id: dict[str, Any], rows: List[Any]) -> None:
        for row in rows or []:
            key = str(getattr(row, "id", id(row)))
            rows_by_id[key] = row

    def _legacy_candidate_routes(
        self,
        legacy_repo: Any,
        *,
        source_standards: Optional[List[str]] = None,
        source_codes: Optional[List[str]] = None,
        target_standards: Optional[List[str]] = None,
        target_codes: Optional[List[str]] = None,
        limit: int = 500,
    ) -> List[Any]:
        rows_by_id: dict[str, Any] = {}
        source_standards = [item for item in source_standards or [] if item] or [None]
        source_codes = [item for item in source_codes or [] if item] or [None]
        target_standards = [item for item in target_standards or [] if item] or [None]
        target_codes = [item for item in target_codes or [] if item] or [None]

        def search_once(**kwargs: Any) -> None:
            filtered = {key: value for key, value in kwargs.items() if value}
            filtered["limit"] = limit
            self._add_unique(rows_by_id, legacy_repo.search(**filtered))

        for source_standard in source_standards:
            for source_code in source_codes:
                for target_standard in target_standards:
                    for target_code in target_codes:
                        search_once(
                            source_standard=source_standard,
                            source_code=source_code,
                            target_standard=target_standard,
                            target_code=target_code,
                        )
                        search_once(
                            source_standard=target_standard,
                            source_code=target_code,
                            target_standard=source_standard,
                            target_code=source_code,
                        )

        return list(rows_by_id.values())[:limit]

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
                rows = repo.get_all(
                    limit=limit, offset=offset, changed_since=changed_since
                )
                if rows or not self._canonical_read_model_is_empty(repo, "get_all"):
                    return rows
            except Exception as exc:
                self._handle_repo_failure("get_all", exc)
                return []
        legacy_repo = self._get_legacy_repo()
        if legacy_repo:
            try:
                return legacy_repo.get_all(
                    limit=limit, offset=offset, changed_since=changed_since
                )
            except Exception as exc:
                self._handle_repo_failure("get_all.legacy", exc)
        return []

    def count(self, changed_since: Optional[datetime] = None) -> int:
        repo = self._get_repo_for_operation("count")
        if repo:
            try:
                count = repo.count(changed_since=changed_since)
                if count > 0:
                    return count
            except Exception as exc:
                self._handle_repo_failure("count", exc)
                return 0
        legacy_repo = self._get_legacy_repo()
        if legacy_repo:
            try:
                return legacy_repo.count(changed_since=changed_since)
            except Exception as exc:
                self._handle_repo_failure("count.legacy", exc)
        return 0

    def find_by_source(self, standard: str, code: str, limit: int = 100) -> List[Any]:
        repo = self._get_repo_for_operation("find_by_source")
        if repo:
            try:
                rows = repo.find_by_source(standard, code, limit=limit)
                if rows or not self._canonical_read_model_is_empty(
                    repo, "find_by_source"
                ):
                    return rows
            except Exception as exc:
                self._handle_repo_failure("find_by_source", exc)
                return []
        legacy_repo = self._get_legacy_repo()
        if legacy_repo:
            try:
                return legacy_repo.find_by_source(standard, code, limit=limit)
            except Exception as exc:
                self._handle_repo_failure("find_by_source.legacy", exc)
        return []

    def find_by_target(self, standard: str, code: str, limit: int = 100) -> List[Any]:
        repo = self._get_repo_for_operation("find_by_target")
        if repo:
            try:
                rows = repo.find_by_target(standard, code, limit=limit)
                if rows or not self._canonical_read_model_is_empty(
                    repo, "find_by_target"
                ):
                    return rows
            except Exception as exc:
                self._handle_repo_failure("find_by_target", exc)
                return []
        legacy_repo = self._get_legacy_repo()
        if legacy_repo:
            try:
                return legacy_repo.find_by_target(standard, code, limit=limit)
            except Exception as exc:
                self._handle_repo_failure("find_by_target.legacy", exc)
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
                rows = repo.find_between_standards(source_std, target_std, limit=limit)
                if rows or not self._canonical_read_model_is_empty(
                    repo, "find_between_standards"
                ):
                    return rows
            except Exception as exc:
                self._handle_repo_failure("find_between_standards", exc)
                return []
        legacy_repo = self._get_legacy_repo()
        if legacy_repo:
            try:
                return legacy_repo.find_between_standards(
                    source_std, target_std, limit=limit
                )
            except Exception as exc:
                self._handle_repo_failure("find_between_standards.legacy", exc)
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
                rows = repo.search(
                    source_standard=source_standard,
                    source_code=source_code,
                    target_standard=target_standard,
                    target_code=target_code,
                    dimension=dimension,
                    min_confidence=min_confidence,
                    limit=limit,
                    changed_since=changed_since,
                )
                if rows or not self._canonical_read_model_is_empty(repo, "search"):
                    return rows
            except Exception as exc:
                self._handle_repo_failure("search", exc)
                return []
        legacy_repo = self._get_legacy_repo()
        if legacy_repo:
            try:
                return legacy_repo.search(
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
                self._handle_repo_failure("search.legacy", exc)
        return []

    def get_supported_standards(self) -> List[str]:
        repo = self._get_repo_for_operation("get_supported_standards")
        if repo:
            try:
                standards = repo.get_supported_standards()
                if standards or not self._canonical_read_model_is_empty(
                    repo, "get_supported_standards"
                ):
                    return standards
            except Exception as exc:
                self._handle_repo_failure("get_supported_standards", exc)
                return []
        legacy_repo = self._get_legacy_repo()
        if legacy_repo:
            try:
                return legacy_repo.get_supported_standards()
            except Exception as exc:
                self._handle_repo_failure("get_supported_standards.legacy", exc)
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
                if rows or not self._canonical_read_model_is_empty(
                    repo, "find_candidate_routes"
                ):
                    return rows
            except Exception as exc:
                self._handle_repo_failure("find_candidate_routes", exc)
                return []
        legacy_repo = self._get_legacy_repo()
        if legacy_repo:
            try:
                return self._legacy_candidate_routes(
                    legacy_repo,
                    source_standards=source_standards,
                    source_codes=source_codes,
                    target_standards=target_standards,
                    target_codes=target_codes,
                    limit=limit,
                )
            except Exception as exc:
                self._handle_repo_failure("find_candidate_routes.legacy", exc)
        return []


def get_standard_mapping_store(db: Optional[Session] = None) -> StandardMappingStore:
    """Factory function to get standard mapping store instance."""
    return StandardMappingStore(db=db)
