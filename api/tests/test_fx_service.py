from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from src.calculation.conversion.fx import (
    FXAmbiguousRateError,
    FXConverter,
    FXMissingRateError,
    FXPolicy,
)


@dataclass
class InMemoryRates:
    observations: list[dict]
    periods: list[dict]

    def find_daily_rate(
        self, *, provider, rate_type, base_currency, quote_currency, rate_date
    ):
        matches = [
            item
            for item in self.observations
            if item["provider"] == provider
            and item["rate_type"] == rate_type
            and item["base_currency"] == base_currency
            and item["quote_currency"] == quote_currency
            and item["rate_date"] == rate_date
        ]
        return matches[0] if matches else None

    def find_previous_daily_rate(
        self, *, provider, rate_type, base_currency, quote_currency, rate_date
    ):
        matches = [
            item
            for item in self.observations
            if item["provider"] == provider
            and item["rate_type"] == rate_type
            and item["base_currency"] == base_currency
            and item["quote_currency"] == quote_currency
            and item["rate_date"] <= rate_date
        ]
        matches.sort(key=lambda item: (item["rate_date"], item["id"]), reverse=True)
        return matches[0] if matches else None

    def find_period_rate(
        self,
        *,
        provider,
        rate_type,
        base_currency,
        quote_currency,
        period_type,
        period_start,
        period_end,
    ):
        matches = [
            item
            for item in self.periods
            if item["provider"] == provider
            and item["rate_type"] == rate_type
            and item["base_currency"] == base_currency
            and item["quote_currency"] == quote_currency
            and item["period_type"] == period_type
            and item["period_start"] == period_start
            and item["period_end"] == period_end
        ]
        return matches[0] if matches else None


def test_transaction_date_policy_uses_exact_daily_rate():
    repo = InMemoryRates(
        observations=[
            {
                "id": 10,
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 15),
                "rate_value": Decimal("1.170000"),
            }
        ],
        periods=[],
    )
    converter = FXConverter(repo)
    policy = FXPolicy(
        id="ecb-daily",
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
    )

    result = converter.convert(
        Decimal("100"),
        from_currency="GBP",
        to_currency="EUR",
        value_date=date(2024, 3, 15),
        period_start=date(2024, 3, 1),
        period_end=date(2024, 3, 31),
        policy=policy,
    )

    assert result.value == Decimal("117.000000")
    assert result.trace[0]["rate_observation_id"] == "10"
    assert result.trace[0]["policy_id"] == "ecb-daily"
    assert result.trace[0]["source"] == "ECB:reference"
    assert result.trace[0]["metadata"]["rate_date"] == "2024-03-15"
    assert result.trace[0]["metadata"]["selection_mode"] == "transaction_date"


def test_previous_available_daily_policy_uses_latest_prior_rate_with_trace_dates():
    repo = InMemoryRates(
        observations=[
            {
                "id": 9,
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 14),
                "rate_value": Decimal("1.160000"),
            },
            {
                "id": 10,
                "provider": "OTHER",
                "rate_type": "reference",
                "base_currency": "GBP",
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 16),
                "rate_value": Decimal("9.990000"),
            },
            {
                "id": 11,
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 15),
                "rate_value": Decimal("1.170000"),
            },
        ],
        periods=[],
    )
    converter = FXConverter(repo)
    policy = FXPolicy(
        id="ecb-daily",
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
        business_day_rule="previous_available",
    )

    result = converter.convert(
        Decimal("100"),
        from_currency="GBP",
        to_currency="EUR",
        value_date=date(2024, 3, 16),
        period_start=date(2024, 3, 1),
        period_end=date(2024, 3, 31),
        policy=policy,
    )

    assert result.value == Decimal("117.000000")
    assert result.trace[0]["rate_observation_id"] == "11"
    assert result.trace[0]["metadata"]["requested_rate_date"] == "2024-03-16"
    assert result.trace[0]["metadata"]["rate_date"] == "2024-03-15"


def test_previous_available_daily_policy_rejects_a_stale_prior_rate():
    repo = InMemoryRates(
        observations=[
            {
                "id": 12,
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 1),
                "rate_value": Decimal("1.170000"),
            }
        ],
        periods=[],
    )
    converter = FXConverter(repo)
    policy = FXPolicy(
        id="ecb-daily",
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
        max_previous_rate_age_days=7,
    )

    with pytest.raises(FXMissingRateError, match="older than the policy ceiling"):
        converter.convert(
            Decimal("100"),
            from_currency="GBP",
            to_currency="EUR",
            value_date=date(2024, 3, 16),
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            policy=policy,
        )


