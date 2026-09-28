from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import indicator_import as mod


def _register_csv(*rows: list[str]) -> str:
    header = ",".join(mod.REQUIRED_REGISTER_COLUMNS)
    return header + "\n" + "\n".join(",".join(row) for row in rows) + "\n"


def _row(
    identifier: str = "", *, title: str = "Title", source_row: str = "7"
) -> list[str]:
    values = {name: "" for name in mod.REQUIRED_REGISTER_COLUMNS}
    values.update(
        {
            "identifier": identifier,
            "title": title,
            "indicator": "Indicator",
            "description": "Description",
            "dimension": "E",
            "unitName": "tCO2e",
            "unitType": "GHGEmissions",
            "periodicity": "annual",
            "periodType": "annual",
            "sourceRef": "public-standard",
            "codeESRS": "",
            "codeGRI": "",
            "sourceRow": source_row,
            "valueType": "numeric",
        }
    )
    return [values[name] for name in mod.REQUIRED_REGISTER_COLUMNS]


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self.rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows
        self.flushed = False
        self.committed = False
        self.rolled_back = False

    def query(self, *_args, **_kwargs):
        return _Query(self.rows)

    def flush(self):
        self.flushed = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_indicator_identifier_normalization_uses_active_fallbacks():
    assert mod.canonicalize_indicator_identifier("plain-id") == "plain-id"
    assert (
        mod.canonicalize_identifier_for_row({"codeESRS": "E1-6 07"})
        == "urn:sds:reg:esrs:e1_6_07"
    )
    assert (
        mod.canonicalize_identifier_for_row({"codeGRI": "305-1"})
        == "urn:sds:reg:gri:305_1"
    )
    assert (
        mod.canonicalize_identifier_for_row(
            {"framework": "GHG Protocol", "title": "Scope 3"}
        )
        == "urn:sds:reg:ghgprotocol:scope_3"
    )
    assert mod.canonicalize_identifier_for_row({"identifier": "raw"}) == "raw"

    normalized = mod.normalize_indicator_row(
        {
            "identifier": "urn:sds:reg:ESRS:E1-6 07",
            "title": "Title",
            "sourceRow": "not-an-int",
        }
    )
    assert normalized["record"]["identifier"] == "urn:sds:reg:esrs:e1_6_07"
    assert normalized["record"]["source_row"] is None
    assert normalized["normalized"] is True


def test_indicator_csv_text_and_file_contract_errors(tmp_path: Path):
    with pytest.raises(mod.IndicatorCsvContractError, match="no header"):
        mod.load_indicator_rows_from_text("")
    with pytest.raises(mod.IndicatorCsvContractError, match="duplicate columns"):
        mod.load_indicator_rows_from_text("identifier,identifier\n1,2\n")
    with pytest.raises(mod.IndicatorCsvContractError, match="missing required"):
        mod.load_indicator_rows_from_text("identifier,title\nid,title\n")
    with pytest.raises(mod.IndicatorCsvContractError, match="more cells than headers"):
        mod.load_indicator_rows_from_text(_register_csv([*_row(), "extra"]))
    with pytest.raises(mod.IndicatorCsvContractError, match="maximum of 1"):
        mod.load_indicator_rows_from_text(
            _register_csv(_row("a"), _row("b")), max_rows=1
        )
    with pytest.raises(mod.IndicatorCsvContractError, match="empty"):
        mod.load_indicator_rows_from_text(
            ",".join(mod.REQUIRED_REGISTER_COLUMNS) + "\n"
        )

    missing_path = tmp_path / "missing.csv"
    with pytest.raises(FileNotFoundError):
        mod.load_indicator_rows_from_csv(missing_path)

    csv_path = tmp_path / "register.csv"
    csv_path.write_text(_register_csv(_row("a"), _row("b")), encoding="utf-8")
    rows = mod.load_indicator_rows_from_csv(csv_path, skip_rows=1)
    assert rows[0]["identifier"] == "b"
    with pytest.raises(mod.IndicatorCsvContractError, match="empty"):
        mod.load_indicator_rows_from_csv(csv_path, skip_rows=3)
    with pytest.raises(mod.IndicatorCsvContractError, match="maximum of 1"):
        mod.load_indicator_rows_from_csv(csv_path, max_rows=1)

    too_many_cells = tmp_path / "too-many-cells.csv"
    too_many_cells.write_text(_register_csv([*_row("a"), "extra"]), encoding="utf-8")
    with pytest.raises(mod.IndicatorCsvContractError, match="more cells than headers"):
        mod.load_indicator_rows_from_csv(too_many_cells)


