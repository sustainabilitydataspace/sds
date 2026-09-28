"""User repository unit tests (DB mocked; offline-safe)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.database.models import UserAccount
from src.database.repositories.user_repository import UserRepository


def test_user_repository_create_user_commits_and_refreshes():
    db = MagicMock()
    repo = UserRepository(db)

    record = repo.create_user(
        user_id="user_admin",
        username="admin",
        email="admin@example.com",
        full_name="Admin",
        company_id="company_001",
        role="admin",
        password_hash="hash",
        is_active=True,
    )

    assert isinstance(record, UserAccount)
    assert record.username == "admin"
    db.add.assert_called_once()
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(record)


def test_user_repository_get_user_by_username_uses_query_chain():
    record = SimpleNamespace(id="user_admin", username="admin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = record
    repo = UserRepository(db)

    assert repo.get_user_by_username("admin") is record
    db.query.assert_called_once_with(UserAccount)


def test_user_repository_update_user_updates_known_attributes_and_commits():
    record = SimpleNamespace(
        id="user_admin", username="admin", email="old@example.com", full_name=None
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = record
    db.query.return_value.filter.return_value.update.return_value = 1
    repo = UserRepository(db)

    updated = repo.update_user("user_admin", email="new@example.com", full_name="Admin")

    assert updated is record
    db.query.return_value.filter.return_value.update.assert_called_once_with(
        {"email": "new@example.com", "full_name": "Admin"},
        synchronize_session=False,
    )
    db.commit.assert_called()
    db.refresh.assert_called_with(record)


def test_user_repository_update_user_returns_none_when_missing():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    repo = UserRepository(db)

    assert repo.update_user("missing", email="new@example.com") is None
