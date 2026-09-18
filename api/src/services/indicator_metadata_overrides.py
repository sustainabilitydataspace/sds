"""Authoritative corrections for known standard metadata defects.

These overrides are intentionally small and explicit. They patch cases where
source extraction or Atomizer handoff metadata classified a quantitative
standard datapoint as text, or assigned the wrong unit dimension.
"""

from __future__ import annotations

from typing import Any, MutableMapping

_ESRS_E1_5_ENERGY_QUANTITY_CODES = frozenset(
    {
        "E1-5_01",
        "E1-5_02",
        "E1-5_03",
        "E1-5_05",
        "E1-5_06",
        "E1-5_07",
        "E1-5_08",
        "E1-5_10",
        "E1-5_11",
        "E1-5_12",
        "E1-5_13",
        "E1-5_14",
        "E1-5_16",
        "E1-5_17",
    }
)

_ESRS_DESCRIPTION_OVERRIDES = {
    "E1-5_13": (
        "Fuel consumption from other fossil sources in own operations "
        "(energy content), as required by ESRS E1-5, paragraph 38(d) and "
        "AR 34, table row (4)."
    ),
}


def apply_indicator_metadata_overrides(
    record: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Apply bounded standard metadata corrections in-place and return ``record``."""

    code = _canonical_esrs_code(
        _first_non_empty(
            record,
            "code_esrs",
            "codeESRS",
            "DatapointCode",
            "datapoint_code",
        )
    )
    if code not in _ESRS_E1_5_ENERGY_QUANTITY_CODES:
        return record

    _set_if_key_present(record, ("unit_name", "unitName", "DefaultUnit"), "MWh")
    _set_if_key_present(record, ("unit_type", "unitType", "UnitType"), "Energy")
    _set_if_key_present(record, ("value_type", "valueType", "DataType"), "numeric")

    description = _ESRS_DESCRIPTION_OVERRIDES.get(code)
    if description:
        _set_if_key_present(record, ("description", "Description"), description)

    return record


def _first_non_empty(record: MutableMapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _set_if_key_present(
    record: MutableMapping[str, Any], keys: tuple[str, ...], value: str
) -> None:
    existing = [key for key in keys if key in record]
    if existing:
        for key in existing:
            record[key] = value
        return
    record[keys[0]] = value


def _canonical_esrs_code(value: str) -> str:
    token = str(value or "").strip().upper()
    if token.startswith("ESRS "):
        token = token[5:].strip()
    return token.replace("-", "-", 1)


__all__ = ["apply_indicator_metadata_overrides"]
