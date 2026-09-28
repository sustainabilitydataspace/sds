"""Resolve operational DB URLs without initializing application settings for explicit URLs."""

from __future__ import annotations

import os

from scripts.operational_db_url import resolve_operational_database_url


def resolve_operational_database_url_with_settings(cli_url: str | None = None) -> str:
    """Honor explicit CLI/env URLs first, then application settings (.env included)."""
    if cli_url is not None or os.getenv("DATABASE_URL") is not None:
        return resolve_operational_database_url(cli_url)
    from src.config.settings import settings

    return resolve_operational_database_url(
        cli_url, configured_url=settings.database_url.get_secret_value()
    )
