from __future__ import annotations

import logging
from typing import Any

import structlog


def configure_runtime_logging(
    level_name: str,
    *,
    structlog_module: Any = structlog,
) -> None:
    """Apply the validated SDS log level to stdlib and structlog."""
    level = getattr(logging, level_name.upper())
    logging.basicConfig(level=level)
    logging.getLogger().setLevel(level)

    configure = getattr(structlog_module, "configure", None)
    make_filtering_bound_logger = getattr(
        structlog_module, "make_filtering_bound_logger", None
    )
    if callable(configure) and callable(make_filtering_bound_logger):
        configure(wrapper_class=make_filtering_bound_logger(level))
