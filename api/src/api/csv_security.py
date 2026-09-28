"""CSV serialization guards for spreadsheet consumers."""

from __future__ import annotations

from typing import Any, Mapping

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def spreadsheet_safe_cell(value: Any) -> Any:
    """Neutralize strings that spreadsheet applications may execute as formulas."""
    if not isinstance(value, str):
        return value
    candidate = value.lstrip()
    if candidate.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def spreadsheet_safe_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a CSV row with every cell neutralized."""
    return {key: spreadsheet_safe_cell(value) for key, value in row.items()}
