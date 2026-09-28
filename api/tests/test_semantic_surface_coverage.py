from __future__ import annotations

from src.database.semantic_surface import (
    _digest_rows,
    _query_rows,
    capture_semantic_surface,
)


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.ordered_by = None

    def order_by(self, *columns):
        self.ordered_by = columns
        return self

    def all(self):
        return self.rows


class _FakeSession:
    def __init__(self):
        self.calls = []

    def query(self, *columns):
        self.calls.append(columns)
        return _FakeQuery([(len(self.calls), "row")])


def test_semantic_surface_snapshot_is_deterministic() -> None:
    assert _digest_rows([[1, "a"]]) == _digest_rows([[1, "a"]])

    session = _FakeSession()
    assert _query_rows(session, object, "code", "label") == [[1, "row"]]

    snapshot = capture_semantic_surface(_FakeSession())

    assert len(snapshot["tables"]) == 12
    assert snapshot["tables"]["indicators"]["count"] == 1
    assert (
        snapshot["overall_digest"]
        == capture_semantic_surface(_FakeSession())["overall_digest"]
    )
