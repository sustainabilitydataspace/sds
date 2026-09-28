"""Public values cursors must reject malformed and oversized envelopes."""

from datetime import date, datetime, timezone

import pytest

from src.services.value_pagination import (
    _base64url_decode,
    decode_scoped_value_cursor,
    encode_scoped_value_cursor,
    encode_value_cursor,
)
from src.services.value_store import InMemoryValueStore


def test_public_cursor_rejects_invalid_and_noncanonical_base64() -> None:
    for component in ("!", "Zh"):
        with pytest.raises(ValueError, match="Invalid values cursor"):
            _base64url_decode(component)


def test_public_cursor_rejects_oversized_issue_and_replay() -> None:
    position = encode_value_cursor(
        period=date(2024, 1, 1),
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        value_id="value-1",
    )
    scope = {"tenant": "tenant-a"}
    secret = "only-for-this-test"
    with pytest.raises(ValueError, match="Invalid values cursor"):
        encode_scoped_value_cursor(
            position,
            scope=scope,
            filters={"concept": "x" * 5000},
            secret=secret,
        )
    with pytest.raises(ValueError, match="Invalid values cursor"):
        decode_scoped_value_cursor(
            "x" * 4097,
            scope=scope,
            filters={"concept": None},
            secret=secret,
        )


def test_memory_first_page_and_cursor_share_descending_order() -> None:
    store = InMemoryValueStore()
    for year in (2024, 2026, 2025):
        store.create(
            value_id=f"year-{year}",
            concept="test",
            entity="test",
            period=date(year, 1, 1),
            value=year,
            unit="kg",
            original_unit=None,
            conversion_applied=False,
            metadata=None,
        )
    filters = dict(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
    )
    first, total = store.list(**filters, limit=1, offset=0)
    assert total == 3
    assert first[0].id == "year-2026"
    cursor = encode_value_cursor(
        period=first[0].period,
        created_at=first[0].created_at,
        value_id=first[0].id,
    )
    second, total, next_cursor, more = store.list_cursor(
        **filters, limit=1, cursor=cursor
    )
    third, total, _, more = store.list_cursor(**filters, limit=1, cursor=next_cursor)
    assert [first[0].id, second[0].id, third[0].id] == [
        "year-2026",
        "year-2025",
        "year-2024",
    ]
    assert more is False