def test_closing_rate_previous_available_uses_latest_prior_period_end_rate():
    repo = InMemoryRates(
        observations=[
            {
                "id": 12,
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "USD",
                "quote_currency": "EUR",
                "rate_date": date(2024, 12, 30),
                "rate_value": Decimal("0.900000"),
            }
        ],
        periods=[],
    )
    converter = FXConverter(repo)
    policy = FXPolicy(
        id="ecb-close",
        provider="ECB",
        rate_type="reference",
        selection_mode="closing_rate",
        business_day_rule="previous_available",
    )

    result = converter.convert(
        Decimal("100"),
        from_currency="USD",
        to_currency="EUR",
        value_date=date(2024, 12, 1),
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        policy=policy,
    )

    assert result.value == Decimal("90.000000")
    assert result.trace[0]["metadata"]["requested_rate_date"] == "2024-12-31"
    assert result.trace[0]["metadata"]["rate_date"] == "2024-12-30"


def test_daily_policy_rejects_unsupported_business_day_rule_and_triangulation():
    repo = InMemoryRates(
        observations=[
            {
                "id": 10,
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 15),
                "rate_value": Decimal("1.170000"),
            }
        ],
        periods=[],
    )
    converter = FXConverter(repo)

    with pytest.raises(FXMissingRateError, match="unsupported FX business day rule"):
        converter.convert(
            Decimal("100"),
            from_currency="GBP",
            to_currency="EUR",
            value_date=date(2024, 3, 15),
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            policy=FXPolicy(
                id="ecb-next",
                provider="ECB",
                rate_type="reference",
                selection_mode="transaction_date",
                business_day_rule="next_available",
            ),
        )

    with pytest.raises(FXMissingRateError, match="FX triangulation is not supported"):
        converter.convert(
            Decimal("100"),
            from_currency="GBP",
            to_currency="EUR",
            value_date=date(2024, 3, 15),
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            policy=FXPolicy(
                id="ecb-triangulate",
                provider="ECB",
                rate_type="reference",
                selection_mode="transaction_date",
                triangulation_allowed=True,
            ),
        )


def test_closing_rate_policy_uses_exact_period_end_daily_rate():
    repo = InMemoryRates(
        observations=[
            {
                "id": 11,
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "USD",
                "quote_currency": "EUR",
                "rate_date": date(2024, 12, 31),
                "rate_value": Decimal("0.900000"),
                "source_hash": "daily-close",
            }
        ],
        periods=[],
    )
    converter = FXConverter(repo)
    policy = FXPolicy(
        id="ecb-close",
        provider="ECB",
        rate_type="reference",
        selection_mode="closing_rate",
    )

    result = converter.convert(
        Decimal("100"),
        from_currency="USD",
        to_currency="EUR",
        value_date=date(2024, 12, 1),
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        policy=policy,
    )

    assert result.value == Decimal("90.000000")
    assert result.trace[0]["rate_observation_id"] == "11"
    assert result.trace[0]["metadata"]["source_hash"] == "daily-close"


def test_closing_rate_requires_period_end():
    converter = FXConverter(InMemoryRates(observations=[], periods=[]))
    policy = FXPolicy(
        id="ecb-close",
        provider="ECB",
        rate_type="reference",
        selection_mode="closing_rate",
    )

    with pytest.raises(FXMissingRateError, match="period_end"):
        converter.convert(
            Decimal("100"),
            from_currency="USD",
            to_currency="EUR",
            value_date=date(2024, 12, 1),
            period_start=date(2024, 1, 1),
            period_end=None,
            policy=policy,
        )


def test_missing_rate_fails_closed():
    converter = FXConverter(InMemoryRates(observations=[], periods=[]))
    policy = FXPolicy(
        id="ecb-daily",
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
    )

    with pytest.raises(FXMissingRateError):
        converter.convert(
            Decimal("100"),
            from_currency="GBP",
            to_currency="EUR",
            value_date=date(2024, 3, 15),
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            policy=policy,
        )


