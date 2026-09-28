"""Database package for PostgreSQL models and session management."""

from .base import Base
from .models import (
    ConversionRule,
    HierarchyConfiguration,
    MetricType,
    Unit,
    UnitCategory,
)


def __getattr__(name):
    if name in {"SessionLocal", "engine", "get_db"}:
        from . import session

        return getattr(session, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Base",
    "SessionLocal",
    "engine",
    "get_db",
    "HierarchyConfiguration",
    "UnitCategory",
    "Unit",
    "ConversionRule",
    "MetricType",
]
