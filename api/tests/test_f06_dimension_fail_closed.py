"""F06 reconverge — unit conversion fails closed on same-category dimension mismatch.

codex F06 M1: UnitConverter.convert -> _convert_via_base_unit only checked category,
so two `ratio`-category units with different dimension_vectors (e.g.
cases_per_million_hours_worked {count:1,time:-1} vs hours_per_employee
{count:-1,time:1}) could linearly convert through a shared base unit instead of
failing closed. The base-unit path now enforces dimensional compatibility.
"""

from __future__ import annotations

import pytest

from src.calculation.unit_converter import UnitConversionError, UnitConverter


@pytest.fixture(scope="module")
def converter() -> UnitConverter:
    return UnitConverter()


def test_incompatible_same_category_ratio_units_fail_closed(converter):
    with pytest.raises(UnitConversionError):
        converter.convert(1, "cases_per_million_hours_worked", "hours_per_employee")


def test_compatible_conversion_still_works(converter):
    result = converter.convert(1, "kg", "g")
    assert result.converted_value == 1000

    # identity conversion is unaffected
    same = converter.convert(5, "kg", "kg")
    assert same.converted_value == 5


def test_incompatible_units_are_not_reported_compatible(converter):
    assert (
        converter.are_units_compatible(
            "cases_per_million_hours_worked", "hours_per_employee"
        )
        is False
    )
