from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable

from src.calculation.conversion.fx import FXConverter, FXPolicy
from src.calculation.conversion.physical import PhysicalUnitConverter


class ConversionDependencyError(ValueError):
    pass


@dataclass(frozen=True)
class ConversionRequest:
    value: Decimal
    unit: str | None = None
    expected_unit: str | None = None
    currency: str | None = None
    expected_currency: str | None = None
    from_unit: str | None = None
    to_unit: str | None = None
    from_currency: str | None = None
    to_currency: str | None = None
    value_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    fx_policy_id: str | None = None
    as_of: date | None = None


@dataclass(frozen=True)
class NormalizedValue:
    value: Decimal
    unit: str | None
    currency: str | None
    trace: list[dict]


class ConversionEngine:
    def __init__(
        self,
        *,
        physical_converter: PhysicalUnitConverter,
        fx_converter: FXConverter | None = None,
        fx_policy_resolver: Callable[[str], FXPolicy] | None = None,
    ) -> None:
        self.physical_converter = physical_converter
        self.fx_converter = fx_converter
        self.fx_policy_resolver = fx_policy_resolver

    def normalize(self, request: ConversionRequest) -> NormalizedValue:
        value = Decimal(str(request.value))
        from_unit = self._coalesce("unit/from_unit", request.unit, request.from_unit)
        to_unit = self._coalesce(
            "expected_unit/to_unit", request.expected_unit, request.to_unit
        )
        from_currency = self._coalesce(
            "currency/from_currency", request.currency, request.from_currency
        )
        to_currency = self._coalesce(
            "expected_currency/to_currency",
            request.expected_currency,
            request.to_currency,
        )
        unit = from_unit
        currency = from_currency
        trace: list[dict] = []

        if from_unit and to_unit and from_unit != to_unit:
            physical_result = self.physical_converter.convert(
                value,
                from_unit,
                to_unit,
                as_of=request.as_of or request.value_date or request.period_end,
            )
            value = physical_result.value
            unit = physical_result.unit
            trace.extend(physical_result.trace)
        elif to_unit is not None:
            unit = to_unit

        if self._fx_needed(from_currency=from_currency, to_currency=to_currency):
            if not from_currency:
                raise ConversionDependencyError(
                    "source currency is required for FX conversion"
                )
            if not request.fx_policy_id:
                raise ConversionDependencyError(
                    "fx_policy_id is required for FX conversion"
                )
            if self.fx_converter is None or self.fx_policy_resolver is None:
                raise ConversionDependencyError(
                    "fx_converter and fx_policy_resolver are required for FX conversion"
                )

            policy = self.fx_policy_resolver(request.fx_policy_id)
            if policy is None:
                raise ConversionDependencyError(
                    f"FX policy not found: {request.fx_policy_id}"
                )
            fx_result = self.fx_converter.convert(
                value,
                from_currency=from_currency,
                to_currency=to_currency,
                value_date=request.value_date,
                period_start=request.period_start,
                period_end=request.period_end,
                policy=policy,
            )
            value = fx_result.value
            currency = fx_result.currency
            trace.extend(fx_result.trace)
        elif to_currency is not None:
            currency = to_currency

        return NormalizedValue(value=value, unit=unit, currency=currency, trace=trace)

    def _coalesce(
        self, label: str, first: str | None, second: str | None
    ) -> str | None:
        if first is not None and second is not None and first != second:
            raise ConversionDependencyError(
                f"conflicting conversion request fields: {label}"
            )
        return first if first is not None else second

    def _fx_needed(self, *, from_currency: str | None, to_currency: str | None) -> bool:
        return bool(to_currency and from_currency != to_currency)
