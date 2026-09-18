"""Service adapter for FX conversion and rate import workflows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from src.calculation.conversion.fx import FXConverter, FXMissingRateError


class FXService:
    """Thin service wrapper around FX repository adapters and domain conversion."""

    REQUIRED_RATE_FIELDS = ("quote_currency", "rate_date", "rate_value")

    def __init__(self, repository):
        self.repository = repository

    def convert(
        self,
        value,
        *,
        from_currency: str | None,
        to_currency: str | None,
        value_date,
        period_start,
        period_end,
        policy_id: str,
    ):
        policy = self.repository.get_policy(policy_id)
        if policy is None:
            raise FXMissingRateError(f"FX policy not found: {policy_id}")

        return FXConverter(self.repository).convert(
            Decimal(str(value)),
            from_currency=self._normalize_currency(from_currency),
            to_currency=self._normalize_currency(to_currency),
            value_date=self._parse_optional_date(value_date, "value_date"),
            period_start=self._parse_optional_date(period_start, "period_start"),
            period_end=self._parse_optional_date(period_end, "period_end"),
            policy=policy,
        )

    def import_rates_csv(
        self,
        rows,
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        source_hash: str,
        created_by: str | None = None,
        source_name: str | None = None,
        source_url: str | None = None,
        batch_metadata: dict[str, Any] | None = None,
    ):
        return self.import_rates_csv_result(
            rows,
            provider=provider,
            rate_type=rate_type,
            base_currency=base_currency,
            source_hash=source_hash,
            created_by=created_by,
            source_name=source_name,
            source_url=source_url,
            batch_metadata=batch_metadata,
        ).batch

    def import_rates_csv_result(
        self,
        rows,
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        source_hash: str,
        created_by: str | None = None,
        source_name: str | None = None,
        source_url: str | None = None,
        batch_metadata: dict[str, Any] | None = None,
    ):
        parsed_rows = [
            self._parse_rate_row(row, index) for index, row in enumerate(rows, start=1)
        ]
        base_currency_normalized = self._normalize_currency(base_currency)
        self._reject_duplicate_rate_rows(
            parsed_rows,
            provider=provider,
            rate_type=rate_type,
            base_currency=base_currency_normalized,
        )
        save_kwargs = {
            "provider": provider,
            "rate_type": rate_type,
            "base_currency": base_currency_normalized,
            "source_hash": source_hash,
            "created_by": created_by,
        }
        if source_name is not None:
            save_kwargs["source_name"] = source_name
        if source_url is not None:
            save_kwargs["source_url"] = source_url
        if batch_metadata is not None:
            save_kwargs["batch_metadata"] = batch_metadata
        return self.repository.save_rate_batch_result(parsed_rows, **save_kwargs)

    def coverage(
        self,
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        quote_currency: str,
        start,
        end,
    ):
        return self.repository.list_rate_coverage(
            provider=provider,
            rate_type=rate_type,
            base_currency=self._normalize_currency(base_currency),
            quote_currency=self._normalize_currency(quote_currency),
            start=self._parse_required_date(start, "start"),
            end=self._parse_required_date(end, "end"),
        )

    def _parse_rate_row(self, row: dict[str, Any], index: int) -> dict[str, Any]:
        for field in self.REQUIRED_RATE_FIELDS:
            if field not in row or row[field] in (None, ""):
                raise ValueError(f"FX rate row {index} missing required field: {field}")

        parsed = {
            "quote_currency": self._normalize_currency(row["quote_currency"]),
            "rate_date": self._parse_required_date(row["rate_date"], "rate_date"),
            "rate_value": self._parse_decimal(row["rate_value"], "rate_value"),
        }
        if "observed_at" in row:
            parsed["observed_at"] = row["observed_at"]
        if "rate_metadata" in row:
            parsed["rate_metadata"] = row["rate_metadata"]
        return parsed

    def _reject_duplicate_rate_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
    ) -> None:
        seen: set[tuple[str, str, str, str, date]] = set()
        for index, row in enumerate(rows, start=1):
            key = (
                provider,
                rate_type,
                base_currency,
                row["quote_currency"],
                row["rate_date"],
            )
            if key in seen:
                raise ValueError(
                    "duplicate FX rate row "
                    f"{index} for {provider}/{rate_type}/"
                    f"{base_currency}/{row['quote_currency']}/{row['rate_date']}"
                )
            seen.add(key)

    def _parse_required_date(self, value, field_name: str) -> date:
        parsed = self._parse_optional_date(value, field_name)
        if parsed is None:
            raise ValueError(f"{field_name} is required")
        return parsed

    def _parse_optional_date(self, value, field_name: str) -> date | None:
        if value is None or value == "":
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO date") from exc

    def _parse_decimal(self, value, field_name: str) -> Decimal:
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field_name} must be a decimal") from exc
        if decimal_value <= 0:
            raise ValueError(f"{field_name} must be positive")
        return decimal_value

    def _normalize_currency(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        return normalized or None
