"""Semantic dimension catalog read (VARCH-8d support).

A small bitemporal read over the published semantic axes/terms vocabulary backing the
``/api/v1/semantic-dimensions`` browse surface. Sync, legacy ``.query()`` style, matching the
repo's repository pattern. The axes/terms are the public dimension vocabulary; the bitemporal
slice predicate mirrors every other resolver read (``status='published'`` + half-open
``[valid_from, valid_to)`` containing ``valid_as_of`` + ``decision_commit_id <= read``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from src.database.semantic_models import SemanticAxis, SemanticTerm


class SemanticCatalogRepository:
    """Published semantic axes/terms for a bitemporal slice."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _published_axis_query(
        self,
        valid_as_of: datetime,
        decision_commit_id: int,
        *,
        axis_keys: Iterable[str] | None = None,
    ):
        query = self.db.query(SemanticAxis).filter(
            SemanticAxis.status == "published",
            SemanticAxis.valid_from <= valid_as_of,
            or_(
                SemanticAxis.valid_to.is_(None),
                SemanticAxis.valid_to > valid_as_of,
            ),
            SemanticAxis.decision_commit_id <= decision_commit_id,
        )
        keys = _normalize_axis_keys(axis_keys)
        if keys is not None:
            query = query.filter(SemanticAxis.axis_key.in_(keys))
        return query

    def count_published_axes(
        self,
        *,
        valid_as_of: datetime,
        decision_commit_id: int,
        axis_keys: Iterable[str] | None = None,
    ) -> int:
        keys = _normalize_axis_keys(axis_keys)
        if keys == ():
            return 0
        query = self.db.query(func.count(SemanticAxis.id)).filter(
            SemanticAxis.status == "published",
            SemanticAxis.valid_from <= valid_as_of,
            or_(
                SemanticAxis.valid_to.is_(None),
                SemanticAxis.valid_to > valid_as_of,
            ),
            SemanticAxis.decision_commit_id <= decision_commit_id,
        )
        if keys is not None:
            query = query.filter(SemanticAxis.axis_key.in_(keys))
        return query.scalar()

    def list_published_axes(
        self,
        *,
        valid_as_of: datetime,
        decision_commit_id: int,
        limit: int,
        offset: int,
        axis_keys: Iterable[str] | None = None,
    ) -> list[dict]:
        keys = _normalize_axis_keys(axis_keys)
        if keys == ():
            return []
        rows = (
            self._published_axis_query(valid_as_of, decision_commit_id, axis_keys=keys)
            .order_by(SemanticAxis.axis_key, SemanticAxis.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            {
                "axis_key": r.axis_key,
                "label": r.label,
                "description": r.description,
                "axis_hash": r.axis_hash,
            }
            for r in rows
        ]

    def list_published_terms(
        self, *, axis_id: str, valid_as_of: datetime, decision_commit_id: int
    ) -> list[dict]:
        rows = (
            self.db.query(SemanticTerm)
            .filter(
                SemanticTerm.axis_id == axis_id,
                SemanticTerm.status == "published",
                SemanticTerm.valid_from <= valid_as_of,
                or_(
                    SemanticTerm.valid_to.is_(None),
                    SemanticTerm.valid_to > valid_as_of,
                ),
                SemanticTerm.decision_commit_id <= decision_commit_id,
            )
            .order_by(SemanticTerm.term_key, SemanticTerm.id)
            .all()
        )
        return [
            {"term_key": r.term_key, "label": r.label, "description": r.description}
            for r in rows
        ]


def _normalize_axis_keys(axis_keys: Iterable[str] | None) -> tuple[str, ...] | None:
    if axis_keys is None:
        return None
    return tuple(
        sorted({str(key) for key in axis_keys if key is not None and str(key)})
    )
