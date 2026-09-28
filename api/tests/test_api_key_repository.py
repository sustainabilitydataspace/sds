"""API key repository unit tests (DB mocked; offline-safe)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.database.models import APIKeyRecord
from src.database.repositories.api_key_repository import APIKeyRepository


def test_api_key_repository_create_api_key_commits_and_refreshes():
    db = MagicMock()
    repo = APIKeyRepository(db)

    record = repo.create_api_key(
        key_id="k1",
        user_id="admin",
        name="ci-key",
        description="test",
        secret_hash="hash",
        permissions=["convert_units"],
        expires_at=None,
    )

    assert isinstance(record, APIKeyRecord)
    assert record.id == "k1"
    assert record.user_id == "admin"
    db.add.assert_called_once()
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(record)


def test_api_key_repository_get_and_list_use_query_chain():
    record = SimpleNamespace(id="k1")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = record
    db.query.return_value.filter.return_value.all.return_value = [record]
    repo = APIKeyRepository(db)

    assert repo.get_api_key_by_id("k1") is record
    assert repo.list_api_keys_for_user("admin") == [record]


def test_api_key_repository_revoke_returns_false_when_missing():
    db = MagicMock()
    repo = APIKeyRepository(db)
    repo.get_api_key_by_id = MagicMock(return_value=None)

    assert repo.revoke_api_key("missing") is False


def test_api_key_repository_revoke_sets_is_active_and_commits():
    record = SimpleNamespace(id="k1", is_active=True)
    db = MagicMock()
    repo = APIKeyRepository(db)
    repo.get_api_key_by_id = MagicMock(return_value=record)

    assert repo.revoke_api_key("k1") is True
    assert record.is_active is False
    db.commit.assert_called_once()


def test_api_key_repository_set_last_used_sets_value_and_commits():
    record = SimpleNamespace(id="k1", last_used=None)
    db = MagicMock()
    repo = APIKeyRepository(db)
    repo.get_api_key_by_id = MagicMock(return_value=record)

    now = datetime.now(timezone.utc)
    assert repo.set_last_used("k1", last_used=now) is True
    assert record.last_used is now
    db.commit.assert_called_once()
