"""Read helpers for governed canonical concepts."""

from __future__ import annotations

from sqlalchemy.orm import Session

import structlog
from src.database.models import CanonicalConcept

logger = structlog.get_logger(__name__)


class CanonicalConceptStore:
    """Resolve current canonical concepts by stable URI."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_current_by_uri(self, canonical_uri: str) -> CanonicalConcept | None:
        try:
            return (
                self.db.query(CanonicalConcept)
                .filter(
                    CanonicalConcept.canonical_uri == canonical_uri,
                    CanonicalConcept.effective_to.is_(None),
                    CanonicalConcept.superseded_by.is_(None),
                )
                .order_by(CanonicalConcept.revision.desc(), CanonicalConcept.id.desc())
                .first()
            )
        except Exception as error:
            logger.warning(
                "Canonical concept lookup failed",
                operation="get_current_by_uri",
                canonical_uri=canonical_uri,
                error=str(error),
            )
            return None
