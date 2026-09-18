from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import (
    ROUND_05UP,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
)
from typing import Any

from src.calculation.conversion.trace import ConversionStep


class FXMissingRateError(ValueError):
    pass


class FXAmbiguousRateError(ValueError):
    pass


@dataclass(frozen=True)
class FXPolicy:
    id: str
    provider: str
    rate_type: str
    selection_mode: str
    business_day_rule: str = "previous_available"
    triangulation_allowed: bool = False
    fallback_behavior: str = "fail_closed"
    rounding_scale: int = 6
    rounding_mode: str = "ROUND_HALF_UP"


@dataclass(frozen=True)
class FXConversionResult:
    value: Decimal
    currency: str
    trace: list[dict]


class FXConverter:
    DAILY_SELECTION_MODES = {"transaction_date", "closing_rate"}
    PERIOD_SELECTION_MODES = {"monthly_average", "annual_average", "period_average"}

    def __init__(self, rate_repository: Any) -> None:
        self.rate_repository = rate_repository

    def convert(
        self,
        value,
        *,
        from_currency: str | None,
        to_currency: str | None,
        value_date: date | None,
        period_start: date | None,
        period_end: date | None,
        policy: FXPolicy,
    ) -> FXConversionResult:
        decimal_value = Decimal(str(value))
        if not from_currency or not to_currency:
            raise FXMissingRateError("source and target currency are required")
        if from_currency == to_currency:
            return FXConversionResult(decimal_value, to_currency, [])
        if policy.fallback_behavior != "fail_closed":
            raise FXMissingRateError(
                f"unsupported FX fallback behavior: {policy.fallback_behavior}"
            )
        if policy.triangulation_allowed:
            raise FXMissingRateError("FX triangulation is not supported")
        if policy.business_day_rule != "previous_available":
            raise FXMissingRateError(
                f"unsupported FX business day rule: {policy.business_day_rule}"
            )

        is_period_rate = False
        if policy.selection_mode == "transaction_date":
            if value_date is None:
                raise FXMissingRateError("transaction_date policy requires value_date")
            rate = self._lookup_daily_rate(
                policy=policy,
                from_currency=from_currency,
                to_currency=to_currency,
                rate_date=value_date,
            )
            metadata = self._metadata(rate) | self._daily_rate_metadata(
                rate=rate,
                selection_mode=policy.selection_mode,
                requested_rate_date=value_date,
            )
        elif policy.selection_mode == "closing_rate":
            if period_end is None:
                raise FXMissingRateError("closing_rate policy requires period_end")
            rate = self._lookup_daily_rate(
                policy=policy,
                from_currency=from_currency,
                to_currency=to_currency,
                rate_date=period_end,
            )
            metadata = self._metadata(rate) | self._daily_rate_metadata(
                rate=rate,
                selection_mode=policy.selection_mode,
                requested_rate_date=period_end,
            )
        elif policy.selection_mode in self.PERIOD_SELECTION_MODES:
            is_period_rate = True
            if period_start is None or period_end is None:
                raise FXMissingRateError(
                    f"{policy.selection_mode} policy requires period_start and period_end"
                )
            rate = self._lookup_period_rate(
                policy=policy,
                from_currency=from_currency,
                to_currency=to_currency,
                period_start=period_start,
                period_end=period_end,
            )
            metadata = self._metadata(rate) | {
                "selection_mode": policy.selection_mode,
                "period_type": policy.selection_mode,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            }
        else:
            raise FXMissingRateError(
                f"unsupported FX selection mode: {policy.selection_mode}"
            )

        rate_value = self._required_decimal(rate, "rate_value")
        converted = self._round(decimal_value * rate_value, policy)
        rate_id = self._required_field(rate, "id")
        rate_id_kwargs = (
            {"rate_period_id": str(rate_id)}
            if is_period_rate
            else {"rate_observation_id": str(rate_id)}
        )
        trace = [
            ConversionStep(
                step_type="fx",
                original_value=str(decimal_value),
                converted_value=str(converted),
                from_currency=from_currency,
                to_currency=to_currency,
                policy_id=policy.id,
                source=f"{policy.provider}:{policy.rate_type}",
                metadata=metadata,
                **rate_id_kwargs,
            ).as_dict()
        ]
        return FXConversionResult(converted, to_currency, trace)

    def _lookup_daily_rate(
        self,
        *,
        policy: FXPolicy,
        from_currency: str,
        to_currency: str,
        rate_date: date,
    ) -> Any:
        response = self.rate_repository.find_daily_rate(
            provider=policy.provider,
            rate_type=policy.rate_type,
            base_currency=from_currency,
            quote_currency=to_currency,
            rate_date=rate_date,
        )
        if response is None and policy.business_day_rule == "previous_available":
            previous_lookup = getattr(
                self.rate_repository, "find_previous_daily_rate", None
            )
            if callable(previous_lookup):
                response = previous_lookup(
                    provider=policy.provider,
                    rate_type=policy.rate_type,
                    base_currency=from_currency,
                    quote_currency=to_currency,
                    rate_date=rate_date,
                )
        rate = self._single_rate_or_raise(response)
        self._validate_rate_key(
            rate,
            {
                "provider": policy.provider,
                "rate_type": policy.rate_type,
                "base_currency": from_currency,
                "quote_currency": to_currency,
            },
        )
        selected_date = self._field(rate, "rate_date")
        if selected_date is None:
            raise FXMissingRateError("FX rate missing rate_date")
        if selected_date > rate_date:
            raise FXMissingRateError("FX previous-available rate is after request date")
        return rate

    def _lookup_period_rate(
        self,
        *,
        policy: FXPolicy,
        from_currency: str,
        to_currency: str,
        period_start: date,
        period_end: date,
    ) -> Any:
        response = self.rate_repository.find_period_rate(
            provider=policy.provider,
            rate_type=policy.rate_type,
            base_currency=from_currency,
            quote_currency=to_currency,
            period_type=policy.selection_mode,
            period_start=period_start,
            period_end=period_end,
        )
        rate = self._single_rate_or_raise(response)
        self._validate_rate_key(
            rate,
            {
                "provider": policy.provider,
                "rate_type": policy.rate_type,
                "base_currency": from_currency,
                "quote_currency": to_currency,
                "period_type": policy.selection_mode,
                "period_start": period_start,
                "period_end": period_end,
            },
        )
        return rate

    def _single_rate_or_raise(self, response: Any) -> Any:
        if response is None:
            raise FXMissingRateError("FX rate not found")
        if isinstance(response, (list, tuple)):
            if len(response) == 0:
                raise FXMissingRateError("FX rate not found")
            if len(response) > 1:
                raise FXAmbiguousRateError("ambiguous FX rate response")
            return response[0]
        return response

    def _metadata(self, rate: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for field_name in (
            "provider",
            "rate_type",
            "source_hash",
            "source_observation_ids",
        ):
            value = self._field(rate, field_name)
            if value is not None:
                metadata[field_name] = value if isinstance(value, list) else str(value)
        return metadata

    def _daily_rate_metadata(
        self, *, rate: Any, selection_mode: str, requested_rate_date: date
    ) -> dict[str, Any]:
        selected_date = self._field(rate, "rate_date") or requested_rate_date
        metadata = {
            "selection_mode": selection_mode,
            "rate_date": selected_date.isoformat(),
        }
        if selected_date != requested_rate_date:
            metadata["requested_rate_date"] = requested_rate_date.isoformat()
        return metadata

    def _required_decimal(self, rate: Any, field_name: str) -> Decimal:
        value = self._field(rate, field_name)
        if value is None:
            raise FXMissingRateError(f"FX rate missing {field_name}")
        decimal_value = Decimal(str(value))
        if field_name == "rate_value" and decimal_value <= 0:
            raise FXMissingRateError("FX rate must be positive")
        return decimal_value

    def _required_field(self, rate: Any, field_name: str) -> Any:
        value = self._field(rate, field_name)
        if value is None:
            raise FXMissingRateError(f"FX rate missing {field_name}")
        return value

    def _field(self, rate: Any, field_name: str) -> Any:
        if isinstance(rate, Mapping):
            return rate.get(field_name)
        return getattr(rate, field_name, None)

    def _validate_rate_key(self, rate: Any, expected: dict[str, Any]) -> None:
        for field_name, expected_value in expected.items():
            actual_value = self._field(rate, field_name)
            if actual_value is None:
                raise FXMissingRateError(f"FX rate missing {field_name}")
            if actual_value != expected_value:
                raise FXAmbiguousRateError(
                    f"FX rate key mismatch for {field_name}: {actual_value} != {expected_value}"
                )

    def _round(self, value: Decimal, policy: FXPolicy) -> Decimal:
        rounding_modes = {
            "ROUND_HALF_UP": ROUND_HALF_UP,
            "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
            "ROUND_HALF_DOWN": ROUND_HALF_DOWN,
            "ROUND_UP": ROUND_UP,
            "ROUND_DOWN": ROUND_DOWN,
            "ROUND_CEILING": ROUND_CEILING,
            "ROUND_FLOOR": ROUND_FLOOR,
            "ROUND_05UP": ROUND_05UP,
        }
        if policy.rounding_mode not in rounding_modes:
            raise FXMissingRateError(
                f"unsupported FX rounding mode: {policy.rounding_mode}"
            )
        quantizer = Decimal("1").scaleb(-int(policy.rounding_scale))
        return value.quantize(quantizer, rounding=rounding_modes[policy.rounding_mode])