def test_unsupported_selection_mode_fails_closed():
    converter = FXConverter(InMemoryRates(observations=[], periods=[]))
    policy = FXPolicy(
        id="ecb-weird",
        provider="ECB",
        rate_type="reference",
        selection_mode="nearest_rate",
    )

    with pytest.raises(FXMissingRateError, match="unsupported FX selection mode"):
        converter.convert(
            Decimal("100"),
            from_currency="USD",
            to_currency="EUR",
            value_date=date(2024, 3, 15),
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            policy=policy,
        )


@pytest.mark.parametrize(
    ("selection_mode", "period_type"),
    [
        ("monthly_average", "monthly_average"),
        ("annual_average", "annual_average"),
        ("period_average", "period_average"),
    ],
)
def test_average_policies_use_exact_materialized_period_rate(
    selection_mode, period_type
):
    repo = InMemoryRates(
        observations=[],
        periods=[
            {
                "id": 99,
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "USD",
                "quote_currency": "EUR",
                "period_type": period_type,
                "period_start": date(2024, 3, 1),
                "period_end": date(2024, 3, 31),
                "rate_value": Decimal("0.920000"),
                "source_hash": "period-hash",
                "source_observation_ids": [1, 2, 3],
            }
        ],
    )
    converter = FXConverter(repo)
    policy = FXPolicy(
        id=f"ecb-{selection_mode}",
        provider="ECB",
        rate_type="reference",
        selection_mode=selection_mode,
    )

    result = converter.convert(
        Decimal("100"),
        from_currency="USD",
        to_currency="EUR",
        value_date=date(2024, 3, 15),
        period_start=date(2024, 3, 1),
        period_end=date(2024, 3, 31),
        policy=policy,
    )

    assert result.value == Decimal("92.000000")
    assert result.trace[0]["policy_id"] == f"ecb-{selection_mode}"
    assert result.trace[0]["rate_period_id"] == "99"
    assert "rate_observation_id" not in result.trace[0]
    assert result.trace[0]["metadata"]["period_type"] == period_type
    assert result.trace[0]["metadata"]["source_hash"] == "period-hash"
    assert result.trace[0]["metadata"]["source_observation_ids"] == [1, 2, 3]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"from_currency": None, "to_currency": "EUR", "value_date": date(2024, 3, 15)},
        {"from_currency": "USD", "to_currency": None, "value_date": date(2024, 3, 15)},
        {"from_currency": "USD", "to_currency": "EUR", "value_date": None},
    ],
)
def test_missing_currency_or_transaction_date_fails_closed(kwargs):
    converter = FXConverter(InMemoryRates(observations=[], periods=[]))
    policy = FXPolicy(
        id="ecb-daily",
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
    )

    with pytest.raises(FXMissingRateError):
        converter.convert(
            Decimal("100"),
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            policy=policy,
            **kwargs,
        )


def test_period_policy_missing_period_fails_closed():
    converter = FXConverter(InMemoryRates(observations=[], periods=[]))
    policy = FXPolicy(
        id="ecb-monthly",
        provider="ECB",
        rate_type="reference",
        selection_mode="monthly_average",
    )

    with pytest.raises(FXMissingRateError):
        converter.convert(
            Decimal("100"),
            from_currency="USD",
            to_currency="EUR",
            value_date=date(2024, 3, 15),
            period_start=None,
            period_end=date(2024, 3, 31),
            policy=policy,
        )


def test_same_currency_identity_does_not_lookup_rate():
    @dataclass
    class ExplodingRates:
        def find_daily_rate(self, **kwargs):
            raise AssertionError("same-currency conversion must not query daily rates")

        def find_period_rate(self, **kwargs):
            raise AssertionError("same-currency conversion must not query period rates")

    converter = FXConverter(ExplodingRates())
    policy = FXPolicy(
        id="ecb-daily",
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
    )

    result = converter.convert(
        Decimal("100"),
        from_currency="EUR",
        to_currency="EUR",
        value_date=None,
        period_start=None,
        period_end=None,
        policy=policy,
    )

    assert result.value == Decimal("100")
    assert result.currency == "EUR"
    assert result.trace == []