def test_indicator_catalog_change_classification_with_active_rows():
    existing_same = SimpleNamespace(
        identifier="urn:sds:reg:esrs:e1_1",
        title="Transition plan",
    )
    existing_changed = SimpleNamespace(
        identifier="urn:sds:reg:esrs:e1_2",
        title="Old title",
    )
    db = _Db([existing_same, existing_changed])

    created, updated, unchanged = mod._classify_catalog_changes(
        db,
        [
            {"identifier": "urn:sds:reg:esrs:e1_1", "title": "Transition plan"},
            {"identifier": "urn:sds:reg:esrs:e1_2", "title": "New title"},
            {"identifier": "urn:sds:reg:esrs:e1_3", "title": "New datapoint"},
        ],
    )

    assert (created, updated, unchanged) == (1, 1, 1)
    assert list(mod._chunks(["a", "b", "c"], 2)) == [["a", "b"], ["c"]]


def test_indicator_legacy_migration_helpers_cover_skip_update_and_flush_paths():
    assert mod.collect_legacy_identifier_migrations(None, []) == {}

    legacy = SimpleNamespace(id="legacy", identifier="urn:sds:reg:ESRS:E1-6 07")

    class _MigrationQuery:
        def __init__(self, first_values):
            self.first_values = list(first_values)
            self.updated = False

        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return self.first_values.pop(0) if self.first_values else None

        def update(self, *_args, **_kwargs):
            self.updated = True
            return 1

        def delete(self, *_args, **_kwargs):
            return 1

        def all(self):
            return [("urn:sds:reg:ESRS:E1-6 07",)]

    class _MigrationDb(_Db):
        def __init__(self, first_values):
            super().__init__([])
            self.query_obj = _MigrationQuery(first_values)

        def query(self, *_args, **_kwargs):
            return self.query_obj

    db = _MigrationDb([None])
    assert mod.migrate_legacy_identifiers(
        db, {"legacy": "canonical"}, commit=False
    ) == (
        0,
        0,
    )

    db = _MigrationDb([legacy, None])
    assert mod.migrate_legacy_identifiers(
        db, {"legacy": "canonical"}, commit=False
    ) == (1, 0)
    assert db.flushed is True

    migrations = mod.collect_legacy_identifier_migrations(
        db,
        [
            {
                "original_identifier": "urn:sds:reg:ESRS:E1-6 07",
                "canonical_identifier": "urn:sds:reg:esrs:e1_6_07",
                "normalized": True,
            }
        ],
    )
    assert migrations["urn:sds:reg:ESRS:E1-6 07"] == "urn:sds:reg:esrs:e1_6_07"

    assert (
        mod._record_changes_indicator(
            {"id": "skip", "missing": "skip"}, SimpleNamespace(identifier="x")
        )
        is False
    )


def test_indicator_apply_rolls_back_when_repository_write_fails(monkeypatch):
    class _FailingRepo:
        def __init__(self, _db):
            pass

        def bulk_upsert(self, *_args, **_kwargs):
            raise RuntimeError("upsert failed")

    db = _Db([])
    monkeypatch.setattr(mod, "IndicatorRepository", _FailingRepo)

    with pytest.raises(RuntimeError, match="upsert failed"):
        mod.apply_indicator_import_transactional(
            csv_text=_register_csv(_row("urn:sds:reg:esrs:e1_6_07")),
            db=db,
            source_ref="register.csv",
            source_hash=None,
            created_by="tester",
        )

    assert db.rolled_back is True
