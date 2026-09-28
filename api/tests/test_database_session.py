"""Tests for database session utilities."""

from __future__ import annotations

import importlib
from unittest.mock import Mock


def test_get_db_yields_and_closes(monkeypatch):
    import src.database.session as session_mod

    db = Mock()
    monkeypatch.setattr(session_mod, "SessionLocal", lambda: db)

    gen = session_mod.get_db()
    assert next(gen) is db
    gen.close()
    db.close.assert_called_once()


def test_session_sets_connect_timeout_for_postgres(monkeypatch):
    import sqlalchemy
    from pydantic import SecretStr

    import src.database.session as session_mod
    from src.config.settings import settings

    original_url = settings.database_url
    try:
        monkeypatch.setattr(
            settings,
            "database_url",
            SecretStr("postgresql://user:[REDACTED]@localhost:5432/db"),
        )
        create_engine = Mock(return_value=Mock())
        monkeypatch.setattr(sqlalchemy, "create_engine", create_engine)

        importlib.reload(session_mod)

        assert session_mod.engine_kwargs["connect_args"] == {"connect_timeout": 3}
        assert create_engine.call_args.kwargs["connect_args"] == {"connect_timeout": 3}
    finally:
        monkeypatch.setattr(settings, "database_url", original_url)
        importlib.reload(session_mod)
