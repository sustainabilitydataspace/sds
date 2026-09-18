"""Helpers for DB-first canonical data enforcement."""

from __future__ import annotations

from typing import Optional

import structlog
from src.config.settings import settings

logger = structlog.get_logger(__name__)


class CanonicalDataUnavailableError(RuntimeError):
    """Raised when DB-first mode cannot serve canonical catalog/semantic data."""


def require_canonical_data(
    *,
    component: str,
    operation: str,
    reason: str,
    error: Optional[BaseException] = None,
) -> None:
    """Raise a deterministic error when DB-first mode cannot serve canonical data."""
    detail = (
        f"{component} requires canonical PostgreSQL data for {operation}, "
        f"but it is unavailable: {reason}"
    )
    if error is not None:
        logger.error(
            "Canonical data unavailable",
            component=component,
            operation=operation,
            reason=reason,
            error=str(error),
        )
    else:
        logger.error(
            "Canonical data unavailable",
            component=component,
            operation=operation,
            reason=reason,
        )
    raise CanonicalDataUnavailableError(detail) from error


def canonical_data_required() -> bool:
    """Return whether the runtime is operating in DB-first strict mode."""
    return bool(settings.require_database)
