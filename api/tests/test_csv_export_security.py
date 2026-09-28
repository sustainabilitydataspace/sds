from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, cast

import pytest


@pytest.mark.parametrize(
    "value",
    [
        '=HYPERLINK("https://example.invalid","open")',
        "+CMD",
        "-1+2",
        "@SUM(1+1)",
        "  =1+1",
        "\t@SUM(1+1)",
        "\r-2+3",
        "\n+2+3",
    ],
)
def test_spreadsheet_safe_cell_neutralizes_formula_prefixes(value: str):
    from src.api.csv_security import spreadsheet_safe_cell

    assert spreadsheet_safe_cell(value) == "'" + value


@pytest.mark.parametrize("value", ["ordinary", "42", 42, None, True, ""])
def test_spreadsheet_safe_cell_preserves_non_formula_values(value):
    from src.api.csv_security import spreadsheet_safe_cell

    assert spreadsheet_safe_cell(value) == value


def test_value_csv_stream_neutralizes_user_controlled_cells():
    from src.api.routers.values import _stream_values_csv

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    item = SimpleNamespace(
        id="id-1",
        concept="concept",
        entity='=HYPERLINK("https://example.invalid","open")',
        period=date(2026, 1, 1),
        external_key="@SUM(1+1)",
        value="+CMD",
        value_type="string",
        unit="-DDE",
        original_unit=None,
        conversion_applied=False,
        metadata={},
        created_at=now,
        updated_at=now,
    )

    rows = list(
        csv.DictReader(io.StringIO("".join(_stream_values_csv([cast(Any, item)]))))
    )
    assert rows[0]["entity"].startswith("'=")
    assert rows[0]["external_key"].startswith("'@")
    assert rows[0]["value"].startswith("'+")
    assert rows[0]["unit"].startswith("'-")
