"""Database bootstrap (seed initial admin) — offline-safe unit tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from src.config.settings import settings


def test_bootstrap_admin_raises_when_password_missing(monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_admin_username", "admin")
    monkeypatch.setattr(settings, "bootstrap_admin_password", None)

    db = MagicMock()
    db.query.return_value.count.return_value = 0

    from src.database.bootstrap import bootstrap_default_admin

    with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        bootstrap_default_admin(db)


def test_bootstrap_admin_skips_when_users_exist(monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_admin_username", "admin")
    monkeypatch.setattr(settings, "bootstrap_admin_password", SecretStr("secret"))

    db = MagicMock()
    db.query.return_value.count.return_value = 1

    from src.database.bootstrap import bootstrap_default_admin

    assert bootstrap_default_admin(db) is False


def test_bootstrap_admin_creates_when_empty(monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_admin_username", "admin")
    monkeypatch.setattr(settings, "bootstrap_admin_password", SecretStr("secret"))

    db = MagicMock()
    db.query.return_value.count.return_value = 0

    # Avoid bcrypt in this unit test.
    monkeypatch.setattr(
        "src.database.bootstrap.jwt_handler.hash_password", lambda _pwd: "hash"
    )

    from src.database.bootstrap import bootstrap_default_admin

    assert bootstrap_default_admin(db) is True
    db.add.assert_called_once()
    db.commit.assert_called()
