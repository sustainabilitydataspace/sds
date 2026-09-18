"""Repository for indicator-related database operations (E1 integration)."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models import Indicator


def _esrs_code_filter(code: str):
    normalized = code.strip()
    path_token = normalized.lower().replace("-", "_").replace(":", "_")
    return or_(
        Indicator.code_esrs.ilike(f"{normalized}%"),
        Indicator.source_ref.ilike(f"%{normalized}%"),
        Indicator.evidence_path.ilike(f"%/{path_token}/%"),
        Indicator.evidence_path.ilike(f"%/{path_token}"),
    )


class IndicatorRepository:
    """Repository for E1 indicator operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        active_only: bool = True,
        changed_since: Optional[datetime] = None,
    ) -> List[Indicator]:
        """Get all indicators with pagination."""
        query = self.db.query(Indicator)
        if active_only:
            query = query.filter(Indicator.is_active.is_(True))
        if changed_since:
            query = query.filter(
                or_(
                    Indicator.updated_at >= changed_since,
                    Indicator.created_at >= changed_since,
                )
            )
        return query.order_by(Indicator.identifier).offset(offset).limit(limit).all()

    def count(
        self, active_only: bool = True, changed_since: Optional[datetime] = None
    ) -> int:
        """Count total indicators."""
        query = self.db.query(Indicator)
        if active_only:
            query = query.filter(Indicator.is_active.is_(True))
        if changed_since:
            query = query.filter(
                or_(
                    Indicator.updated_at >= changed_since,
                    Indicator.created_at >= changed_since,
                )
            )
        return query.count()

    def get_by_id(self, id: str) -> Optional[Indicator]:
        """Get indicator by primary key ID."""
        return self.db.query(Indicator).filter(Indicator.id == id).first()

    def get_by_identifier(self, identifier: str) -> Optional[Indicator]:
        """Get indicator by URN identifier (e.g., urn:sds:reg:2)."""
        return (
            self.db.query(Indicator)
            .filter(
                and_(Indicator.identifier == identifier, Indicator.is_active.is_(True))
            )
            .first()
        )

    def get_active_by_id_or_identifier(self, value: str) -> Optional[Indicator]:
        """Public catalog lookup by primary key OR URN identifier, ACTIVE only.

        Fail-closed for public reads: a retired indicator must not remain
        directly retrievable by id even though list/search filter is_active
        (codex F04 M1). Internal/admin callers that need any state use
        ``get_by_id`` / ``get_by_identifier_any_state``.
        """
        return (
            self.db.query(Indicator)
            .filter(
                and_(
                    or_(Indicator.id == value, Indicator.identifier == value),
                    Indicator.is_active.is_(True),
                )
            )
            .first()
        )

    def get_by_identifier_any_state(self, identifier: str) -> Optional[Indicator]:
        """Get an indicator by URN regardless of active/retired state."""
        return (
            self.db.query(Indicator)
            .filter(
                or_(
                    Indicator.identifier == identifier,
                    Indicator.id == identifier,
                )
            )
            .first()
        )

    def search_by_dimension(self, dimension: str, limit: int = 100) -> List[Indicator]:
        """Search indicators by ESG dimension (E, S, G, Transversal)."""
        return (
            self.db.query(Indicator)
            .filter(
                and_(Indicator.dimension == dimension, Indicator.is_active.is_(True))
            )
            .limit(limit)
            .all()
        )

    def search_by_esrs(self, code: str, limit: int = 100) -> List[Indicator]:
        """Search indicators by ESRS code (exact or prefix match)."""
        return (
            self.db.query(Indicator)
            .filter(
                and_(
                    _esrs_code_filter(code),
                    Indicator.is_active.is_(True),
                )
            )
            .limit(limit)
            .all()
        )

    def search_by_gri(self, code: str, limit: int = 100) -> List[Indicator]:
        """Search indicators by GRI code (exact or prefix match)."""
        return (
            self.db.query(Indicator)
            .filter(
                and_(
                    Indicator.code_gri.ilike(f"{code}%"), Indicator.is_active.is_(True)
                )
            )
            .limit(limit)
            .all()
        )

    def search(
        self,
        dimension: Optional[str] = None,
        esrs: Optional[str] = None,
        gri: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 100,
        changed_since: Optional[datetime] = None,
    ) -> List[Indicator]:
        """Search indicators with multiple filters."""
        q = self.db.query(Indicator).filter(Indicator.is_active.is_(True))

        if dimension:
            q = q.filter(Indicator.dimension == dimension)
        if esrs:
            q = q.filter(_esrs_code_filter(esrs))
        if gri:
            q = q.filter(Indicator.code_gri.ilike(f"{gri}%"))
        if query:
            q = q.filter(
                or_(
                    Indicator.identifier.ilike(f"%{query}%"),
                    Indicator.title.ilike(f"%{query}%"),
                    Indicator.indicator_name.ilike(f"%{query}%"),
                    Indicator.description.ilike(f"%{query}%"),
                    Indicator.code_esrs.ilike(f"%{query}%"),
                    Indicator.code_gri.ilike(f"%{query}%"),
                    Indicator.code_gri_expanded.ilike(f"%{query}%"),
                    Indicator.source_ref.ilike(f"%{query}%"),
                    Indicator.evidence_path.ilike(
                        f"%{query.lower().replace('-', '_').replace(':', '_')}%"
                    ),
                )
            )
        if changed_since:
            q = q.filter(
                or_(
                    Indicator.updated_at >= changed_since,
                    Indicator.created_at >= changed_since,
                )
            )

        return q.order_by(Indicator.identifier).limit(limit).all()

    def create(self, data: Dict[str, Any]) -> Indicator:
        """Create a new indicator."""
        indicator = Indicator(**data)
        self.db.add(indicator)
        self.db.commit()
        self.db.refresh(indicator)
        return indicator

    def update(self, id: str, data: Dict[str, Any]) -> Optional[Indicator]:
        """Update an existing indicator."""
        indicator = self.get_by_id(id)
        if indicator:
            for key, value in data.items():
                if hasattr(indicator, key):
                    setattr(indicator, key, value)
            self.db.commit()
            self.db.refresh(indicator)
        return indicator

    def bulk_upsert(self, records: List[Dict[str, Any]], *, commit: bool = True) -> int:
        """Bulk insert or update indicators. Returns count of affected rows."""
        count = 0
        try:
            for record in records:
                identifier = record.get("identifier")
                if not identifier:
                    continue

                existing = self.get_by_identifier_any_state(identifier)
                if existing:
                    if "is_active" not in record and hasattr(existing, "is_active"):
                        existing.is_active = True
                    for key, value in record.items():
                        if hasattr(existing, key) and key != "id":
                            setattr(existing, key, value)
                    count += 1
                else:
                    if "id" not in record:
                        record["id"] = identifier
                    self.db.add(Indicator(**record))
                    count += 1

            if commit:
                self.db.commit()
            else:
                self.db.flush()
        except Exception:
            if commit:
                self.db.rollback()
            raise
        return count

    def delete(self, id: str) -> bool:
        """Soft delete an indicator."""
        indicator = self.get_by_id(id)
        if indicator:
            indicator.is_active = False
            self.db.commit()
            return True
        return False
