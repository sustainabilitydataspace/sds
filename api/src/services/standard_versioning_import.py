"""DB-backed import for Atomizer standard-versioning package metadata."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from src.database.models import StandardDatapoint, StandardRelease
from src.services.atomizer_package_import import validate_standard_versioning_manifest


class StandardVersioningImportError(ValueError):
    """Raised when standard-versioning metadata cannot be imported."""


@dataclass
class StandardVersioningImportReport:
    package_dir: str
    dry_run: bool
    valid: bool
    committed: bool
    standard_id: str | None = None
    release_id: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_dir": self.package_dir,
            "dry_run": self.dry_run,
            "valid": self.valid,
            "committed": self.committed,
            "standard_id": self.standard_id,
            "release_id": self.release_id,
            "counts": self.counts,
            "errors": self.errors,
        }


def import_standard_versioning_package(
    *,
    package_dir: Path,
    register_csv: Path,
    db: Session,
    dry_run: bool = False,
    created_by: str = "standard_versioning_import",
) -> StandardVersioningImportReport:
    """Validate and import Atomizer standard-versioning metadata into SDS.

    The import is additive and idempotent. It upserts the external
    ``StandardRelease`` row and its release-specific ``StandardDatapoint`` rows.
    It does not activate tenant reporting releases or migrate tenant values.
    """

    package_dir = package_dir.resolve()
    validation = validate_standard_versioning_manifest(package_dir, require=True)
    if not validation.valid:
        return StandardVersioningImportReport(
            package_dir=str(package_dir),
            dry_run=dry_run,
            valid=False,
            committed=False,
            errors=list(validation.errors),
        )

    payload = _load_standard_versioning_payload(package_dir)
    release_manifest = _object(payload["release_manifest"])
    lineage_rows = _lineage_rows(payload.get("datapoint_lineage_map"))
    register_rows = _load_register_rows(register_csv)
    _validate_lineage_rows(
        lineage_rows,
        release_manifest=release_manifest,
        register_rows=register_rows,
    )
    report = StandardVersioningImportReport(
        package_dir=str(package_dir),
        dry_run=dry_run,
        valid=True,
        committed=False,
        standard_id=str(release_manifest["standard_id"]),
        release_id=str(release_manifest["release_id"]),
    )

    try:
        release = _upsert_standard_release(
            db,
            release_manifest=release_manifest,
            payload=payload,
            report=report,
            created_by=created_by,
        )
        db.flush()

        for row in lineage_rows:
            _upsert_standard_datapoint(
                db,
                release=release,
                lineage=row,
                register_row=register_rows.get(row["sds_identifier"], {}),
                payload=payload,
                report=report,
                created_by=created_by,
            )

        db.flush()
        if dry_run:
            db.rollback()
        else:
            db.commit()
            report.committed = True
        return report
    except Exception:
        db.rollback()
        raise


def validate_standard_versioning_package(
    *,
    package_dir: Path,
    register_csv: Path,
) -> StandardVersioningImportReport:
    """Validate standard-versioning metadata without opening a database session."""

    package_dir = package_dir.resolve()
    validation = validate_standard_versioning_manifest(package_dir, require=True)
    if not validation.valid:
        return StandardVersioningImportReport(
            package_dir=str(package_dir),
            dry_run=True,
            valid=False,
            committed=False,
            errors=list(validation.errors),
        )

    payload = _load_standard_versioning_payload(package_dir)
    release_manifest = _object(payload["release_manifest"])
    lineage_rows = _lineage_rows(payload.get("datapoint_lineage_map"))
    register_rows = _load_register_rows(register_csv)
    _validate_lineage_rows(
        lineage_rows,
        release_manifest=release_manifest,
        register_rows=register_rows,
    )
    return StandardVersioningImportReport(
        package_dir=str(package_dir),
        dry_run=True,
        valid=True,
        committed=False,
        standard_id=str(release_manifest["standard_id"]),
        release_id=str(release_manifest["release_id"]),
        counts={
            "standard_releases_validated": 1,
            "standard_datapoints_validated": len(lineage_rows),
        },
    )


def _load_standard_versioning_payload(package_dir: Path) -> dict[str, Any]:
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise StandardVersioningImportError("manifest.json must be a JSON object")
    payload = manifest.get("standard_versioning")
    if not isinstance(payload, Mapping):
        raise StandardVersioningImportError(
            "manifest.json is missing standard_versioning"
        )
    return dict(payload)


def _load_register_rows(register_csv: Path) -> dict[str, dict[str, str]]:
    with register_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "identifier" not in reader.fieldnames:
            return {}
        return {
            str(row.get("identifier") or "").strip(): {
                str(key): str(value or "").strip() for key, value in row.items()
            }
            for row in reader
            if str(row.get("identifier") or "").strip()
        }


def _upsert_standard_release(
    db: Session,
    *,
    release_manifest: Mapping[str, Any],
    payload: Mapping[str, Any],
    report: StandardVersioningImportReport,
    created_by: str,
) -> StandardRelease:
    release = (
        db.query(StandardRelease)
        .filter(
            StandardRelease.standard_id == str(release_manifest["standard_id"]),
            StandardRelease.version == str(release_manifest["release_id"]),
        )
        .first()
    )
    values = {
        "standard_id": str(release_manifest["standard_id"]),
        "name": str(release_manifest.get("official_name") or ""),
        "version": str(release_manifest["release_id"]),
        "release_date": _parse_date(release_manifest.get("publication_date")),
        "source_url": _empty_to_none(release_manifest.get("source_url")),
        "lifecycle_status": str(release_manifest.get("release_state") or "active"),
        "provenance": {
            "source_file": "manifest.json",
            "standard_versioning": {
                "schema_version": payload.get("schema_version"),
                "source_hash": release_manifest.get("source_hash"),
                "source_hash_set": release_manifest.get("source_hash_set"),
                "publisher": release_manifest.get("publisher"),
                "effective_date": release_manifest.get("effective_date"),
                "supersedes_release_id": release_manifest.get("supersedes_release_id"),
                "trust_envelope_id": release_manifest.get("trust_envelope_id"),
                "delta_manifest": payload.get("delta_manifest"),
                "affected_scope_plan": payload.get("affected_scope_plan"),
                "tenant_activation_policy": payload.get("tenant_activation_policy"),
            },
        },
    }
    if release is None:
        release = StandardRelease(**values, created_by=created_by)
        db.add(release)
        _count(report, "standard_releases", "created")
        return release

    if _apply_values(release, values):
        _count(report, "standard_releases", "updated")
    else:
        _count(report, "standard_releases", "unchanged")
    return release


def _upsert_standard_datapoint(
    db: Session,
    *,
    release: StandardRelease,
    lineage: Mapping[str, str],
    register_row: Mapping[str, str],
    payload: Mapping[str, Any],
    report: StandardVersioningImportReport,
    created_by: str,
) -> StandardDatapoint:
    datapoint = (
        db.query(StandardDatapoint)
        .filter(
            StandardDatapoint.standard_release_id == release.id,
            StandardDatapoint.code == lineage["datapoint_id"],
        )
        .first()
    )
    values = {
        "standard_release": release,
        "code": lineage["datapoint_id"],
        "label": register_row.get("title") or lineage["datapoint_id"],
        "disclosure_text": _empty_to_none(register_row.get("description")),
        "datapoint_type": _empty_to_none(register_row.get("valueType")),
        "unit": _empty_to_none(register_row.get("unitName")),
        "lifecycle_status": "active",
        "metadata_json": {
            "sds_identifier": lineage["sds_identifier"],
            "standard_id": lineage["standard_id"],
            "release_id": lineage["release_id"],
            "concept_id": lineage.get("concept_id"),
            "relationship": lineage.get("relationship"),
            "source_file": "manifest.json",
            "standard_versioning_schema": payload.get("schema_version"),
        },
    }
    if datapoint is None:
        datapoint = StandardDatapoint(**values, created_by=created_by)
        db.add(datapoint)
        _count(report, "standard_datapoints", "created")
        return datapoint

    if _apply_values(datapoint, values):
        _count(report, "standard_datapoints", "updated")
    else:
        _count(report, "standard_datapoints", "unchanged")
    return datapoint


def _lineage_rows(raw_rows: Any) -> list[dict[str, str]]:
    if not isinstance(raw_rows, list):
        raise StandardVersioningImportError("datapoint_lineage_map must be a list")
    rows: list[dict[str, str]] = []
    for item in raw_rows:
        if not isinstance(item, Mapping):
            raise StandardVersioningImportError(
                "datapoint_lineage_map entries must be objects"
            )
        rows.append(
            {
                "sds_identifier": str(item.get("sds_identifier") or "").strip(),
                "standard_id": str(item.get("standard_id") or "").strip(),
                "release_id": str(item.get("release_id") or "").strip(),
                "datapoint_id": str(item.get("datapoint_id") or "").strip(),
                "concept_id": str(item.get("concept_id") or "").strip(),
                "relationship": str(item.get("relationship") or "").strip(),
            }
        )
    return rows


def _validate_lineage_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    release_manifest: Mapping[str, Any],
    register_rows: Mapping[str, Mapping[str, str]],
) -> None:
    expected_standard_id = str(release_manifest["standard_id"])
    expected_release_id = str(release_manifest["release_id"])
    register_identifiers = set(register_rows)
    seen_keys: dict[tuple[str, str, str], tuple[int, str]] = {}
    errors: list[str] = []

    for index, row in enumerate(rows, start=1):
        sds_identifier = row["sds_identifier"]
        standard_id = row["standard_id"]
        release_id = row["release_id"]
        datapoint_id = row["datapoint_id"]

        if sds_identifier not in register_identifiers:
            errors.append(
                f"datapoint_lineage_map[{index}].sds_identifier "
                f"{sds_identifier!r} is not present in register CSV"
            )
        if standard_id != expected_standard_id:
            errors.append(
                f"datapoint_lineage_map[{index}].standard_id {standard_id!r} "
                f"does not match release_manifest.standard_id "
                f"{expected_standard_id!r}"
            )
        if release_id != expected_release_id:
            errors.append(
                f"datapoint_lineage_map[{index}].release_id {release_id!r} "
                f"does not match release_manifest.release_id "
                f"{expected_release_id!r}"
            )

        key = (standard_id, release_id, datapoint_id)
        previous = seen_keys.get(key)
        if previous is not None:
            previous_index, previous_identifier = previous
            errors.append(
                "duplicate datapoint lineage key "
                f"{key!r} at rows {previous_index} and {index} "
                f"for sds_identifiers {previous_identifier!r} and "
                f"{sds_identifier!r}; current StandardDatapoint import keys "
                "by release plus datapoint_id, so one legal datapoint mapped "
                "to multiple SDS rows needs an explicit schema extension"
            )
        else:
            seen_keys[key] = (index, sds_identifier)

    if errors:
        raise StandardVersioningImportError("; ".join(errors))


def _object(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StandardVersioningImportError("expected JSON object")
    return value


def _parse_date(value: Any) -> date | None:
    text = _empty_to_none(value)
    return date.fromisoformat(text) if text else None


def _empty_to_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _apply_values(obj: Any, values: Mapping[str, Any]) -> bool:
    changed = False
    for key, value in values.items():
        if getattr(obj, key) != value:
            setattr(obj, key, value)
            changed = True
    return changed


def _count(report: StandardVersioningImportReport, entity: str, action: str) -> None:
    key = f"{entity}_{action}"
    report.counts[key] = report.counts.get(key, 0) + 1
