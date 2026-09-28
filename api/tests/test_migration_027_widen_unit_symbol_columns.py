from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "027_widen_unit_symbol_columns.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_027_units", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeOp:
    def __init__(self, overflow: tuple[str, str] | None = None):
        self.overflow = overflow
        self.calls: list[tuple[str, str, str]] = []

    def get_bind(self):
        return self

    def execute(self, statement, _params):
        text = str(statement)
        for table_name, column_name in (
            ("conversion_rules", "to_unit"),
            ("conversion_rules", "from_unit"),
            ("units", "name"),
            ("units", "symbol"),
        ):
            if f"SELECT {column_name}" in text and f"FROM {table_name}" in text:
                self.calls.append(("check", table_name, column_name))
                if self.overflow == (table_name, column_name):
                    return _Result([("value-that-is-too-long",)])
                return _Result([])
        raise AssertionError(f"unexpected statement: {text}")

    def alter_column(self, table_name, column_name, **_kwargs):
        self.calls.append(("alter", table_name, column_name))


def test_migration_027_downgrade_checks_all_columns_before_narrowing(monkeypatch):
    migration = _load_migration()
    fake_op = _FakeOp()
    monkeypatch.setattr(migration, "op", fake_op)

    migration.downgrade()

    assert fake_op.calls[:4] == [
        ("check", "conversion_rules", "to_unit"),
        ("check", "conversion_rules", "from_unit"),
        ("check", "units", "name"),
        ("check", "units", "symbol"),
    ]
    assert fake_op.calls[4:] == [
        ("alter", "conversion_rules", "to_unit"),
        ("alter", "conversion_rules", "from_unit"),
        ("alter", "units", "name"),
        ("alter", "units", "symbol"),
    ]


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    [
        ("conversion_rules", "to_unit"),
        ("conversion_rules", "from_unit"),
        ("units", "name"),
        ("units", "symbol"),
    ],
)
def test_migration_027_downgrade_blocks_overflow_before_narrowing(
    monkeypatch,
    table_name,
    column_name,
):
    migration = _load_migration()
    fake_op = _FakeOp(overflow=(table_name, column_name))
    monkeypatch.setattr(migration, "op", fake_op)

    with pytest.raises(RuntimeError, match=f"{table_name}.{column_name}"):
        migration.downgrade()

    assert all(call[0] != "alter" for call in fake_op.calls)
