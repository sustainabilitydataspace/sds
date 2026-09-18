"""Cursor pagination helpers for values APIs."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date, datetime


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
