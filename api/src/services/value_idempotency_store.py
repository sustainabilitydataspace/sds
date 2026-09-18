"""Idempotency helpers for value-write APIs."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.config.settings import settings
from src.database.repositories.value_idempotency_repository import (
    ValueIdempotencyRepository,
)
from src.database.session import get_db_optional


@dataclass(frozen=True)
class IdempotencyReplay:
    status_code: int
    body: dict[str, Any]


@dataclass(frozen=True)
class IdempotencyClaim:
    record_id: int


class InMemoryValueIdempotencyStore:
    """In-memory request idempotency cache for isolated tests."""

    def __init__(self):
        self._records: Dict[Tuple[str, str, str], dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._next_record_id = 1

    def claim(
        self,
        *,
        user_id: str,
        scope: str,
        idempotency_key: Optional[str],
        request_hash: str,
        ttl_seconds: int = 86400,
    ) -> Optional[IdempotencyReplay | IdempotencyClaim]:
        if not idempotency_key:
            return None

        key = (user_id, scope, idempotency_key)
        now = datetime.now(timezone.utc)
        with self._lock:
            existing = self._records.get(key)
            if existing and existing["expires_at"] <= now:
                self._records.pop(key, None)
                existing = None

            if existing:
                if existing["request_hash"] != request_hash:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency key reuse with different payload",
                    )
                if existing["state"] == "in_progress":
                    raise HTTPException(
                        status_code=409, detail="Idempotency key is already in progress"
                    )
                return IdempotencyReplay(
                    status_code=existing["response_status"],
                    body=existing["response_body"],
                )

            record_id = self._next_record_id
            self._next_record_id += 1
            self._records[key] = {
                "id": record_id,
                "request_hash": request_hash,
                "state": "in_progress",
                "response_status": None,
                "response_body": None,
                "expires_at": now + timedelta(seconds=ttl_seconds),
            }
            return IdempotencyClaim(record_id=record_id)

    def complete(
        self,
        *,
        user_id: str,
        scope: str,
        idempotency_key: str,
        record_id: int,
        response_status: int,
        response_body: dict[str, Any],
    ) -> None:
        key = (user_id, scope, idempotency_key)
        with self._lock:
            record = self._records.get(key)
            if record is None or record["id"] != record_id:
                return
            record["state"] = "completed"
            record["response_status"] = response_status
            record["response_body"] = response_body

    def abandon(
        self,
        *,
        user_id: str,
        scope: str,
        idempotency_key: str,
        record_id: int,
    ) -> None:
        key = (user_id, scope, idempotency_key)
        with self._lock:
            record = self._records.get(key)
            if record is None or record["id"] != record_id:
                return
            self._records.pop(key, None)


class DatabaseValueIdempotencyStore:
    """PostgreSQL-backed request idempotency for values APIs."""

    def __init__(self, db: Session):
        self._db = db
        self._repo = ValueIdempotencyRepository(db)

    @staticmethod
    def _as_aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _active_existing_record(self, existing, now: datetime, *, commit: bool = True):
        if existing and self._as_aware_utc(existing.expires_at) <= now:
            self._repo.delete(record_id=existing.id, commit=commit)
            return None
        return existing

    def _create_claim(self, *, commit: bool, **claim_data):
        if commit:
            return self._repo.create_claim(**claim_data, commit=True)
        with self._db.begin_nested():
            return self._repo.create_claim(**claim_data, commit=False)

    @staticmethod
    def _resolve_existing(
        existing, request_hash: str
    ) -> IdempotencyReplay | IdempotencyClaim:
        if existing.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key reuse with different payload",
            )
        if existing.state == "in_progress":
            raise HTTPException(
                status_code=409, detail="Idempotency key is already in progress"
            )
        return IdempotencyReplay(
            status_code=existing.response_status or 200,
            body=existing.response_body or {},
        )

    def claim(
        self,
        *,
        user_id: str,
        scope: str,
        idempotency_key: Optional[str],
        request_hash: str,
        ttl_seconds: int = 86400,
        commit: bool = True,
    ) -> Optional[IdempotencyReplay | IdempotencyClaim]:
        if not idempotency_key:
            return None

        existing = self._repo.get(
            user_id=user_id, scope=scope, idempotency_key=idempotency_key
        )
        now = datetime.now(timezone.utc)
        existing = self._active_existing_record(existing, now, commit=commit)

        if existing:
            return self._resolve_existing(existing, request_hash)

        try:
            record = self._create_claim(
                user_id=user_id,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                ttl_seconds=ttl_seconds,
                commit=commit,
            )
        except IntegrityError:
            if commit:
                self._repo.rollback()
            existing = self._repo.get(
                user_id=user_id, scope=scope, idempotency_key=idempotency_key
            )
            existing = self._active_existing_record(
                existing, datetime.now(timezone.utc), commit=commit
            )
            if existing:
                return self._resolve_existing(existing, request_hash)
            record = self._create_claim(
                user_id=user_id,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                ttl_seconds=ttl_seconds,
                commit=commit,
            )
        return IdempotencyClaim(record_id=record.id)

    def complete(
        self,
        *,
        user_id: str,
        scope: str,
        idempotency_key: str,
        record_id: int,
        response_status: int,
        response_body: dict[str, Any],
        commit: bool = True,
    ) -> None:
        self._repo.complete(
            record_id=record_id,
            response_status=response_status,
            response_body=response_body,
            commit=commit,
        )

    def abandon(
        self,
        *,
        user_id: str,
        scope: str,
        idempotency_key: str,
        record_id: int,
        commit: bool = True,
    ) -> None:
        self._repo.delete(record_id=record_id, commit=commit)


def build_request_hash(payload: Any) -> str:
    """Build a deterministic hash for an idempotent write payload."""
    normalized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def get_value_idempotency_store(
    request: Request,
    db: Optional[Session] = Depends(get_db_optional),
):
    """Dependency that returns the configured idempotency store for values APIs."""
    if settings.require_database:
        if db is None:
            raise HTTPException(status_code=503, detail="Database session unavailable")
        return DatabaseValueIdempotencyStore(db)

    store = getattr(request.app.state, "value_idempotency_store", None)
    if store is None:
        store = InMemoryValueIdempotencyStore()
        request.app.state.value_idempotency_store = store
    return store
