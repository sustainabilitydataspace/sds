"""Tests for period parsing validation (F-014)."""

import pytest

from src.api.models import TemporalGranularity
from src.api.routers.calculations import _parse_period


class TestParsePeriod:
    def test_annual(self):
        start, end = _parse_period("2024", TemporalGranularity.ANNUAL)
        assert start.year == 2024 and start.month == 1
        assert end.year == 2024 and end.month == 12

    def test_quarterly(self):
        start, end = _parse_period("2024-Q1", TemporalGranularity.QUARTERLY)
        assert start.month == 1 and end.month == 3

    def test_monthly(self):
        start, end = _parse_period("2024-06", TemporalGranularity.MONTHLY)
        assert start.month == 6 and end.month == 6

    def test_invalid_quarter_raises(self):
        with pytest.raises(ValueError):
            _parse_period("2024-Q5", TemporalGranularity.QUARTERLY)

    def test_invalid_month_raises(self):
        with pytest.raises(ValueError):
            _parse_period("2024-13", TemporalGranularity.MONTHLY)

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            _parse_period("not-a-date", TemporalGranularity.ANNUAL)
