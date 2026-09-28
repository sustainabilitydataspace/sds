"""Regression tests for the DB-backed unit bootstrap helper."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.database import bootstrap_units as bootstrap_units_mod
from src.database.models import ConversionRule, Unit, UnitCategory


class _FakeQuery:
    def __init__(self, session: "_FakeSession", model):
        self._session = session
        self._model = model
        self._criteria = []

    def count(self) -> int:
        return len(self._session._rows[self._model])

    def filter(self, *criteria):
        self._criteria.extend(criteria)
        return self

    def first(self):
        for row in self._session._rows[self._model]:
            if all(_matches(row, criterion) for criterion in self._criteria):
                return row
        return None


def _matches(row, criterion) -> bool:
    left = getattr(criterion, "left", None)
    right = getattr(criterion, "right", None)
    key = getattr(left, "key", getattr(left, "name", None))
    expected = getattr(right, "value", right)
    if key is None:
        return True
    return getattr(row, key) == expected


class _FakeSession:
    def __init__(self):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
        self._rows = {
            UnitCategory: [],
            Unit: [],
            ConversionRule: [],
        }
        self._pending = []
        self._next_ids = {
            UnitCategory: 1,
            Unit: 1,
            ConversionRule: 1,
        }
        self.commits = 0

    def query(self, model):
        return _FakeQuery(self, model)

    def add(self, row):
        self._pending.append(row)

    def flush(self):
        self._persist_pending()

    def commit(self):
        self._persist_pending()
        self.commits += 1

    def rollback(self):
        self._pending.clear()

    def _persist_pending(self):
        while self._pending:
            row = self._pending.pop(0)
            row_type = type(row)
            if getattr(row, "id", None) in (None, 0):
                row.id = self._next_ids[row_type]
                self._next_ids[row_type] += 1
            if row not in self._rows[row_type]:
                self._rows[row_type].append(row)


def _write_units_payload(path: Path) -> None:
    payload = {
        "version": "1.0.0",
        "categories": {
            "mass": {
                "base_unit": "kg",
                "description": "Mass units",
                "special_conversions": False,
                "units": {
                    "kg": {
                        "symbol": "kg",
                        "name": "kilogram",
                        "conversion_factor": 1,
                        "aliases": ["kilogram"],
                        "metadata": {
                            "source": "fixture",
                            "dimension_vector": {"mass": 1},
                            "source_system": "SI/BIPM",
                            "source_version": "fixture-v1",
                            "valid_from": "2024-01-01",
                        },
                    },
                    "g": {
                        "symbol": "g",
                        "name": "gram",
                        "conversion_factor": 0.001,
                        "aliases": ["gram"],
                        "metadata": {
                            "source": "fixture",
                            "dimension_vector": {"mass": 1},
                            "source_system": "SI/BIPM",
                            "source_version": "fixture-v1",
                        },
                    },
                },
            }
        },
        "custom_conversion_rules": [
            {
                "from_unit": "kg",
                "to_unit": "g",
                "formula": "value * 1000",
                "reverse_formula": "value / 1000",
                "conditions": {"category": "mass"},
                "metadata": {
                    "description": "mass scaling",
                    "priority": 10,
                    "source_system": "fixture",
                    "source_version": "fixture-v1",
                },
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_bootstrap_units_if_empty_seeds_and_is_idempotent(tmp_path):
    session = _FakeSession()
    units_json_path = tmp_path / "units_database.json"
    _write_units_payload(units_json_path)

    first = bootstrap_units_mod.bootstrap_units_if_empty(
        session, units_json_path=units_json_path
    )

    assert first["categories_before"] == 0
    assert first["units_before"] == 0
    assert first["rules_before"] == 0
    assert first["categories_seeded"] == 1
    assert first["units_seeded"] == 2
    assert first["rules_seeded"] == 1
    assert first["categories_updated"] == 0
    assert first["units_updated"] == 0
    assert first["rules_updated"] == 0
    assert first["categories_total"] == 1
    assert first["units_total"] == 2
    assert first["rules_total"] == 1

    second = bootstrap_units_mod.bootstrap_units_if_empty(
        session, units_json_path=units_json_path
    )

    assert second["categories_before"] == 1
    assert second["units_before"] == 2
    assert second["rules_before"] == 1
    assert second["categories_seeded"] == 0
    assert second["units_seeded"] == 0
    assert second["rules_seeded"] == 0
    assert second["categories_updated"] == 0
    assert second["units_updated"] == 0
    assert second["rules_updated"] == 0
    assert second["categories_total"] == 1
    assert second["units_total"] == 2
    assert second["rules_total"] == 1

    assert [row.name for row in session._rows[UnitCategory]] == ["mass"]
    assert sorted(row.symbol for row in session._rows[Unit]) == ["g", "kg"]
    kg = next(row for row in session._rows[Unit] if row.symbol == "kg")
    assert kg.dimension_vector == {"mass": 1}
    assert kg.source_system == "SI/BIPM"
    assert kg.source_version == "fixture-v1"
    assert kg.valid_from.isoformat() == "2024-01-01"
    assert [(row.from_unit, row.to_unit) for row in session._rows[ConversionRule]] == [
        ("kg", "g")
    ]
    rule = session._rows[ConversionRule][0]
    assert rule.priority == 10
    assert rule.source_system == "fixture"


def test_bootstrap_units_if_empty_missing_file_returns_current_counts(tmp_path):
    session = _FakeSession()
    missing_path = tmp_path / "missing_units_database.json"

    summary = bootstrap_units_mod.bootstrap_units_if_empty(
        session, units_json_path=missing_path
    )

    assert summary["categories_before"] == 0
    assert summary["units_before"] == 0
    assert summary["rules_before"] == 0
    assert summary["categories_seeded"] == 0
    assert summary["units_seeded"] == 0
    assert summary["rules_seeded"] == 0
    assert summary["categories_total"] == 0
    assert summary["units_total"] == 0
    assert summary["rules_total"] == 0
    assert session._rows[UnitCategory] == []
    assert session._rows[Unit] == []
    assert session._rows[ConversionRule] == []


def test_bootstrap_units_helper_edges_and_postgres_lock():
    class _PostgresSession:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def __init__(self):
            self.executed = []

        def execute(self, statement, params):
            self.executed.append((str(statement), params))

    session = _PostgresSession()

    lock_key = bootstrap_units_mod._with_postgres_lock(session)
    bootstrap_units_mod._release_postgres_lock(session, lock_key)

    assert lock_key == bootstrap_units_mod.UNITS_BOOTSTRAP_LOCK_KEY
    assert len(session.executed) == 2
    target = SimpleNamespace(value="old")
    assert bootstrap_units_mod._update_if_changed(target, value="new") == 1
    assert target.value == "new"
    assert bootstrap_units_mod._parse_optional_date("2024-01-02").isoformat() == (
        "2024-01-02"
    )
    assert (
        bootstrap_units_mod._parse_optional_date(
            bootstrap_units_mod._parse_optional_date("2024-01-02")
        ).isoformat()
        == "2024-01-02"
    )


def test_bootstrap_units_skips_invalid_rule_and_rolls_back_on_commit_failure(
    tmp_path,
):
    units_json_path = tmp_path / "units_database.json"
    _write_units_payload(units_json_path)
    payload = json.loads(units_json_path.read_text(encoding="utf-8"))
    payload["custom_conversion_rules"].insert(0, "not-an-object")
    units_json_path.write_text(json.dumps(payload), encoding="utf-8")

    session = _FakeSession()
    summary = bootstrap_units_mod.bootstrap_units_if_empty(
        session, units_json_path=units_json_path
    )
    assert summary["rules_seeded"] == 1

    class _CommitFailingSession(_FakeSession):
        def commit(self):
            raise RuntimeError("commit failed")

    failing = _CommitFailingSession()
    try:
        bootstrap_units_mod.bootstrap_units_if_empty(
            failing, units_json_path=units_json_path
        )
    except RuntimeError as exc:
        assert "commit failed" in str(exc)
    else:
        raise AssertionError("commit failure should propagate")
    assert failing._pending == []
