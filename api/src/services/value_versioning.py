"""Deterministic helpers for revision-backed SDS value history."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

CONTEXT_HASH_RECIPE_VERSION = "sds-value-context-v1"
CONTEXT_HASH_RECIPE_VERSION_V2 = "sds-value-context-v2"
SDS_CANONICAL_OPERATIONAL_RELEASE_ID = "SDS_CANONICAL_OPERATIONAL_V1"
SOURCE_OBSERVATION_STANDARD_DIRECT_LEGACY = "standard_direct_legacy"
SOURCE_OBSERVATION_CANONICAL_OPERATIONAL = "canonical_operational"
SOURCE_OBSERVATION_STANDARD_BOUND_EVIDENCE = "standard_bound_evidence"
SOURCE_OBSERVATION_TYPES = frozenset(
    {
        SOURCE_OBSERVATION_STANDARD_DIRECT_LEGACY,
        SOURCE_OBSERVATION_CANONICAL_OPERATIONAL,
        SOURCE_OBSERVATION_STANDARD_BOUND_EVIDENCE,
    }
)

VALUE_REVISION_STATES = frozenset(
    {
        "legacy_current",
        "draft",
        "submitted",
        "validated",
        "approved",
        "locked",
        "published",
        "reported",
        "superseded",
        "corrected",
        "restated",
        "migrated",
        "system_recalc",
        "redacted_input",
        "invalidated",
        "voided",
        "rejected",
        "redacted",
    }
)

VALUE_STATE_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "legacy_current": frozenset(
        {"approved", "validated", "superseded", "corrected", "restated", "redacted"}
    ),
    "draft": frozenset({"submitted", "rejected", "voided", "redacted"}),
    "submitted": frozenset({"validated", "rejected", "voided", "redacted"}),
    "validated": frozenset({"approved", "rejected", "voided", "redacted"}),
    "approved": frozenset(
        {
            "locked",
            "superseded",
            "corrected",
            "restated",
            "migrated",
            "voided",
            "redacted",
        }
    ),
    "locked": frozenset(
        {"published", "reported", "corrected", "restated", "voided", "redacted"}
    ),
    "published": frozenset({"reported", "corrected", "restated", "redacted"}),
    "reported": frozenset({"corrected", "restated", "redacted_input", "redacted"}),
    "superseded": frozenset({"redacted"}),
    "corrected": frozenset({"approved", "locked", "published", "reported", "redacted"}),
    "restated": frozenset({"approved", "locked", "published", "reported", "redacted"}),
    "migrated": frozenset(
        {"approved", "locked", "published", "reported", "superseded", "redacted"}
    ),
    "system_recalc": frozenset(
        {"approved", "locked", "published", "reported", "invalidated", "redacted"}
    ),
    "redacted_input": frozenset({"invalidated", "restated", "redacted"}),
    "invalidated": frozenset({"voided", "redacted"}),
    "voided": frozenset({"redacted"}),
    "rejected": frozenset({"redacted"}),
    "redacted": frozenset(),
}

_LOCALE_OR_GROUPING_PATTERN = re.compile(r"[,_]")


class ValueVersioningError(ValueError):
    """Raised when value-versioning canonicalization or state rules fail."""


@dataclass(frozen=True)
class ValueContextIdentity:
    """Stable identity fields for one tenant value context.

    Expected unit and currency are validation policy, so they are stored on the
    context but intentionally excluded from the context hash identity payload.
    """

    tenant_id: str
    entity_id: str
    reporting_period_id: str
    period_start: date | None
    period_end: date | None
    period_close_date: date | None
    period_type: str
    reporting_boundary_id: str
    sds_indicator_id: str | None
    indicator_identifier: str
    standard_release_id: str
    standard_datapoint_id: str
    dimensions: Mapping[str, Any]
    expected_unit: str | None
    expected_currency: str | None
    value_kind: str
    scenario_basis: str = "actual"
    canonical_concept_id: int | None = None
    canonical_uri: str | None = None
    source_observation_type: str = SOURCE_OBSERVATION_STANDARD_DIRECT_LEGACY
    context_hash_recipe_version: str = CONTEXT_HASH_RECIPE_VERSION

    def context_payload(
        self,
        *,
        recipe_version: str | None = None,
    ) -> dict[str, Any]:
        resolved_recipe_version = _recipe_version(
            recipe_version or self.context_hash_recipe_version
        )
        source_observation_type = _source_observation_type(self.source_observation_type)
        identity_fields = {
            "tenant_id": _required_text(self.tenant_id, "tenant_id"),
            "entity_id": _required_text(self.entity_id, "entity_id"),
            "reporting_period_id": _required_text(
                self.reporting_period_id,
                "reporting_period_id",
            ),
            "period_start": _date_or_none(self.period_start),
            "period_end": _date_or_none(self.period_end),
            "period_close_date": _date_or_none(self.period_close_date),
            "period_type": _required_text(self.period_type, "period_type"),
            "reporting_boundary_id": _required_text(
                self.reporting_boundary_id,
                "reporting_boundary_id",
            ),
            "sds_indicator_id": _text_or_none(self.sds_indicator_id),
            "indicator_identifier": _required_text(
                self.indicator_identifier,
                "indicator_identifier",
            ),
            "standard_release_id": _required_text(
                self.standard_release_id,
                "standard_release_id",
            ),
            "standard_datapoint_id": _required_text(
                self.standard_datapoint_id,
                "standard_datapoint_id",
            ),
            "dimensions": normalize_dimensions(self.dimensions),
            "scenario_basis": _required_text(
                self.scenario_basis or "actual",
                "scenario_basis",
            ),
            "value_kind": _required_text(self.value_kind, "value_kind"),
        }
        if resolved_recipe_version == CONTEXT_HASH_RECIPE_VERSION_V2:
            identity_fields["canonical_uri"] = _text_or_none(self.canonical_uri)
            identity_fields["source_observation_type"] = source_observation_type
        return {
            "context_hash_recipe_version": resolved_recipe_version,
            "identity_fields": identity_fields,
        }


def canonical_numeric_string(value: Any) -> str:
    """Return the canonical decimal string used by value hashes."""

    if isinstance(value, bool):
        raise ValueVersioningError("boolean is not a numeric value")
    if isinstance(value, float) and (
        value != value or value in {float("inf"), float("-inf")}
    ):
        raise ValueVersioningError("NaN and infinity are not valid SDS numeric values")

    raw = str(value).strip()
    if not raw:
        raise ValueVersioningError("numeric value cannot be blank")
    if _LOCALE_OR_GROUPING_PATTERN.search(raw):
        raise ValueVersioningError("locale/grouping separators are not accepted")
    if raw.lower().startswith(
        ("0x", "0o", "0b", "+0x", "+0o", "+0b", "-0x", "-0o", "-0b")
    ):
        raise ValueVersioningError("non-base-10 numeric encodings are not accepted")

    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueVersioningError(f"invalid numeric value: {value}") from exc
    if not parsed.is_finite():
        raise ValueVersioningError("NaN and infinity are not valid SDS numeric values")
    if parsed == 0:
        return "0"

    text = format(parsed.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text.startswith("+"):
        text = text[1:]
    if text.startswith("."):
        text = "0" + text
    if text.startswith("-."):
        text = text.replace("-.", "-0.", 1)
    return text


def normalize_dimensions(dimensions: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize dimension/member identity with stable key and member ordering."""

    normalized: dict[str, Any] = {}
    for raw_key, raw_value in sorted(dimensions.items(), key=lambda item: str(item[0])):
        key = _required_text(str(raw_key).strip(), "dimension_id")
        if isinstance(raw_value, (list, tuple, set, frozenset)):
            normalized[key] = sorted(str(value).strip() for value in raw_value)
        elif raw_value is None:
            normalized[key] = None
        else:
            normalized[key] = str(raw_value).strip()
    return normalized


