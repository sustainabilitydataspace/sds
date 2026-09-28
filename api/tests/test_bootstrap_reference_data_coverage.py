from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_reference_data_load_records_handles_missing_invalid_and_valid_payload(
    tmp_path,
):
    from src.database import bootstrap_reference_data as mod

    assert mod._load_records(tmp_path / "missing.json", "items") == []

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"items": {"not": "a list"}}), encoding="utf-8")
    assert mod._load_records(invalid, "items") == []

    valid = tmp_path / "valid.json"
    valid.write_text(
        json.dumps({"items": [{"id": "one"}, "skip", {"id": "two"}]}),
        encoding="utf-8",
    )
    assert mod._load_records(valid, "items") == [{"id": "one"}, {"id": "two"}]


def test_reference_data_bootstrap_seeds_empty_database_and_releases_lock(monkeypatch):
    from src.database import bootstrap_reference_data as mod

    events: list[str] = []

    class _Repo:
        def __init__(self, db, *, kind):
            self.kind = kind
            self.counts = [0, 2 if kind == "indicators" else 3]

        def count(self):
            return self.counts.pop(0)

        def bulk_upsert(self, records):
            events.append(f"{self.kind}:{len(records)}")
            return len(records)

    indicator_repo = _Repo(None, kind="indicators")
    mapping_repo = _Repo(None, kind="mappings")
    monkeypatch.setattr(mod, "IndicatorRepository", lambda _db: indicator_repo)
    monkeypatch.setattr(mod, "StandardMappingRepository", lambda _db: mapping_repo)
    monkeypatch.setattr(
        mod,
        "_load_records",
        lambda _path, key: (
            [{"id": "a"}, {"id": "b"}]
            if key == "indicators"
            else [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]
        ),
    )

    existing_query = MagicMock()
    existing_query.filter_by.return_value = existing_query
    existing_query.first.side_effect = [
        SimpleNamespace(id="ESRS"),
        None,
        None,
        None,
        None,
        None,
        None,
    ]
    db = MagicMock()
    db.bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    db.query.return_value = existing_query

    summary = mod.bootstrap_reference_data_if_empty(db)

    assert summary == {
        "indicators_before": 0,
        "mappings_before": 0,
        "indicators_seeded": 2,
        "mappings_seeded": 3,
        "standards_created": 6,
        "indicators_total": 2,
        "mappings_total": 3,
    }
    assert events == ["indicators:2", "mappings:3"]
    assert db.execute.call_count == 2


def test_reference_data_bootstrap_skips_non_empty_repos_without_lock(monkeypatch):
    from src.database import bootstrap_reference_data as mod

    class _Repo:
        def __init__(self, count):
            self._count = count

        def count(self):
            return self._count

        def bulk_upsert(self, _records):
            raise AssertionError("should not seed non-empty repo")

    monkeypatch.setattr(mod, "IndicatorRepository", lambda _db: _Repo(5))
    monkeypatch.setattr(mod, "StandardMappingRepository", lambda _db: _Repo(7))

    db = MagicMock()
    db.bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    summary = mod.bootstrap_reference_data_if_empty(db)

    assert summary["indicators_before"] == 5
    assert summary["mappings_before"] == 7
    assert summary["indicators_seeded"] == 0
    assert summary["mappings_seeded"] == 0
    db.execute.assert_not_called()
