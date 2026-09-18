"""Strict CSV contract for operational ESG value imports."""

from __future__ import annotations

import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional, TextIO

from pydantic import ValidationError

from src.api.models import ValueCreate, ValueScalar

REQUIRED_VALUE_COLUMNS = ("concept", "entity", "period", "value", "unit")
OPTIONAL_VALUE_COLUMNS = (
    "metadata_json",
    "external_key",
    "value_type",
    "currency",
    "value_date",
    "period_start",
    "period_end",
    "expected_unit",
    "expected_currency",
    "fx_policy_id",
)
ALLOWED_VALUE_COLUMNS = REQUIRED_VALUE_COLUMNS + OPTIONAL_VALUE_COLUMNS


class ValueCsvContractError(ValueError):
    """Raised when a values CSV violates the operational import contract."""


def load_values_from_csv(
    csv_path: Path,
    *,
    default_entity: Optional[str] = None,
    default_metadata: Optional[dict[str, Any]] = None,
    max_rows: int | None = None,
) -> list[ValueCreate]:
    """Parse and validate a values CSV using the operational import contract."""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return load_values_from_handle(
            handle,
            default_entity=default_entity,
            default_metadata=default_metadata,
            max_rows=max_rows,
        )


def load_values_from_handle(
    handle: TextIO,
    *,
    default_entity: Optional[str] = None,
    default_metadata: Optional[dict[str, Any]] = None,
    max_rows: int | None = None,
) -> list[ValueCreate]:
    """Parse and validate a values CSV from an already-open text handle."""
    reader = csv.DictReader(handle)
    fieldnames = tuple(
        name.strip() for name in (reader.fieldnames or []) if name is not None
    )
    _validate_headers(fieldnames)

    rows: list[ValueCreate] = []
    for row_number, row in enumerate(reader, start=2):
        if max_rows is not None and max_rows > 0 and len(rows) >= max_rows:
            raise ValueCsvContractError(
                f"Values CSV exceeds configured row limit of {max_rows}."
            )
        try:
            payload = {
                "concept": (row.get("concept") or "").strip(),
                "entity": ((row.get("entity") or "").strip() or default_entity or ""),
                "period": (row.get("period") or "").strip(),
                "external_key": (row.get("external_key") or "").strip() or None,
                "value": None,
                "unit": (row.get("unit") or "").strip(),
                "currency": _normalize_currency(row.get("currency"), "currency"),
                "expected_unit": (row.get("expected_unit") or "").strip() or None,
                "expected_currency": _normalize_currency(
                    row.get("expected_currency"), "expected_currency"
                ),
                "fx_policy_id": (row.get("fx_policy_id") or "").strip() or None,
                "value_date": (row.get("value_date") or "").strip() or None,
                "period_start": (row.get("period_start") or "").strip() or None,
                "period_end": (row.get("period_end") or "").strip() or None,
                "metadata": _build_metadata(row.get("metadata_json"), default_metadata),
            }
            _validate_conversion_fields(payload)
            payload["value"], payload["value_type"] = _parse_value(
                row.get("value"), row.get("value_type")
            )
            rows.append(ValueCreate.model_validate(payload))
        except (ValidationError, ValueCsvContractError) as error:
            raise ValueCsvContractError(
                f"Invalid values CSV row {row_number}: {error}"
            ) from error

    if not rows:
        raise ValueCsvContractError("Values CSV is empty.")

    return rows


def _validate_headers(headers: tuple[str, ...]) -> None:
    if not headers:
        raise ValueCsvContractError("Values CSV has no header row.")

    duplicates = sorted({header for header in headers if headers.count(header) > 1})
    if duplicates:
        raise ValueCsvContractError(
            "Values CSV contains duplicate columns: " + ", ".join(duplicates)
        )

    missing = [column for column in REQUIRED_VALUE_COLUMNS if column not in headers]
    if missing:
        raise ValueCsvContractError(
            "Values CSV is missing required columns: " + ", ".join(missing)
        )

    unexpected = [column for column in headers if column not in ALLOWED_VALUE_COLUMNS]
    if unexpected:
        raise ValueCsvContractError(
            "Values CSV contains unsupported columns for the operational import contract: "
            + ", ".join(unexpected)
        )


