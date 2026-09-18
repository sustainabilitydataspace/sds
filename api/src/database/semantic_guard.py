"""Guards that prevent value operations from mutating semantic/catalog tables."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.database.models import (
    Concept,
    ConceptEquivalence,
    ConceptFormula,
    ConceptIndicatorLink,
    ConceptVariable,
    ConversionRule,
    HierarchyConfiguration,
    Indicator,
    StandardMapping,
    SustainabilityStandard,
    Unit,
    UnitCategory,
)


class SemanticIsolationError(RuntimeError):
    """Raised when a value-only operation attempts to mutate protected tables."""


PROTECTED_VALUE_WRITE_MODELS = (
    Indicator,
    StandardMapping,
    SustainabilityStandard,
    Concept,
    ConceptFormula,
    ConceptVariable,
    ConceptEquivalence,
    ConceptIndicatorLink,
    UnitCategory,
    Unit,
    ConversionRule,
    HierarchyConfiguration,
)


def _iter_identity_set(values: Any) -> Iterable[Any]:
    """Normalize SQLAlchemy identity sets or mocked collections to plain iterables."""
    if values is None:
        return ()
    if isinstance(values, (list, tuple, set, frozenset)):
        return values
    try:
        return tuple(values)
    except TypeError:
        return ()


def collect_protected_writes(session) -> list[str]:
    """Return a sorted list of protected model names pending mutation in the session."""
    touched: set[str] = set()
    for collection_name in ("new", "dirty", "deleted"):
        for item in _iter_identity_set(getattr(session, collection_name, ())):
            if isinstance(item, PROTECTED_VALUE_WRITE_MODELS):
                touched.add(type(item).__name__)
    return sorted(touched)


def ensure_value_operation_is_isolated(session, *, operation: str) -> None:
    """Fail if the current session is trying to mutate semantic/catalog tables."""
    touched = collect_protected_writes(session)
    if touched:
        raise SemanticIsolationError(
            f"Value operation '{operation}' attempted to mutate protected semantic/catalog tables: "
            + ", ".join(touched)
        )
