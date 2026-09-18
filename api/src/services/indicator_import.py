"""Indicator register CSV validation and import orchestration."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from src.database.models import Indicator
from src.database.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)
from src.database.repositories.indicator_repository import IndicatorRepository
from src.services.dataset_history import persist_dataset_snapshot
from src.services.dataset_manifest import build_dataset_manifest
from src.services.dataset_serialization import serialize_indicator
from src.services.indicator_metadata_overrides import apply_indicator_metadata_overrides
from src.services.semantic_concept_projector import SemanticConceptProjector

URN_PATTERN = re.compile(r"^urn:sds:reg:[a-z0-9]+:[a-z0-9_]+$")
URN_PREFIX_PATTERN = re.compile(r"^urn:sds:reg:([^:]+):(.+)$", re.IGNORECASE)
NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")
MULTI_UNDERSCORE_PATTERN = re.compile(r"_+")

DEFAULT_MAX_ERRORS = 200
DEFAULT_MAX_ROWS = 10_000

REQUIRED_REGISTER_COLUMNS = (
    "identifier",
    "title",
    "indicator",
    "description",
    "dimension",
    "unitName",
    "unitType",
    "periodicity",
    "periodType",
    "sourceRef",
    "codeESRS",
    "codeGRI",
    "codeGRI_expanded",
    "evidencePath",
    "sourceRow",
    "owner",
    "accessRights",
    "validationMethod",
    "doubleMateriality",
    "valueType",
)

COLUMN_MAP = {
    "identifier": "identifier",
    "title": "title",
    "indicator": "indicator_name",
    "description": "description",
    "dimension": "dimension",
    "unitName": "unit_name",
    "unitType": "unit_type",
    "periodicity": "periodicity",
    "periodType": "period_type",
    "sourceRef": "source_ref",
    "codeESRS": "code_esrs",
    "codeGRI": "code_gri",
    "codeGRI_expanded": "code_gri_expanded",
    "evidencePath": "evidence_path",
    "sourceRow": "source_row",
    "owner": "owner",
    "accessRights": "access_rights",
    "validationMethod": "validation_method",
    "doubleMateriality": "double_materiality",
    "valueType": "value_type",
}


class IndicatorCsvContractError(ValueError):
    """Raised when a register CSV violates the import contract."""


class IndicatorImportValidationError(ValueError):
    """Raised when an apply operation receives an invalid import plan."""

    def __init__(self, plan: "IndicatorImportPlan"):
        super().__init__("Indicator import validation failed")
        self.plan = plan


@dataclass(frozen=True)
class IndicatorImportRowError:
    row_number: int
    identifier: Optional[str]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "identifier": self.identifier,
            "message": self.message,
        }


@dataclass
class IndicatorImportPlan:
    """Validated register import plan plus non-public normalized records."""

    total_rows: int
    accepted_rows: int
    rejected_rows: int
    created_rows: int
    updated_rows: int
    unchanged_rows: int
    normalized_rows: int
    errors: list[IndicatorImportRowError] = field(default_factory=list)
    errors_truncated: bool = False
    normalized_samples: list[dict[str, Any]] = field(default_factory=list)
    source_sha256: Optional[str] = None
    source_size_bytes: Optional[int] = None
    records: list[dict[str, Any]] = field(default_factory=list)
    legacy_migrations: dict[str, str] = field(default_factory=dict)
    migrated_legacy_ids: int = 0
    removed_legacy_duplicates: int = 0
    committed: bool = False
    snapshot_id: Optional[int] = None
    manifest_hash: Optional[str] = None
    semantic_projection: Optional[dict[str, Any]] = None

    @property
    def valid(self) -> bool:
        return self.rejected_rows == 0

    def to_result_body(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "total_rows": self.total_rows,
            "accepted_rows": self.accepted_rows,
            "rejected_rows": self.rejected_rows,
            "created_rows": self.created_rows,
            "updated_rows": self.updated_rows,
            "unchanged_rows": self.unchanged_rows,
            "normalized_rows": self.normalized_rows,
            "migrated_legacy_ids": self.migrated_legacy_ids,
            "removed_legacy_duplicates": self.removed_legacy_duplicates,
            "committed": self.committed,
            "snapshot_id": self.snapshot_id,
            "manifest_hash": self.manifest_hash,
            "semantic_projection": self.semantic_projection,
            "source_sha256": self.source_sha256,
            "source_size_bytes": self.source_size_bytes,
            "normalized_samples": self.normalized_samples,
            "errors": [error.to_dict() for error in self.errors],
            "errors_truncated": self.errors_truncated,
        }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_framework_token(framework: str | None) -> str:
    token = NON_ALNUM_PATTERN.sub("", (framework or "").strip().lower())
    return token or "unknown"


def canonicalize_identifier_segment(
    value: str | None, *, fallback: str = "item"
) -> str:
    normalized = NON_ALNUM_PATTERN.sub("_", (value or "").strip().lower())
    normalized = MULTI_UNDERSCORE_PATTERN.sub("_", normalized).strip("_")
    return normalized or fallback


def build_indicator_identifier(
    framework: str | None,
    code: str | None,
    *,
    fallback: str = "item",
) -> str:
    namespace = normalize_framework_token(framework)
    segment = canonicalize_identifier_segment(code, fallback=fallback)
    return f"urn:sds:reg:{namespace}:{segment}"


def canonicalize_indicator_identifier(identifier: str | None) -> str:
    raw = (identifier or "").strip()
    match = URN_PREFIX_PATTERN.match(raw)
    if not match:
        return raw
    namespace, segment = match.groups()
    return build_indicator_identifier(namespace, segment)


def is_valid_indicator_identifier(identifier: str | None) -> bool:
    return bool(URN_PATTERN.fullmatch((identifier or "").strip()))


def validate_urn(identifier: str | None) -> bool:
    return is_valid_indicator_identifier(identifier)


def canonicalize_identifier_for_row(row: dict[str, Any]) -> str:
    raw_identifier = str(row.get("identifier", "") or "").strip()
    if raw_identifier.lower().startswith("urn:sds:reg:"):
        canonical = canonicalize_indicator_identifier(raw_identifier)
        if canonical:
            return canonical

    code_esrs = str(row.get("codeESRS") or row.get("code_esrs") or "").strip()
    if code_esrs:
        return build_indicator_identifier("esrs", code_esrs)

    code_gri = str(row.get("codeGRI") or row.get("code_gri") or "").strip()
    if code_gri:
        return build_indicator_identifier("gri", code_gri)

    framework = str(row.get("framework") or "").strip() or "unknown"
    label = str(
        row.get("title") or row.get("indicator") or row.get("indicator_name") or ""
    ).strip()
    if label:
        return build_indicator_identifier(framework, label)

    return raw_identifier


def normalize_indicator_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map a CSV row to a canonical Indicator payload plus normalization metadata."""
    result: dict[str, Any] = {}
    for csv_col, model_field in COLUMN_MAP.items():
        value = row.get(csv_col, "")
        if model_field == "source_row" and value:
            try:
                value = int(value)
            except (ValueError, TypeError):
                value = None
        result[model_field] = value if value != "" else None

    original_identifier = str(result.get("identifier") or "").strip()
    canonical_identifier = canonicalize_identifier_for_row(row)
    result["identifier"] = canonical_identifier or None
    result["id"] = canonical_identifier or None
    apply_indicator_metadata_overrides(result)

    return {
        "record": result,
        "row_number": int(row.get("__row_number__", 0) or 0),
        "original_identifier": original_identifier,
        "canonical_identifier": canonical_identifier,
        "normalized": bool(
            original_identifier
            and canonical_identifier
            and original_identifier != canonical_identifier
        ),
        "valid": validate_urn(canonical_identifier),
    }