def _normalize_currency(raw_value: Optional[str], field_name: str) -> Optional[str]:
    value = (raw_value or "").strip()
    if not value:
        return None
    normalized = value.upper()
    if len(normalized) != 3 or not normalized.isalpha() or not normalized.isascii():
        raise ValueCsvContractError(f"{field_name} must be a 3-letter currency code.")
    return normalized


def _validate_conversion_fields(payload: dict[str, Any]) -> None:
    expected_currency = payload.get("expected_currency")
    fx_policy_id = payload.get("fx_policy_id")
    has_period_start = bool(payload.get("period_start"))
    has_period_end = bool(payload.get("period_end"))
    if has_period_start != has_period_end:
        raise ValueCsvContractError(
            "period_start and period_end must be supplied together."
        )
    if expected_currency and not fx_policy_id:
        raise ValueCsvContractError("expected_currency requires fx_policy_id.")
    if fx_policy_id and not expected_currency:
        raise ValueCsvContractError("fx_policy_id requires expected_currency.")
    if not expected_currency:
        return

    source_currency = payload.get("currency")
    if not source_currency:
        raise ValueCsvContractError("expected_currency requires row-level currency.")
    if source_currency == expected_currency:
        return

    has_value_date = bool(payload.get("value_date"))
    if not has_value_date and not (has_period_start and has_period_end):
        raise ValueCsvContractError(
            "FX values require value_date or period_start plus period_end."
        )


def _build_metadata(
    raw_metadata: Optional[str], default_metadata: Optional[dict[str, Any]]
) -> dict[str, Any]:
    metadata = dict(default_metadata or {})
    text = (raw_metadata or "").strip()
    if not text:
        return metadata
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueCsvContractError(
            f"Invalid metadata_json payload: {error.msg}"
        ) from error

    if not isinstance(parsed, dict):
        raise ValueCsvContractError("metadata_json must decode to a JSON object.")

    metadata.update(parsed)
    return metadata


def _parse_value(
    raw_value: Optional[str], raw_value_type: Optional[str]
) -> tuple[ValueScalar, str]:
    value = (raw_value or "").strip()
    value_type = _normalize_value_type(raw_value_type)
    if not value:
        raise ValueCsvContractError("value must not be empty.")

    if value_type == "auto":
        lowered = value.lower()
        if lowered in {"true", "false", "yes", "no"}:
            return _parse_boolean(value), "boolean"
        numeric = _try_parse_decimal(value)
        return (numeric, "numeric") if numeric is not None else (value, "narrative")

    if value_type == "numeric":
        numeric = _try_parse_decimal(value)
        if numeric is None:
            raise ValueCsvContractError(
                "value_type 'numeric' requires a numeric value."
            )
        return numeric, "numeric"

    if value_type == "boolean":
        return _parse_boolean(value), "boolean"

    if value_type in {"narrative", "semi-narrative"}:
        return value, value_type

    raise ValueCsvContractError(
        "value_type must be one of: numeric, boolean, narrative, semi-narrative, or auto."
    )


def _normalize_value_type(raw_value_type: Optional[str]) -> str:
    value_type = (raw_value_type or "").strip().lower().replace("_", "-")
    if value_type in {"", "auto"}:
        return "auto"
    if value_type in {"number", "numeric", "decimal"}:
        return "numeric"
    if value_type in {"boolean", "bool"}:
        return "boolean"
    if value_type in {"text", "string", "narrative"}:
        return "narrative"
    if value_type in {"semi-narrative", "seminarrative"}:
        return "semi-narrative"
    return value_type


def _try_parse_decimal(value: str) -> Optional[Decimal]:
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _parse_boolean(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"true", "yes", "1"}:
        return True
    if lowered in {"false", "no", "0"}:
        return False
    raise ValueCsvContractError("Boolean values must be true/false, yes/no, or 1/0.")
