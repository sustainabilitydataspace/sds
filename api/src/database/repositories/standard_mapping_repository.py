"""Repository for standard-agnostic mapping operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from ..models import MaterializedPairwiseMapping, StandardMapping


@dataclass
class CanonicalPairwiseMappingData:
    """Read model row adapted to the public mapping response contract."""

    id: int
    source_standard: str
    source_code: str
    source_label: Optional[str]
    target_standard: str
    target_code: Optional[str]
    target_label: Optional[str]
    esg_dimension: Optional[str]
    relationship_type: Optional[str]
    confidence: Optional[float]
    dataset: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class StandardMappingRepository:
    """Repository for standard-agnostic mappings between sustainability standards."""

    def __init__(self, db: Session):
        self.db = db

    def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        active_only: bool = True,
        changed_since: Optional[datetime] = None,
    ) -> List[StandardMapping]:
        query = self.db.query(StandardMapping)
        if active_only:
            query = query.filter(StandardMapping.is_active.is_(True))
        if changed_since:
            query = query.filter(
                or_(
                    StandardMapping.updated_at >= changed_since,
                    StandardMapping.created_at >= changed_since,
                )
            )
        return query.order_by(StandardMapping.id).offset(offset).limit(limit).all()

    def count(
        self, active_only: bool = True, changed_since: Optional[datetime] = None
    ) -> int:
        query = self.db.query(StandardMapping)
        if active_only:
            query = query.filter(StandardMapping.is_active.is_(True))
        if changed_since:
            query = query.filter(
                or_(
                    StandardMapping.updated_at >= changed_since,
                    StandardMapping.created_at >= changed_since,
                )
            )
        return query.count()

    def get_by_id(self, id: int) -> Optional[StandardMapping]:
        """Get mapping by primary key ID."""
        return self.db.query(StandardMapping).filter(StandardMapping.id == id).first()

    @staticmethod
    def _code_lookup_filter(column, code: str):
        """Match exact/prefixed codes and prefixed public labels like 'GRI 303-5.c'."""
        return or_(column.ilike(f"{code}%"), column.ilike(f"% {code}%"))

    def find_by_source(
        self,
        standard: str,
        code: str,
        limit: int = 100,
    ) -> List[StandardMapping]:
        return (
            self.db.query(StandardMapping)
            .filter(
                and_(
                    StandardMapping.source_standard == standard,
                    self._code_lookup_filter(StandardMapping.source_code, code),
                    StandardMapping.is_active.is_(True),
                )
            )
            .order_by(StandardMapping.id)
            .limit(limit)
            .all()
        )

    def find_by_target(
        self,
        standard: str,
        code: str,
        limit: int = 100,
    ) -> List[StandardMapping]:
        return (
            self.db.query(StandardMapping)
            .filter(
                and_(
                    StandardMapping.target_standard == standard,
                    self._code_lookup_filter(StandardMapping.target_code, code),
                    StandardMapping.is_active.is_(True),
                )
            )
            .order_by(StandardMapping.id)
            .limit(limit)
            .all()
        )

    def find_between_standards(
        self,
        source_std: str,
        target_std: str,
        limit: int = 100,
    ) -> List[StandardMapping]:
        return (
            self.db.query(StandardMapping)
            .filter(
                and_(
                    StandardMapping.source_standard == source_std,
                    StandardMapping.target_standard == target_std,
                    StandardMapping.is_active.is_(True),
                )
            )
            .order_by(StandardMapping.id)
            .limit(limit)
            .all()
        )

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
    ) -> List[StandardMapping]:
        query = self.db.query(StandardMapping).filter(
            StandardMapping.is_active.is_(True)
        )

        if source_standard:
            query = query.filter(StandardMapping.source_standard == source_standard)
        if source_code:
            query = query.filter(
                self._code_lookup_filter(StandardMapping.source_code, source_code)
            )
        if target_standard:
            query = query.filter(StandardMapping.target_standard == target_standard)
        if target_code:
            query = query.filter(
                self._code_lookup_filter(StandardMapping.target_code, target_code)
            )
        if dimension:
            query = query.filter(StandardMapping.esg_dimension == dimension)
        if min_confidence is not None:
            query = query.filter(StandardMapping.confidence >= min_confidence)
        if changed_since:
            query = query.filter(
                or_(
                    StandardMapping.updated_at >= changed_since,
                    StandardMapping.created_at >= changed_since,
                )
            )

        return query.order_by(StandardMapping.id).limit(limit).all()

    def get_supported_standards(self) -> List[str]:
        sources = self.db.query(StandardMapping.source_standard).distinct().all()
        targets = self.db.query(StandardMapping.target_standard).distinct().all()
        standards = set([s[0] for s in sources] + [t[0] for t in targets])
        return sorted(standards)

    def bulk_upsert(
        self, records: List[Dict[str, Any]], *, deactivate_missing: bool = False
    ) -> int:
        """Bulk upsert mappings. Returns count of processed rows.

        When ``deactivate_missing`` is enabled, the provided records are treated
        as the authoritative active snapshot for each source/target standard
        pair present in the batch.
        """
        count = 0
        active_keys: set[tuple[str, str, str, str]] = set()
        standard_pairs: set[tuple[str, str]] = set()
        try:
            for record in records:
                source_standard = record.get("source_standard")
                source_code = record.get("source_code")
                target_standard = record.get("target_standard")
                target_code = record.get("target_code")

                if not source_standard or not source_code or not target_standard:
                    continue

                key = (
                    source_standard,
                    source_code,
                    target_standard,
                    target_code or "",
                )
                active_keys.add(key)
                standard_pairs.add((source_standard, target_standard))

                existing = (
                    self.db.query(StandardMapping)
                    .filter(
                        and_(
                            StandardMapping.source_standard == source_standard,
                            StandardMapping.source_code == source_code,
                            StandardMapping.target_standard == target_standard,
                            StandardMapping.target_code == target_code,
                        )
                    )
                    .first()
                )

                if existing:
                    for key, value in record.items():
                        if hasattr(existing, key) and key != "id":
                            setattr(existing, key, value)
                else:
                    self.db.add(StandardMapping(**record))

                count += 1

            if deactivate_missing:
                for source_standard, target_standard in standard_pairs:
                    active_rows = (
                        self.db.query(StandardMapping)
                        .filter(
                            and_(
                                StandardMapping.source_standard == source_standard,
                                StandardMapping.target_standard == target_standard,
                                StandardMapping.is_active.is_(True),
                            )
                        )
                        .all()
                    )
                    for row in active_rows:
                        key = (
                            row.source_standard,
                            row.source_code,
                            row.target_standard,
                            row.target_code or "",
                        )
                        if key not in active_keys:
                            row.is_active = False

            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return count

    def create(self, data: Dict[str, Any]) -> StandardMapping:
        """Create a new mapping entry."""
        mapping = StandardMapping(**data)
        self.db.add(mapping)
        self.db.commit()
        self.db.refresh(mapping)
        return mapping

    def delete(self, id: int) -> bool:
        """Soft delete a mapping."""
        mapping = (
            self.db.query(StandardMapping).filter(StandardMapping.id == id).first()
        )
        if mapping:
            mapping.is_active = False
            self.db.commit()
            return True
        return False


class CanonicalPairwiseMappingRepository:
    """Repository for the canonical Sygris-derived pairwise mapping read model."""

    def __init__(self, db: Session):
        self.db = db

    def _current_query(self):
        return (
            self.db.query(MaterializedPairwiseMapping)
            .options(
                joinedload(MaterializedPairwiseMapping.source_datapoint),
                joinedload(MaterializedPairwiseMapping.target_datapoint),
            )
            .filter(MaterializedPairwiseMapping.is_current.is_(True))
        )

    def _as_public_mapping(
        self, row: MaterializedPairwiseMapping
    ) -> CanonicalPairwiseMappingData:
        source_dp = getattr(row, "source_datapoint", None)
        target_dp = getattr(row, "target_datapoint", None)
        metadata = getattr(row, "metadata_json", None) or {}
        confidence = getattr(row, "match_strength", None)
        if confidence is not None:
            confidence = float(confidence)
        generated_at = getattr(row, "generated_at", None)
        return CanonicalPairwiseMappingData(
            id=row.id,
            source_standard=row.source_standard,
            source_code=row.source_code,
            source_label=getattr(source_dp, "label", None),
            target_standard=row.target_standard,
            target_code=row.target_code,
            target_label=getattr(target_dp, "label", None),
            esg_dimension=metadata.get("esg_dimension") or metadata.get("dimension"),
            relationship_type=row.relationship_type,
            confidence=confidence,
            dataset="canonical_pairwise_mappings",
            created_at=generated_at,
            updated_at=generated_at,
        )

    @staticmethod
    def _metadata_dimension_filter(dimension: str):
        metadata = MaterializedPairwiseMapping.metadata_json
        return or_(
            metadata["esg_dimension"].astext == dimension,
            metadata["dimension"].astext == dimension,
        )

    @staticmethod
    def _code_prefix_filter(column, codes: list[str]):
        return or_(*[column.ilike(f"{code}%") for code in codes])

    @staticmethod
    def _side_filter(*, standard_column, code_column, standards, codes):
        conditions = []
        if standards:
            conditions.append(standard_column.in_(standards))
        if codes:
            conditions.append(
                CanonicalPairwiseMappingRepository._code_prefix_filter(
                    code_column, codes
                )
            )
        if not conditions:
            return None
        return and_(*conditions)

    def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        active_only: bool = True,
        changed_since: Optional[datetime] = None,
    ) -> List[CanonicalPairwiseMappingData]:
        query = self._current_query()
        if changed_since:
            query = query.filter(
                MaterializedPairwiseMapping.generated_at >= changed_since
            )
        rows = (
            query.order_by(
                MaterializedPairwiseMapping.source_standard,
                MaterializedPairwiseMapping.source_code,
                MaterializedPairwiseMapping.target_standard,
                MaterializedPairwiseMapping.target_code,
                MaterializedPairwiseMapping.id,
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [self._as_public_mapping(row) for row in rows]

    def count(
        self, active_only: bool = True, changed_since: Optional[datetime] = None
    ) -> int:
        query = self._current_query()
        if changed_since:
            query = query.filter(
                MaterializedPairwiseMapping.generated_at >= changed_since
            )
        return query.count()

    def find_by_source(
        self,
        standard: str,
        code: str,
        limit: int = 100,
    ) -> List[CanonicalPairwiseMappingData]:
        rows = (
            self._current_query()
            .filter(
                and_(
                    MaterializedPairwiseMapping.source_standard == standard,
                    MaterializedPairwiseMapping.source_code.ilike(f"{code}%"),
                )
            )
            .order_by(
                MaterializedPairwiseMapping.target_standard,
                MaterializedPairwiseMapping.target_code,
                MaterializedPairwiseMapping.id,
            )
            .limit(limit)
            .all()
        )
        return [self._as_public_mapping(row) for row in rows]

    def find_by_target(
        self,
        standard: str,
        code: str,
        limit: int = 100,
    ) -> List[CanonicalPairwiseMappingData]:
        rows = (
            self._current_query()
            .filter(
                and_(
                    MaterializedPairwiseMapping.target_standard == standard,
                    MaterializedPairwiseMapping.target_code.ilike(f"{code}%"),
                )
            )
            .order_by(
                MaterializedPairwiseMapping.source_standard,
                MaterializedPairwiseMapping.source_code,
                MaterializedPairwiseMapping.id,
            )
            .limit(limit)
            .all()
        )
        return [self._as_public_mapping(row) for row in rows]

    def find_between_standards(
        self,
        source_std: str,
        target_std: str,
        limit: int = 100,
    ) -> List[CanonicalPairwiseMappingData]:
        rows = (
            self._current_query()
            .filter(
                and_(
                    MaterializedPairwiseMapping.source_standard == source_std,
                    MaterializedPairwiseMapping.target_standard == target_std,
                )
            )
            .order_by(
                MaterializedPairwiseMapping.source_code,
                MaterializedPairwiseMapping.target_code,
                MaterializedPairwiseMapping.id,
            )
            .limit(limit)
            .all()
        )
        return [self._as_public_mapping(row) for row in rows]

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
    ) -> List[CanonicalPairwiseMappingData]:
        query = self._current_query()
        if source_standard:
            query = query.filter(
                MaterializedPairwiseMapping.source_standard == source_standard
            )
        if source_code:
            query = query.filter(
                MaterializedPairwiseMapping.source_code.ilike(f"{source_code}%")
            )
        if target_standard:
            query = query.filter(
                MaterializedPairwiseMapping.target_standard == target_standard
            )
        if target_code:
            query = query.filter(
                MaterializedPairwiseMapping.target_code.ilike(f"{target_code}%")
            )
        if min_confidence is not None:
            query = query.filter(
                MaterializedPairwiseMapping.match_strength >= min_confidence
            )
        if changed_since:
            query = query.filter(
                MaterializedPairwiseMapping.generated_at >= changed_since
            )
        if dimension:
            query = query.filter(self._metadata_dimension_filter(dimension))

        rows = (
            query.order_by(
                MaterializedPairwiseMapping.source_standard,
                MaterializedPairwiseMapping.source_code,
                MaterializedPairwiseMapping.target_standard,
                MaterializedPairwiseMapping.target_code,
                MaterializedPairwiseMapping.id,
            )
            .limit(limit)
            .all()
        )
        return [self._as_public_mapping(row) for row in rows]

    def find_candidate_routes(
        self,
        *,
        source_standards: Optional[List[str]] = None,
        source_codes: Optional[List[str]] = None,
        target_standards: Optional[List[str]] = None,
        target_codes: Optional[List[str]] = None,
        limit: int = 500,
    ) -> List[CanonicalPairwiseMappingData]:
        source_standards = [item for item in source_standards or [] if item]
        source_codes = [item for item in source_codes or [] if item]
        target_standards = [item for item in target_standards or [] if item]
        target_codes = [item for item in target_codes or [] if item]

        target_on_target = self._side_filter(
            standard_column=MaterializedPairwiseMapping.target_standard,
            code_column=MaterializedPairwiseMapping.target_code,
            standards=target_standards,
            codes=target_codes,
        )
        target_on_source = self._side_filter(
            standard_column=MaterializedPairwiseMapping.source_standard,
            code_column=MaterializedPairwiseMapping.source_code,
            standards=target_standards,
            codes=target_codes,
        )

        if source_standards or source_codes:
            source_on_source = self._side_filter(
                standard_column=MaterializedPairwiseMapping.source_standard,
                code_column=MaterializedPairwiseMapping.source_code,
                standards=source_standards,
                codes=source_codes,
            )
            source_on_target = self._side_filter(
                standard_column=MaterializedPairwiseMapping.target_standard,
                code_column=MaterializedPairwiseMapping.target_code,
                standards=source_standards,
                codes=source_codes,
            )
            route_conditions = [
                (
                    and_(source_on_source, target_on_target)
                    if source_on_source is not None and target_on_target is not None
                    else None
                ),
                (
                    and_(target_on_source, source_on_target)
                    if target_on_source is not None and source_on_target is not None
                    else None
                ),
            ]
        else:
            route_conditions = [target_on_target, target_on_source]

        route_conditions = [
            condition for condition in route_conditions if condition is not None
        ]
        if not route_conditions:
            return []

        rows = (
            self._current_query()
            .filter(or_(*route_conditions))
            .order_by(
                MaterializedPairwiseMapping.source_standard,
                MaterializedPairwiseMapping.source_code,
                MaterializedPairwiseMapping.target_standard,
                MaterializedPairwiseMapping.target_code,
                MaterializedPairwiseMapping.id,
            )
            .limit(limit)
            .all()
        )
        return [self._as_public_mapping(row) for row in rows]

    def get_supported_standards(self) -> List[str]:
        sources = (
            self._current_query()
            .with_entities(MaterializedPairwiseMapping.source_standard)
            .distinct()
            .all()
        )
        targets = (
            self._current_query()
            .with_entities(MaterializedPairwiseMapping.target_standard)
            .distinct()
            .all()
        )
        standards = set([s[0] for s in sources] + [t[0] for t in targets])
        return sorted(standards)
