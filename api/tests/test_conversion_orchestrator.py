from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from src.calculation.conversion.dimensions import DimensionVector
from src.calculation.conversion.fx import FXConverter, FXPolicy
from src.calculation.conversion.orchestrator import (
    ConversionDependencyError,
    ConversionEngine,
    ConversionRequest,
)
from src.calculation.conversion.physical import PhysicalUnit, PhysicalUnitConverter


def _physical_converter():
    return PhysicalUnitConverter(
        units={
            "kgCO2e": PhysicalUnit(
                "kgCO2e", "kilogram CO2e", DimensionVector({"co2e": 1}), Decimal("1")
            ),
            "tCO2e": PhysicalUnit(
                "tCO2e", "tonne CO2e", DimensionVector({"co2e": 1}), Decimal("1000")
            ),
            "kWh": PhysicalUnit(
                "kWh",
                "kilowatt hour",
                DimensionVector({"energy": 1}),
                Decimal("3600000"),
            ),
            "MWh": PhysicalUnit(
                "MWh",
                "megawatt hour",
                DimensionVector({"energy": 1}),
                Decimal("3600000000"),
            ),
        },
        rules=[],
        precision=6,
    )


@dataclass
class InMemoryRates:
    observations: list[dict]

    def find_daily_rate(self, **kwargs):
        matches = [
            item
            for item in self.observations
            if all(item[key] == value for key, value in kwargs.items())
        ]
        return matches[0] if matches else None

    def find_period_rate(self, **kwargs):
        return None


def _fx_converter():
    return FXConverter(
        InMemoryRates(
            observations=[
                {
                    "id": "fx-1",
                    "provider": "ECB",
                    "rate_type": "reference",
                    "base_currency": "USD",
                    "quote_currency": "EUR",
                    "rate_date": date(2024, 3, 15),
                    "rate_value": Decimal("0.900000"),
                }
            ]
        )
    )


def _policy_resolver(policy_id):
    assert policy_id == "ecb-daily"
    return FXPolicy(
        id=policy_id,
        provider="ECB",
        rate_type="reference",
        selection_mode="transaction_date",
    )


def test_normalize_applies_unit_conversion_before_fx_conversion():
    engine = ConversionEngine(
        physical_converter=_physical_converter(),
        fx_converter=_fx_converter(),
        fx_policy_resolver=_policy_resolver,
    )

    result = engine.normalize(
        ConversionRequest(
            value=Decimal("1000"),
            from_unit="kgCO2e/kWh",
            to_unit="tCO2e/MWh",
            from_currency="USD",
            to_currency="EUR",
            value_date=date(2024, 3, 15),
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            fx_policy_id="ecb-daily",
        )
    )

    assert result.value == Decimal("900.000000")
    assert result.unit == "tCO2e/MWh"
    assert result.currency == "EUR"
    assert [step["step_type"] for step in result.trace] == ["unit", "fx"]
    assert result.trace[0]["converted_value"] == "1000.000000"
    assert result.trace[1]["original_value"] == "1000.000000"


def test_normalize_accepts_plan_field_names_for_runtime_integration():
    engine = ConversionEngine(
        physical_converter=_physical_converter(),
        fx_converter=_fx_converter(),
        fx_policy_resolver=_policy_resolver,
    )

    result = engine.normalize(
        ConversionRequest(
            value=Decimal("1000"),
            unit="kgCO2e/kWh",
            expected_unit="tCO2e/MWh",
            currency="USD",
            expected_currency="EUR",
            value_date=date(2024, 3, 15),
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
            fx_policy_id="ecb-daily",
        )
    )

    assert result.value == Decimal("900.000000")
    assert result.unit == "tCO2e/MWh"
    assert result.currency == "EUR"


def test_normalize_noops_when_already_normalized():
    engine = ConversionEngine(physical_converter=_physical_converter())

    result = engine.normalize(
        ConversionRequest(
            value=Decimal("42"),
            from_unit="tCO2e/MWh",
            to_unit="tCO2e/MWh",
            from_currency="EUR",
            to_currency="EUR",
        )
    )

    assert result.value == Decimal("42")
    assert result.unit == "tCO2e/MWh"
    assert result.currency == "EUR"
    assert result.trace == []


@pytest.mark.parametrize(
    "engine",
    [
        ConversionEngine(
            physical_converter=_physical_converter(), fx_converter=_fx_converter()
        ),
        ConversionEngine(
            physical_converter=_physical_converter(),
            fx_policy_resolver=_policy_resolver,
        ),
    ],
)
def test_fx_missing_dependencies_fail_closed(engine):
    with pytest.raises(ConversionDependencyError):
        engine.normalize(
            ConversionRequest(
                value=Decimal("100"),
                from_unit="tCO2e/MWh",
                to_unit="tCO2e/MWh",
                from_currency="USD",
                to_currency="EUR",
                value_date=date(2024, 3, 15),
                fx_policy_id="ecb-daily",
            )
        )


def test_missing_source_currency_when_target_currency_differs_fails_closed():
    engine = ConversionEngine(
        physical_converter=_physical_converter(),
        fx_converter=_fx_converter(),
        fx_policy_resolver=_policy_resolver,
    )

    with pytest.raises(ConversionDependencyError):
        engine.normalize(
            ConversionRequest(
                value=Decimal("100"),
                from_unit="tCO2e/MWh",
                to_unit="tCO2e/MWh",
                from_currency=None,
                to_currency="EUR",
                value_date=date(2024, 3, 15),
                fx_policy_id="ecb-daily",
            )
        )


def test_fx_conversion_requires_policy_id_and_known_policy():
    engine = ConversionEngine(
        physical_converter=_physical_converter(),
        fx_converter=_fx_converter(),
        fx_policy_resolver=lambda _policy_id: None,
    )

    with pytest.raises(ConversionDependencyError, match="fx_policy_id is required"):
        engine.normalize(
            ConversionRequest(
                value=Decimal("100"),
                from_unit="tCO2e/MWh",
                to_unit="tCO2e/MWh",
                from_currency="USD",
                to_currency="EUR",
                value_date=date(2024, 3, 15),
            )
        )

    with pytest.raises(ConversionDependencyError, match="FX policy not found"):
        engine.normalize(
            ConversionRequest(
                value=Decimal("100"),
                from_unit="tCO2e/MWh",
                to_unit="tCO2e/MWh",
                from_currency="USD",
                to_currency="EUR",
                value_date=date(2024, 3, 15),
                fx_policy_id="missing-policy",
            )
        )


def test_conflicting_alias_fields_fail_closed():
    engine = ConversionEngine(physical_converter=_physical_converter())

    with pytest.raises(ConversionDependencyError):
        engine.normalize(
            ConversionRequest(
                value=Decimal("1"),
                unit="kgCO2e/kWh",
                from_unit="tCO2e/MWh",
                expected_unit="tCO2e/MWh",
            )
        )
