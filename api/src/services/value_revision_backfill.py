"""Backfill legacy operational values into revision-backed SDS value tables."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping

from sqlalchemy.orm import Session

from src.database.models import ESGValue, ValueRevision
from src.services.value_revision_store import ValueRevisionInput, ValueRevisionStore
from src.services.value_versioning import (
    CONTEXT_HASH_RECIPE_VERSION,
    CONTEXT_HASH_RECIPE_VERSION_V2,
    SDS_CANONICAL_OPERATIONAL_RELEASE_ID,
    SOURCE_OBSERVATION_CANONICAL_OPERATIONAL,
    SOURCE_OBSERVATION_STANDARD_DIRECT_LEGACY,
    ValueContextIdentity,
    ValueVersioningError,
    canonical_json_hash,
)

LEGACY_VALUE_SOURCE_SYSTEM = "legacy_esg_values"
LEGACY_VALUE_STATE = "legacy_current"
LEGACY_STANDARD_RELEASE_ID = "legacy_current"
LEGACY_BOUNDARY_ID = "legacy_current_boundary"
VALUE_RESPONSE_METADATA_KEY = "value_response"


@dataclass
class ValueRevisionBackfillReport:
    dry_run: bool
    tenant_id: str
    scanned: int = 0
    created: int = 0
    skipped_existing: int = 0
    failed: int = 0
    committed: bool = False
    errors: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "tenant_id": self.tenant_id,
            "scanned": self.scanned,
            "created": self.created,
            "skipped_existing": self.skipped_existing,
            "failed": self.failed,
            "committed": self.committed,
            "errors": self.errors,
        }


def backfill_esg_values_to_revisions(
    *,
    db: Session,
    tenant_id: str,
    dry_run: bool = True,
    created_by: str = "value_revision_backfill",
    limit: int | None = None,
) -> ValueRevisionBackfillReport:
    """Create revision rows for current-state ``esg_values`` records.

    The backfill is idempotent by ``tenant_id`` plus legacy source observation key.
    It intentionally keeps ``sds_indicator_id`` nullable unless the legacy row has
    an explicit metadata mapping to an SDS indicator primary key.
    """

    report = ValueRevisionBackfillReport(
        dry_run=dry_run,
        tenant_id=_required_text(tenant_id, "tenant_id"),
    )
    revision_store = ValueRevisionStore(db)
    query = db.query(ESGValue).order_by(ESGValue.period.asc(), ESGValue.id.asc())
    if limit is not None:
        query = query.limit(max(int(limit), 0))

    try:
        for row in query.all():
            report.scanned += 1
            external_key = _legacy_external_key(row)
            if _revision_exists(db, report.tenant_id, external_key):
                report.skipped_existing += 1
                continue
            try:
                revision_store.append_revision(
                    revision_input_for_esg_value(
                        row,
                        tenant_id=report.tenant_id,
                        external_key=external_key,
                        created_by=created_by,
                    ),
                    commit=False,
                )
                report.created += 1
            except (ValueError, ValueVersioningError) as exc:
                report.failed += 1
                report.errors.append({"value_id": str(row.id), "error": str(exc)})

        if dry_run:
            db.rollback()
        else:
            db.commit()
            report.committed = True
        return report
    except Exception:
        db.rollback()
        raise


def revision_input_for_esg_value(
    row: ESGValue,
    *,
    tenant_id: str,
    external_key: str | None = None,
    use_legacy_external_key_default: bool = True,
    state: str = LEGACY_VALUE_STATE,
    source_system: str = LEGACY_VALUE_SOURCE_SYSTEM,
    revision_provenance: str | None = LEGACY_VALUE_STATE,
    created_by: str,
) -> ValueRevisionInput:
    metadata = _metadata(row.value_metadata)
    value_kind = str(row.value_type or "numeric").strip() or "numeric"
    canonical_uri = _text_or_none(metadata.get("canonical_uri"))
    canonical_concept_id = _int_or_none(metadata.get("canonical_concept_id"))
    context_hash_recipe_version = str(
        metadata.get("context_hash_recipe_version")
        or (
            CONTEXT_HASH_RECIPE_VERSION_V2
            if canonical_uri
            else CONTEXT_HASH_RECIPE_VERSION
        )
    )
    source_observation_type = str(
        metadata.get("source_observation_type")
        or (
            SOURCE_OBSERVATION_CANONICAL_OPERATIONAL
            if canonical_uri
            else SOURCE_OBSERVATION_STANDARD_DIRECT_LEGACY
        )
    )
    indicator_identifier = str(
        metadata.get("indicator_identifier") or canonical_uri or row.concept
    )
    standard_release_id = str(
        metadata.get("standard_release_id")
        or (
            SDS_CANONICAL_OPERATIONAL_RELEASE_ID
            if canonical_uri
            else LEGACY_STANDARD_RELEASE_ID
        )
    )
    standard_datapoint_id = str(
        metadata.get("standard_datapoint_id") or canonical_uri or row.concept
    )
    identity = ValueContextIdentity(
        tenant_id=tenant_id,
        entity_id=row.entity,
        reporting_period_id=str(
            metadata.get("reporting_period_id") or row.period.isoformat()
        ),
        period_start=row.period_start or row.period,
        period_end=row.period_end or row.period,
        period_close_date=_date_or_none(metadata.get("period_close_date")),
        period_type=str(metadata.get("period_type") or _infer_period_type(row)),
        reporting_boundary_id=str(
            metadata.get("reporting_boundary_id") or LEGACY_BOUNDARY_ID
        ),
        sds_indicator_id=_text_or_none(metadata.get("sds_indicator_id")),
        indicator_identifier=indicator_identifier,
        standard_release_id=standard_release_id,
        standard_datapoint_id=standard_datapoint_id,
        dimensions=_dimensions(metadata),
        expected_unit=row.unit,
        expected_currency=row.currency,
        value_kind=value_kind,
        scenario_basis=str(metadata.get("scenario_basis") or "actual"),
        canonical_concept_id=canonical_concept_id,
        canonical_uri=canonical_uri,
        source_observation_type=source_observation_type,
        context_hash_recipe_version=context_hash_recipe_version,
    )
    return ValueRevisionInput(
        context_identity=identity,
        value=_value_for_kind(row, value_kind),
        value_kind=value_kind,
        unit=row.unit,
        currency=row.currency,
        state=state,
        source_system=source_system,
        source_record_id=row.id,
        external_key=(
            external_key
            if external_key is not None
            else (
                _legacy_external_key(row)
                if use_legacy_external_key_default
                else row.external_key
            )
        ),
        evidence_hash=_text_or_none(metadata.get("evidence_hash")),
        materiality_metadata=_materiality_metadata(row, metadata),
        conversion_trace=row.conversion_trace,
        revision_provenance=revision_provenance,
        source_payload_hash=_source_payload_hash(
            row,
            metadata,
            source_system=source_system,
        ),
        original_value=row.original_value,
        original_unit=row.original_unit,
        original_currency=row.original_currency,
        created_by=created_by,
    )


def _revision_exists(db: Session, tenant_id: str, external_key: str) -> bool:
    return (
        db.query(ValueRevision.id)
        .filter(
            ValueRevision.tenant_id == tenant_id,
            ValueRevision.source_system == LEGACY_VALUE_SOURCE_SYSTEM,
            ValueRevision.external_key == external_key,
        )
        .first()
        is not None
    )


def _legacy_external_key(row: ESGValue) -> str:
    return str(row.external_key or f"legacy-esg-value:{row.id}").strip()


def _metadata(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _dimensions(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw = metadata.get("dimensions") or metadata.get("dimensions_json") or {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _value_for_kind(row: ESGValue, value_kind: str) -> Any:
    kind = value_kind.lower()
    if kind == "boolean":
        return row.boolean_value
    if kind in {"narrative", "semi-narrative", "text"}:
        return row.text_value
    return row.value


def _infer_period_type(row: ESGValue) -> str:
    start = row.period_start or row.period
    end = row.period_end or row.period
    return "point" if start == end else "custom"


def _date_or_none(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _mapping_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _materiality_metadata(row: ESGValue, metadata: Mapping[str, Any]) -> dict[str, Any]:
    materiality = {
        key: value for key, value in metadata.items() if key != "materiality_metadata"
    }
    materiality.update(_mapping_or_none(metadata.get("materiality_metadata")) or {})
    materiality[VALUE_RESPONSE_METADATA_KEY] = {
        "conversion_applied": bool(row.conversion_applied),
        "currency_conversion_applied": bool(row.currency_conversion_applied),
        "value_date": _date_payload(row.value_date),
    }
    return materiality


def _date_payload(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _source_payload_hash(
    row: ESGValue,
    metadata: Mapping[str, Any],
    *,
    source_system: str,
) -> str:
    return canonical_json_hash(
        {
            "source": source_system,
            "id": row.id,
            "concept": row.concept,
            "entity": row.entity,
            "period": row.period.isoformat(),
            "external_key": row.external_key,
            "value_type": row.value_type,
            "value": str(row.value) if row.value is not None else None,
            "text_value": row.text_value,
            "boolean_value": row.boolean_value,
            "unit": row.unit,
            "currency": row.currency,
            "metadata": dict(metadata),
        }
    )


def _required_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)
