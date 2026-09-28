"""Cursor pagination helpers for values APIs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Optional

_PUBLIC_CURSOR_DOMAIN = b"sds-values-public-cursor-v1\0"
_MAX_PUBLIC_CURSOR_LENGTH = 4096


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    if not value or any(char not in alphabet for char in value):
        raise ValueError("Invalid values cursor")
    raw = base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    )
    if _base64url_encode(raw) != value:
        raise ValueError("Invalid values cursor")
    return raw


def encode_scoped_value_cursor(
    position: str,
    *,
    scope: Mapping[str, Optional[str]],
    filters: Mapping[str, Optional[str]],
    secret: str,
) -> str:
    """Issue a tenant/principal/filter-bound public continuation token."""

    decode_value_cursor(position)
    body = json.dumps(
        {
            "version": 1,
            "scope": dict(scope),
            "filters": dict(filters),
            "position": position,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hmac.new(
        secret.encode("utf-8"), _PUBLIC_CURSOR_DOMAIN + body, hashlib.sha256
    ).digest()
    token = f"{_base64url_encode(body)}.{_base64url_encode(digest)}"
    if len(token) > _MAX_PUBLIC_CURSOR_LENGTH:
        raise ValueError("Invalid values cursor")
    return token


def decode_scoped_value_cursor(
    token: str,
    *,
    scope: Mapping[str, Optional[str]],
    filters: Mapping[str, Optional[str]],
    secret: str,
) -> str:
    """Verify a public cursor before passing its position to a value store."""

    try:
        if not 0 < len(token) <= _MAX_PUBLIC_CURSOR_LENGTH:
            raise ValueError("Invalid values cursor")
        encoded_body, encoded_digest = token.split(".")
        body = _base64url_decode(encoded_body)
        supplied_digest = _base64url_decode(encoded_digest)
        expected_digest = hmac.new(
            secret.encode("utf-8"), _PUBLIC_CURSOR_DOMAIN + body, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(supplied_digest, expected_digest):
            raise ValueError("Invalid values cursor")
        payload = json.loads(body.decode("utf-8"))
        if (
            type(payload) is not dict
            or set(payload) != {"version", "scope", "filters", "position"}
            or type(payload["version"]) is not int
            or payload["version"] != 1
            or payload["scope"] != dict(scope)
            or payload["filters"] != dict(filters)
            or type(payload["position"]) is not str
        ):
            raise ValueError("Invalid values cursor")
        decode_value_cursor(payload["position"])
        return payload["position"]
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("Invalid values cursor") from exc


@dataclass(frozen=True)
class ValueCursor:
    period: date
    created_at: datetime
    value_id: str


def encode_value_cursor(*, period: date, created_at: datetime, value_id: str) -> str:
    payload = {
        "period": period.isoformat(),
        "created_at": created_at.isoformat(),
        "value_id": value_id,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_value_cursor(cursor: str) -> ValueCursor:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        return ValueCursor(
            period=date.fromisoformat(payload["period"]),
            created_at=datetime.fromisoformat(payload["created_at"]),
            value_id=str(payload["value_id"]),
        )
    except Exception as exc:  # pragma: no cover - error path exercised via API contract
        raise ValueError("Invalid values cursor") from exc


def is_after_cursor(
    cursor: ValueCursor, *, period: date, created_at: datetime, value_id: str
) -> bool:
    """Return True when the row appears after the cursor in DESC ordering."""
    row_key = (period, created_at, value_id)
    cursor_key = (cursor.period, cursor.created_at, cursor.value_id)
    return row_key < cursor_key
