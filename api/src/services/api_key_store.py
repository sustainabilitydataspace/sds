"""API key persistence backends (DB or in-memory)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Sequence

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.auth.jwt_handler import jwt_handler
from src.auth.models import APIKeyCreate, APIKeyInfoResponse, APIKeyResponse, Permission
from src.config.settings import settings
from src.database.repositories.api_key_repository import APIKeyRepository
from src.database.session import get_db_optional


@dataclass(frozen=True)
class ResolvedAPIKey:
    key_id: str
    user_id: str
    permissions: list[Permission]


@dataclass
class _StoredAPIKey:
    id: str
    user_id: str
    name: str
    description: Optional[str]
    secret_hash: str
    created_at: datetime
    expires_at: Optional[datetime]
    last_used: Optional[datetime]
    is_active: bool
    permissions: list[Permission]


def _resolve_expires_at(
    requested_expires_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    if requested_expires_at is not None:
        return _as_utc_aware(requested_expires_at)

    expire_days = getattr(settings, "api_key_expire_days", 365)
    if expire_days <= 0:
        return None

    anchor = now or datetime.now(timezone.utc)
    return anchor + timedelta(days=expire_days)


def _as_utc_aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_expired(expires_at: Optional[datetime]) -> bool:
    expires_at_utc = _as_utc_aware(expires_at)
    return bool(expires_at_utc and datetime.now(timezone.utc) > expires_at_utc)


# Avoid a DB write+commit on every API-key-authenticated request (contention/DoS):
# only refresh last_used when the stored value is stale beyond this window.
_LAST_USED_DEBOUNCE_SECONDS = 300


def _should_update_last_used(previous: Optional[datetime], now: datetime) -> bool:
    if previous is None:
        return True
    previous_utc = _as_utc_aware(previous)
    if previous_utc is None:
        return True
    return (now - previous_utc).total_seconds() >= _LAST_USED_DEBOUNCE_SECONDS


def _split_api_key(api_key: str) -> Optional[tuple[str, str]]:
    """Parse API key into (key_id, secret).

    Format: sds_<key_id>.<secret>
    - key_id uses hex (no dots)
    - secret is urlsafe (may include '_' but not '.')
    """
    if not api_key or not api_key.startswith("sds_"):
        return None
    remainder = api_key[len("sds_") :]
    if "." not in remainder:
        return None
    key_id, secret = remainder.split(".", 1)
    if not key_id or not secret:
        return None
    return key_id, secret


class InMemoryAPIKeyStore:
    """Offline-safe API key store (ephemeral, per-process)."""

    def __init__(self):
        self._keys: Dict[str, _StoredAPIKey] = {}

    def create_api_key(self, *, user_id: str, request: APIKeyCreate) -> APIKeyResponse:
        key_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        raw_key = f"sds_{key_id}.{secret}"

        now = datetime.now(timezone.utc)
        expires_at = _resolve_expires_at(request.expires_at, now=now)
        stored = _StoredAPIKey(
            id=key_id,
            user_id=user_id,
            name=request.name,
            description=request.description,
            secret_hash=jwt_handler.hash_api_key(secret),
            created_at=now,
            expires_at=expires_at,
            last_used=None,
            is_active=True,
            permissions=list(request.permissions),
        )
        self._keys[key_id] = stored

        return APIKeyResponse(
            id=key_id,
            name=stored.name,
            description=stored.description,
            key=raw_key,
            created_at=stored.created_at,
            expires_at=stored.expires_at,
            permissions=stored.permissions,
        )

    def list_api_keys(self, *, user_id: str) -> list[APIKeyInfoResponse]:
        rows = [k for k in self._keys.values() if k.user_id == user_id]
        rows.sort(key=lambda r: (r.created_at, r.id))
        return [
            APIKeyInfoResponse(
                id=r.id,
                name=r.name,
                description=r.description,
                created_at=r.created_at,
                expires_at=r.expires_at,
                last_used=r.last_used,
                is_active=r.is_active,
                permissions=r.permissions,
            )
            for r in rows
        ]

    def revoke_api_key(self, *, user_id: str, key_id: str) -> bool:
        stored = self._keys.get(key_id)
        if not stored or stored.user_id != user_id:
            return False
        stored.is_active = False
        return True

    def authenticate(self, api_key: str) -> Optional[ResolvedAPIKey]:
        parsed = _split_api_key(api_key)
        if not parsed:
            return None
        key_id, secret = parsed
        stored = self._keys.get(key_id)
        if not stored or not stored.is_active:
            return None
        if _is_expired(stored.expires_at):
            return None
        if not jwt_handler.verify_api_key(secret, stored.secret_hash):
            return None
        stored.last_used = datetime.now(timezone.utc)
        return ResolvedAPIKey(
            key_id=key_id, user_id=stored.user_id, permissions=list(stored.permissions)
        )


class DatabaseAPIKeyStore:
    """PostgreSQL-backed API key store."""

    def __init__(self, db: Session):
        self._repo = APIKeyRepository(db)

    def create_api_key(self, *, user_id: str, request: APIKeyCreate) -> APIKeyResponse:
        key_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        raw_key = f"sds_{key_id}.{secret}"
        expires_at = _resolve_expires_at(request.expires_at)

        record = self._repo.create_api_key(
            key_id=key_id,
            user_id=user_id,
            name=request.name,
            description=request.description,
            secret_hash=jwt_handler.hash_api_key(secret),
            permissions=[p.value for p in request.permissions],
            expires_at=expires_at,
        )

        return APIKeyResponse(
            id=record.id,
            name=record.name,
            description=record.description,
            key=raw_key,
            created_at=record.created_at,
            expires_at=_as_utc_aware(getattr(record, "expires_at", None) or expires_at),
            permissions=list(request.permissions),
        )

    def list_api_keys(self, *, user_id: str) -> list[APIKeyInfoResponse]:
        rows = self._repo.list_api_keys_for_user(user_id)
        rows.sort(key=lambda r: (r.created_at, r.id))
        out: list[APIKeyInfoResponse] = []
        for r in rows:
            perms_raw = r.permissions if isinstance(r.permissions, list) else []
            perms: list[Permission] = []
            for p in perms_raw:
                try:
                    perms.append(Permission(p))
                except Exception:
                    continue
            out.append(
                APIKeyInfoResponse(
                    id=r.id,
                    name=r.name,
                    description=r.description,
                    created_at=r.created_at,
                    expires_at=_as_utc_aware(r.expires_at),
                    last_used=r.last_used,
                    is_active=bool(r.is_active),
                    permissions=perms,
                )
            )
        return out

    def revoke_api_key(self, *, user_id: str, key_id: str) -> bool:
        record = self._repo.get_api_key_by_id(key_id)
        if not record or record.user_id != user_id:
            return False
        return self._repo.revoke_api_key(key_id)

    def authenticate(self, api_key: str) -> Optional[ResolvedAPIKey]:
        parsed = _split_api_key(api_key)
        if not parsed:
            return None
        key_id, secret = parsed
        record = self._repo.get_api_key_by_id(key_id)
        if not record or not bool(record.is_active):
            return None
        if _is_expired(record.expires_at):
            return None
        if not jwt_handler.verify_api_key(secret, record.secret_hash):
            return None

        now = datetime.now(timezone.utc)
        if _should_update_last_used(getattr(record, "last_used", None), now):
            self._repo.set_last_used(key_id, last_used=now)

        perms_raw = record.permissions if isinstance(record.permissions, list) else []
        perms: list[Permission] = []
        for p in perms_raw:
            try:
                perms.append(Permission(p))
            except Exception:
                continue
        return ResolvedAPIKey(key_id=key_id, user_id=record.user_id, permissions=perms)


def get_api_key_store(
    request: Request,
    db: Optional[Session] = Depends(get_db_optional),
):
    """Dependency that returns the configured API key store (DB when required)."""
    if settings.require_database:
        if db is None:
            raise HTTPException(status_code=503, detail="Database session unavailable")
        return DatabaseAPIKeyStore(db)

    store = getattr(request.app.state, "api_key_store", None)
    if store is None:
        store = InMemoryAPIKeyStore()
        request.app.state.api_key_store = store
    return store
