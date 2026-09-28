from __future__ import annotations

import logging
from typing import Any


class DummyLogger:
    """Logging-backed structlog compatibility surface for src-first launches."""

    def __init__(
        self,
        name: str | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self._logger = logging.getLogger(name)
        self._context = dict(context or {})

    def bind(self, **kwargs: Any) -> "DummyLogger":
        return DummyLogger(
            self._logger.name,
            context={**self._context, **kwargs},
        )

    def _emit(self, level: int, event: Any, **kwargs: Any) -> None:
        self._logger.log(
            level,
            str(event),
            extra={"sds_context": {**self._context, **kwargs}},
        )

    def info(self, event: Any, **kwargs: Any) -> None:
        self._emit(logging.INFO, event, **kwargs)

    def debug(self, event: Any, **kwargs: Any) -> None:
        self._emit(logging.DEBUG, event, **kwargs)

    def warning(self, event: Any, **kwargs: Any) -> None:
        self._emit(logging.WARNING, event, **kwargs)

    def error(self, event: Any, **kwargs: Any) -> None:
        self._emit(logging.ERROR, event, **kwargs)


def get_logger(name: str | None = None, **kwargs: Any) -> DummyLogger:
    return DummyLogger(name, context=kwargs)
