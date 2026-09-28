"""Tests for DB-backed token revocation (F-003)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth.jwt_handler import jwt_handler
from src.database.models import RevokedToken


class TestTokenRevocationPersistent:
    def test_revoke_token_persistent_writes_to_db(self):
        token = "test-token-string"
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        jwt_handler.revoke_token_persistent(token, expires, db)
        db.add.assert_called_once()
        db.commit.assert_called_once()
        added_obj = db.add.call_args[0][0]
        assert added_obj.token_hash == jwt_handler._token_fingerprint(token)
        assert added_obj.expires_at == expires

    def test_persistent_revoke_writes_even_when_memory_cache_is_already_set(self):
        token = "pre-cached-token"
        fingerprint = jwt_handler._token_fingerprint(token)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        jwt_handler._revoked_tokens.add(fingerprint)
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        try:
            jwt_handler.revoke_token_persistent(token, expires, db)
        finally:
            jwt_handler._revoked_tokens.discard(fingerprint)

        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_is_revoked_persistent_checks_memory_first(self):
        token = "mem-cached-token"
        fingerprint = jwt_handler._token_fingerprint(token)
        jwt_handler._revoked_tokens.add(fingerprint)
        db = MagicMock()
        assert jwt_handler.is_revoked_persistent(token, db) is True
        db.query.assert_not_called()
        jwt_handler._revoked_tokens.discard(fingerprint)

    def test_is_revoked_persistent_falls_back_to_db(self):
        token = "db-only-token"
        fingerprint = jwt_handler._token_fingerprint(token)
        jwt_handler._revoked_tokens.discard(fingerprint)
        db = MagicMock()
        mock_revoked = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = mock_revoked
        assert jwt_handler.is_revoked_persistent(token, db) is True
        assert fingerprint in jwt_handler._revoked_tokens
        jwt_handler._revoked_tokens.discard(fingerprint)

    def test_is_revoked_persistent_returns_false_when_not_found(self):
        token = "never-revoked-token"
        fingerprint = jwt_handler._token_fingerprint(token)
        jwt_handler._revoked_tokens.discard(fingerprint)
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        assert jwt_handler.is_revoked_persistent(token, db) is False

    def test_refresh_consumption_is_durable_across_sessions_and_denies_replay(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        RevokedToken.__table__.create(engine)
        token = "synthetic-one-use-refresh-token"
        fingerprint = jwt_handler._token_fingerprint(token)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        try:
            with Session(engine) as first:
                assert (
                    jwt_handler.consume_token_persistent(token, expires, first) is True
                )
                assert (
                    first.query(RevokedToken).filter_by(token_hash=fingerprint).count()
                    == 1
                )
            jwt_handler._revoked_tokens.discard(fingerprint)
            with Session(engine) as second:
                assert (
                    jwt_handler.consume_token_persistent(token, expires, second)
                    is False
                )
                assert (
                    second.query(RevokedToken).filter_by(token_hash=fingerprint).count()
                    == 1
                )
        finally:
            jwt_handler._revoked_tokens.discard(fingerprint)
            engine.dispose()

    def test_refresh_consumption_rejects_unique_collision_without_issuing_authority(
        self,
    ):
        token = "synthetic-colliding-refresh-token"
        fingerprint = jwt_handler._token_fingerprint(token)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        db.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
        try:
            assert jwt_handler.consume_token_persistent(token, expires, db) is False
            db.add.assert_called_once()
            db.rollback.assert_called_once()
            assert fingerprint in jwt_handler._revoked_tokens
        finally:
            jwt_handler._revoked_tokens.discard(fingerprint)
