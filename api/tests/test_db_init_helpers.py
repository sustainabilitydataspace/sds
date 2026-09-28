"""Tests for database initialization helpers (mocked engine)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.database import init_db as init_db_mod


def test_database_package_preserves_lazy_session_exports():
    import src.database as database
    from src.database.session import SessionLocal

    assert database.SessionLocal is SessionLocal


def test_schema_init_helpers_do_not_initialize_application_settings_on_import():
    api_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import src.database.init_db; "
            "assert 'src.config.settings' not in sys.modules; "
            "assert 'src.database.session' not in sys.modules",
        ],
        cwd=api_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_init_db_calls_migration_helper(monkeypatch):
    engine = MagicMock()
    conn = engine.connect.return_value.__enter__.return_value

    ensure_database_schema = MagicMock()

    monkeypatch.setattr(init_db_mod, "engine", engine)
    monkeypatch.setattr(init_db_mod, "ensure_database_schema", ensure_database_schema)

    init_db_mod.init_db()

    engine.connect.assert_called_once()
    ensure_database_schema.assert_called_once_with(conn)


def test_init_db_resolves_default_engine_only_when_called(monkeypatch):
    application_engine = MagicMock()
    conn = application_engine.connect.return_value.__enter__.return_value
    schema_check = MagicMock()
    monkeypatch.setattr(init_db_mod, "engine", None)
    monkeypatch.setattr(init_db_mod, "ensure_database_schema", schema_check)
    monkeypatch.setitem(
        sys.modules,
        "src.database.session",
        SimpleNamespace(engine=application_engine),
    )

    init_db_mod.init_db()

    application_engine.connect.assert_called_once()
    schema_check.assert_called_once_with(conn)


def test_ping_db_executes_select_one(monkeypatch):
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    monkeypatch.setattr(init_db_mod, "engine", engine)

    init_db_mod.ping_db()

    engine.connect.assert_called_once()
    assert conn.execute.called
    sql = str(conn.execute.call_args.args[0])
    assert "SELECT 1" in sql.upper()