def build_context_hash(
    identity: ValueContextIdentity,
    *,
    recipe_version: str | None = None,
) -> str:
    payload = identity.context_payload(recipe_version=recipe_version)
    return canonical_json_hash(payload)


def canonical_json_hash(payload: Mapping[str, Any]) -> str:
    normalized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def assert_state_transition_allowed(from_state: str, to_state: str) -> None:
    source = str(from_state or "").strip()
    target = str(to_state or "").strip()
    if source not in VALUE_REVISION_STATES:
        raise ValueVersioningError(f"unknown value revision state: {source}")
    if target not in VALUE_REVISION_STATES:
        raise ValueVersioningError(f"unknown value revision state: {target}")
    if target not in VALUE_STATE_TRANSITIONS[source]:
        raise ValueVersioningError(
            f"value revision transition is not allowed: {source} -> {target}"
        )


def _date_or_none(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _required_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueVersioningError(f"{field_name} is required")
    return text


def _text_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _recipe_version(value: str) -> str:
    text = _required_text(value, "context_hash_recipe_version")
    if text not in {CONTEXT_HASH_RECIPE_VERSION, CONTEXT_HASH_RECIPE_VERSION_V2}:
        raise ValueVersioningError(f"unknown context hash recipe version: {text}")
    return text


def _source_observation_type(value: str) -> str:
    text = _required_text(value, "source_observation_type")
    if text not in SOURCE_OBSERVATION_TYPES:
        raise ValueVersioningError(f"unknown source observation type: {text}")
    return text