def test_ambiguous_repository_response_fails_closed():
    @dataclass
    class AmbiguousRates:
        def find_daily_rate(self, **kwargs):
            return [
                {"id": 1, "rate_value": Decimal("1.1")},
                {"id": 2, "rate_value": Decimal("1.2")},
            ]

        def find_period_rate(self, **kwargs):
            return None

    converter = FXConverter(AmbiguousRates())
    policy = FXPolicy(
        id="ecb-daily",
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
    )

    with pytest.raises(FXAmbiguousRateError):
        converter.convert(
            Decimal("100"),
            from_currency="GBP",
            to_currency="EUR",
            value_date=date(2024, 3, 15),
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            policy=policy,
        )


def test_rate_key_mismatch_fails_closed_even_if_repository_returns_row():
    @dataclass
    class WrongKeyRates:
        def find_daily_rate(self, **kwargs):
            return {
                "id": 1,
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "USD",
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 15),
                "rate_value": Decimal("1.2"),
            }

        def find_period_rate(self, **kwargs):
            return None

    converter = FXConverter(WrongKeyRates())
    policy = FXPolicy(
        id="ecb-daily",
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
    )

    with pytest.raises(FXAmbiguousRateError):
        converter.convert(
            Decimal("100"),
            from_currency="GBP",
            to_currency="EUR",
            value_date=date(2024, 3, 15),
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            policy=policy,
        )


def test_zero_or_negative_rate_fails_closed():
    repo = InMemoryRates(
        observations=[
            {
                "id": 10,
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 15),
                "rate_value": Decimal("0"),
            }
        ],
        periods=[],
    )
    converter = FXConverter(repo)
    policy = FXPolicy(
        id="ecb-daily",
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
    )

    with pytest.raises(FXMissingRateError):
        converter.convert(
            Decimal("100"),
            from_currency="GBP",
            to_currency="EUR",
            value_date=date(2024, 3, 15),
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            policy=policy,
        )


def test_fx_converter_private_edge_branches():
    converter = FXConverter(InMemoryRates(observations=[], periods=[]))

    with pytest.raises(FXMissingRateError, match="not found"):
        converter._single_rate_or_raise([])

    assert converter._single_rate_or_raise([{"id": 1}]) == {"id": 1}

    with pytest.raises(FXMissingRateError, match="missing rate_value"):
        converter._required_decimal({"id": 1}, "rate_value")

    with pytest.raises(FXMissingRateError, match="missing id"):
        converter._required_field({"rate_value": Decimal("1")}, "id")

    with pytest.raises(FXMissingRateError, match="missing quote_currency"):
        converter._validate_rate_key(
            {"base_currency": "USD"}, {"quote_currency": "EUR"}
        )

    with pytest.raises(FXMissingRateError, match="unsupported FX rounding mode"):
        converter._round(
            Decimal("1.234"),
            FXPolicy(
                id="bad-round",
                provider="ECB",
                rate_type="reference",
                selection_mode="transaction_date",
                rounding_mode="ROUND_UNSUPPORTED",
            ),
        )


@pytest.mark.parametrize(
    "rounding_mode",
    [
        "ROUND_HALF_UP",
        "ROUND_HALF_EVEN",
        "ROUND_HALF_DOWN",
        "ROUND_UP",
        "ROUND_DOWN",
        "ROUND_CEILING",
        "ROUND_FLOOR",
        "ROUND_05UP",
    ],
)
def test_all_standard_rounding_modes_are_accepted(rounding_mode):
    """FX converter should accept all standard Decimal rounding modes."""
    converter = FXConverter(InMemoryRates(observations=[], periods=[]))
    policy = FXPolicy(
        id=f"ecb-{rounding_mode.lower()}",
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
        rounding_mode=rounding_mode,
    )
    result = converter._round(Decimal("1.234567"), policy)
    assert result is not None


def test_non_fail_closed_policy_is_rejected():
    repo = InMemoryRates(
        observations=[
            {
                "id": 10,
                "provider": "ECB",
                "rate_type": "reference",
                "base_currency": "GBP",
                "quote_currency": "EUR",
                "rate_date": date(2024, 3, 15),
                "rate_value": Decimal("1.170000"),
            }
        ],
        periods=[],
    )
    converter = FXConverter(repo)
    policy = FXPolicy(
        id="ecb-daily",
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
        fallback_behavior="latest_available",
    )

    with pytest.raises(FXMissingRateError):
        converter.convert(
            Decimal("100"),
            from_currency="GBP",
            to_currency="EUR",
            value_date=date(2024, 3, 15),
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            policy=policy,
        )
