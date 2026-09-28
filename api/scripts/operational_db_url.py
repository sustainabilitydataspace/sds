"""Fail-closed database URL selection for standalone operational CLIs.

This helper has no Settings import so --help, explicit --db-url and the public
manifest command do not require database configuration or load a dotenv file.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, quote, unquote, urlsplit

_ERROR = "Explicit database configuration required"
_UNSAFE_PASSWORDS = frozenset({"", "***", "[redacted]", "pass", "password"})
_PG_SCHEMES = frozenset({"postgresql", "postgresql+psycopg2", "postgresql+psycopg"})
_PG_QUERY_OVERRIDES = frozenset(
    {
        "password",
        "user",
        "username",
        "host",
        "hostaddr",
        "port",
        "database",
        "dbname",
        "service",
        "servicefile",
        "passfile",
    }
)


def _checked_url(value: str) -> str:
    if not value.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError(_ERROR)
    try:
        parsed = urlsplit(value)
        password = parsed.password
        if parsed.scheme in _PG_SCHEMES:
            if (
                not value.startswith(parsed.scheme + "://")
                or not parsed.hostname
                or not parsed.username
                or password is None
                or not parsed.path.startswith("/")
                or parsed.path == "/"
                or parsed.fragment
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)
            ):
                raise ValueError(_ERROR)
            query_keys = [
                key.lower()
                for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
            ]
            if any(key in _PG_QUERY_OVERRIDES for key in query_keys) or len(
                query_keys
            ) != len(set(query_keys)):
                raise ValueError(_ERROR)
        elif (
            parsed.scheme != "sqlite"
            or not value.startswith("sqlite://")
            or parsed.fragment
            or parsed.query
        ):
            raise ValueError(_ERROR)
    except ValueError:
        raise ValueError(_ERROR) from None
    if password is not None and unquote(password).strip().lower() in _UNSAFE_PASSWORDS:
        raise ValueError(_ERROR)
    return value


def resolve_operational_database_url(
    cli_url: str | None = None, *, configured_url: str | None = None
) -> str:
    """Prefer explicit CLI, configured settings, environment, then explicit parts.

    The settings model's redacted example URL is not a credential and must not
    shadow an explicitly configured environment. No failure embeds a URL.
    """
    if cli_url is not None:
        return _checked_url(cli_url)
    if configured_url:
        try:
            configured_password = urlsplit(configured_url).password
        except ValueError:
            raise ValueError(_ERROR) from None
        if configured_password not in {"***", "[REDACTED]"}:
            return _checked_url(configured_url)
    env_url = os.getenv("DATABASE_URL")
    if env_url is not None:
        return _checked_url(env_url)
    password = os.getenv("POSTGRES_PASSWORD")
    if password is None or password.strip().lower() in _UNSAFE_PASSWORDS:
        raise ValueError(_ERROR)
    user = quote(os.getenv("POSTGRES_USER", "sds"), safe="")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = quote(os.getenv("POSTGRES_DB", "sds"), safe="")
    if not user or not host or not port.isdecimal() or not database:
        raise ValueError(_ERROR)
    return _checked_url(
        f"postgresql://{user}:{quote(password, safe='')}@{host}:{port}/{database}"
    )