def load_indicator_rows_from_text(
    csv_text: str, *, max_rows: int = DEFAULT_MAX_ROWS
) -> list[dict[str, Any]]:
    """Parse a register CSV string and return raw rows with source row numbers."""
    handle = io.StringIO(csv_text)
    reader = csv.DictReader(handle)
    headers = _normalized_headers(reader.fieldnames or [])
    _validate_headers(headers)

    rows: list[dict[str, Any]] = []
    for row_number, row in enumerate(reader, start=2):
        if len(rows) >= max_rows:
            raise IndicatorCsvContractError(
                f"Indicator CSV exceeds the maximum of {max_rows} data rows."
            )
        if None in row:
            raise IndicatorCsvContractError(
                f"Indicator CSV row {row_number} has more cells than headers."
            )
        cleaned = {
            _normalize_header(key): (value or "").strip()
            for key, value in row.items()
            if key is not None
        }
        cleaned["__row_number__"] = row_number
        rows.append(cleaned)

    if not rows:
        raise IndicatorCsvContractError("Indicator CSV is empty.")
    return rows


def load_indicator_rows_from_csv(
    csv_path: Path, *, skip_rows: int = 0, max_rows: int = DEFAULT_MAX_ROWS
) -> list[dict[str, Any]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = _normalized_headers(reader.fieldnames or [])
        _validate_headers(headers)
        rows: list[dict[str, Any]] = []
        for row_number, row in enumerate(reader, start=2):
            if row_number - 2 < skip_rows:
                continue
            if len(rows) >= max_rows:
                raise IndicatorCsvContractError(
                    f"Indicator CSV exceeds the maximum of {max_rows} data rows."
                )
            if None in row:
                raise IndicatorCsvContractError(
                    f"Indicator CSV row {row_number} has more cells than headers."
                )
            cleaned = {
                _normalize_header(key): (value or "").strip()
                for key, value in row.items()
                if key is not None
            }
            cleaned["__row_number__"] = row_number
            rows.append(cleaned)
    if not rows:
        raise IndicatorCsvContractError("Indicator CSV is empty.")
    return rows


def validate_indicator_import_text(
    csv_text: str,
    *,
    db: Optional[Session] = None,
    source_sha256: Optional[str] = None,
    source_size_bytes: Optional[int] = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_errors: int = DEFAULT_MAX_ERRORS,
) -> IndicatorImportPlan:
    rows = load_indicator_rows_from_text(csv_text, max_rows=max_rows)
    return validate_indicator_rows(
        rows,
        db=db,
        source_sha256=source_sha256 or sha256_text(csv_text),
        source_size_bytes=(
            source_size_bytes
            if source_size_bytes is not None
            else len(csv_text.encode("utf-8"))
        ),
        max_errors=max_errors,
    )


def validate_indicator_rows(
    rows: Iterable[dict[str, Any]],
    *,
    db: Optional[Session] = None,
    source_sha256: Optional[str] = None,
    source_size_bytes: Optional[int] = None,
    max_errors: int = DEFAULT_MAX_ERRORS,
) -> IndicatorImportPlan:
    analyzed_rows = [normalize_indicator_row(row) for row in rows]
    errors: list[IndicatorImportRowError] = []
    errors_truncated = False
    seen_identifiers: set[str] = set()
    valid_records: list[dict[str, Any]] = []

    for item in analyzed_rows:
        identifier = item["canonical_identifier"] or item["original_identifier"]
        row_number = item.get("row_number") or 0
        message = None
        if not item["valid"]:
            message = "Invalid indicator identifier after canonicalization"
        elif item["canonical_identifier"] in seen_identifiers:
            message = (
                f"Duplicate indicator identifier in CSV: {item['canonical_identifier']}"
            )

        if message:
            if len(errors) < max_errors:
                errors.append(
                    IndicatorImportRowError(
                        row_number=row_number, identifier=identifier, message=message
                    )
                )
            else:
                errors_truncated = True
            continue

        seen_identifiers.add(item["canonical_identifier"])
        valid_records.append(item["record"])

    normalized_samples = [
        {
            "row_number": item.get("row_number"),
            "original_identifier": item["original_identifier"],
            "canonical_identifier": item["canonical_identifier"],
        }
        for item in analyzed_rows
        if item["normalized"]
    ][:10]

    legacy_migrations = (
        collect_legacy_identifier_migrations(db, analyzed_rows)
        if db is not None
        else {}
    )
    created_rows, updated_rows, unchanged_rows = _classify_catalog_changes(
        db,
        valid_records,
        legacy_migrations=legacy_migrations,
    )

    rejected_rows = len(analyzed_rows) - len(valid_records)
    return IndicatorImportPlan(
        total_rows=len(analyzed_rows),
        accepted_rows=len(valid_records),
        rejected_rows=rejected_rows,
        created_rows=created_rows,
        updated_rows=updated_rows,
        unchanged_rows=unchanged_rows,
        normalized_rows=sum(1 for item in analyzed_rows if item["normalized"]),
        errors=errors,
        errors_truncated=errors_truncated,
        normalized_samples=normalized_samples,
        source_sha256=source_sha256,
        source_size_bytes=source_size_bytes,
        records=valid_records,
        legacy_migrations=legacy_migrations,
    )


def apply_indicator_import_transactional(
    *,
    csv_text: str,
    db: Session,
    source_ref: Optional[str],
    source_hash: Optional[str],
    created_by: Optional[str],
    max_rows: int = DEFAULT_MAX_ROWS,
) -> IndicatorImportPlan:
    """Validate and apply an indicator register import as one DB transaction."""
    source_size_bytes = len(csv_text.encode("utf-8"))
    plan = validate_indicator_import_text(
        csv_text,
        db=db,
        source_sha256=source_hash or sha256_text(csv_text),
        source_size_bytes=source_size_bytes,
        max_rows=max_rows,
    )
    if not plan.valid:
        db.rollback()
        raise IndicatorImportValidationError(plan)

    try:
        repo = IndicatorRepository(db)
        migrated, removed = migrate_legacy_identifiers(
            db, plan.legacy_migrations, commit=False
        )
        affected = repo.bulk_upsert(plan.records, commit=False)
        projection_result = SemanticConceptProjector(db).project(commit=False)

        current_total = repo.count()
        current_indicators = repo.get_all(limit=max(current_total, 1), offset=0)
        snapshot_payload = [serialize_indicator(item) for item in current_indicators]
        manifest = build_dataset_manifest(
            dataset="indicators",
            items=snapshot_payload,
            last_modified=max(
                (
                    getattr(item, "updated_at", None)
                    or getattr(item, "created_at", None)
                    for item in current_indicators
                ),
                default=None,
            ),
        )
        snapshot = persist_dataset_snapshot(
            repo=DatasetSnapshotRepository(db),
            dataset="indicators",
            manifest=manifest,
            items=snapshot_payload,
            source_ref=source_ref,
            source_hash=source_hash,
            created_by=created_by,
            commit=False,
        )
        db.commit()

        plan.accepted_rows = affected
        plan.migrated_legacy_ids = migrated
        plan.removed_legacy_duplicates = removed
        plan.committed = True
        plan.snapshot_id = snapshot.id
        plan.manifest_hash = manifest.manifest_hash
        plan.semantic_projection = projection_result.as_dict()
        return plan
    except Exception:
        db.rollback()
        raise


def migrate_legacy_identifiers(
    db: Session,
    legacy_to_canonical: dict[str, str],
    *,
    commit: bool = True,
) -> tuple[int, int]:
    """Rename legacy malformed indicator URNs in-place before upserting canonical rows."""
    migrated = 0
    removed_duplicates = 0

    for legacy_identifier, canonical_identifier in sorted(legacy_to_canonical.items()):
        if (
            not legacy_identifier
            or not canonical_identifier
            or legacy_identifier == canonical_identifier
        ):
            continue

        legacy = (
            db.query(Indicator)
            .filter(Indicator.identifier == legacy_identifier)
            .first()
        )
        if legacy is None:
            continue

        canonical = (
            db.query(Indicator)
            .filter(Indicator.identifier == canonical_identifier)
            .first()
        )
        if canonical and canonical.id != legacy.id:
            (
                db.query(Indicator)
                .filter(Indicator.identifier == legacy_identifier)
                .delete(synchronize_session=False)
            )
            removed_duplicates += 1
            continue

        (
            db.query(Indicator)
            .filter(Indicator.identifier == legacy_identifier)
            .update(
                {
                    Indicator.id: canonical_identifier,
                    Indicator.identifier: canonical_identifier,
                },
                synchronize_session=False,
            )
        )
        migrated += 1

    if migrated or removed_duplicates:
        if commit:
            db.commit()
        else:
            db.flush()

    return migrated, removed_duplicates


def collect_legacy_identifier_migrations(
    db: Optional[Session], analyzed_rows: list[dict[str, Any]]
) -> dict[str, str]:
    if db is None:
        return {}
    migrations = {
        item["original_identifier"]: item["canonical_identifier"]
        for item in analyzed_rows
        if item["normalized"]
    }

    for (identifier,) in db.query(Indicator.identifier).all():
        canonical_identifier = canonicalize_indicator_identifier(identifier)
        if identifier != canonical_identifier and validate_urn(canonical_identifier):
            migrations[identifier] = canonical_identifier

    return migrations


def _normalized_headers(fieldnames: Iterable[str]) -> tuple[str, ...]:
    return tuple(_normalize_header(name) for name in fieldnames if name is not None)


def _normalize_header(name: str) -> str:
    return (name or "").strip().lstrip("\ufeff")


def _validate_headers(headers: tuple[str, ...]) -> None:
    if not headers:
        raise IndicatorCsvContractError("Indicator CSV has no header row.")
    duplicates = sorted({header for header in headers if headers.count(header) > 1})
    if duplicates:
        raise IndicatorCsvContractError(
            "Indicator CSV contains duplicate columns: " + ", ".join(duplicates)
        )
    missing = [column for column in REQUIRED_REGISTER_COLUMNS if column not in headers]
    if missing:
        raise IndicatorCsvContractError(
            "Indicator CSV is missing required columns: " + ", ".join(missing)
        )


def _classify_catalog_changes(
    db: Optional[Session],
    records: list[dict[str, Any]],
    *,
    legacy_migrations: Optional[dict[str, str]] = None,
) -> tuple[int, int, int]:
    if db is None:
        return len(records), 0, 0

    identifiers = [
        record["identifier"] for record in records if record.get("identifier")
    ]
    existing_by_identifier = {
        indicator.identifier: indicator
        for chunk in _chunks(identifiers, 500)
        for indicator in db.query(Indicator)
        .filter(Indicator.identifier.in_(chunk))
        .all()
    }
    legacy_by_canonical = _existing_legacy_by_canonical(db, legacy_migrations or {})

    created = 0
    updated = 0
    unchanged = 0
    for record in records:
        existing = existing_by_identifier.get(record.get("identifier"))
        if existing is None:
            if record.get("identifier") in legacy_by_canonical:
                updated += 1
                continue
            created += 1
            continue
        if _record_changes_indicator(record, existing):
            updated += 1
        else:
            unchanged += 1
    return created, updated, unchanged


def _existing_legacy_by_canonical(
    db: Session, legacy_migrations: dict[str, str]
) -> dict[str, str]:
    if not legacy_migrations:
        return {}
    legacy_identifiers = list(legacy_migrations.keys())
    existing_legacy = {
        identifier
        for chunk in _chunks(legacy_identifiers, 500)
        for identifier, in db.query(Indicator.identifier)
        .filter(Indicator.identifier.in_(chunk))
        .all()
    }
    return {
        canonical: legacy
        for legacy, canonical in legacy_migrations.items()
        if legacy in existing_legacy
    }


def _record_changes_indicator(record: dict[str, Any], indicator: Indicator) -> bool:
    for key, value in record.items():
        if key == "id" or not hasattr(indicator, key):
            continue
        if getattr(indicator, key) != value:
            return True
    return False


def _chunks(items: list[str], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]
