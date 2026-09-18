"""Incremental changed-feed cursor helpers for downstream synchronization."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime

CHANGE_FEED_DATASETS = {"indicators", "mappings", "values"}


@dataclass(frozen=True)
class ChangeFeedCursor:
    occurred_at: datetime
    dataset: str
    event_id: str


def encode_change_feed_cursor(
    *, occurred_at: datetime, dataset: str, event_id: str
) -> str:
    payload = {
        "occurred_at": occurred_at.isoformat(),
        "dataset": dataset,
        "event_id": event_id,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_change_feed_cursor(cursor: str) -> ChangeFeedCursor:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        dataset = str(payload["dataset"])
        if dataset not in CHANGE_FEED_DATASETS:
            raise ValueError("unsupported dataset")
        return ChangeFeedCursor(
            occurred_at=datetime.fromisoformat(payload["occurred_at"]),
            dataset=dataset,
            event_id=str(payload["event_id"]),
        )
    except Exception as exc:  # pragma: no cover - exercised through API contract
        raise ValueError("Invalid changed-feed cursor") from exc
