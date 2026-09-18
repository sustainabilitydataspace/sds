"""Internal API router for canonical mapping assertion inspection."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.api.models import CanonicalMappingPackageJobResponse
from src.auth.models import User
from src.config.settings import settings
from src.database.session import get_db_optional
from src.policies.policy_enforcer import require_policy
from src.services.canonical_mapping_db_import import (
    import_canonical_mapping_package_to_db,
)
from src.services.canonical_mapping_inspection import inspect_canonical_mapping_package
from src.services.canonical_mapping_installed_standards import (
    StandardReleaseKey,
    active_standard_release_keys_from_db,
    validate_canonical_mapping_installed_standard_compatibility,
)
from src.services.canonical_mapping_package_job_store import (
    get_canonical_mapping_package_job_store,
)
from src.services.canonical_pairwise_materialization import (
    DEFAULT_APPROVAL_STATUSES,
    DEFAULT_MAPPING_PROFILE,
    materialize_pairwise_mappings,
)

router = APIRouter()

INTERNAL_ASSERTION_INSPECTION_CONTRACT = {
    "surface": "internal_shadow_mapping_assertions",
    "source": "canonical_mapping_package_files",
    "read_only": True,
    "db_write": False,
    "materialization_write": False,
    "public_mapping_surface": False,
    "operational_mapping_surface": False,
}


class MappingAssertionPackageInspectionResponse(BaseModel):
    """Read-only package inspection response for internal review surfaces."""

    api_contract: dict[str, Any]
    package_id: str
    package_dir: str
    mode: str
    source_version: str | None = None
    manifest_hash: str | None = None
    package_schema_version: str | None = None
    valid: bool
    operational_eligible: bool
    operational_blockers: list[str]
    validation: dict[str, Any]
    candidate_count: int
    non_operational_candidate_count: int
    relationship_type_counts: dict[str, int]
    non_operational_relationship_type_counts: dict[str, int]
    target_identity_status_counts: dict[str, int]
    candidates: list[dict[str, Any]]


class InstalledStandardReleaseRequest(BaseModel):
    """Installed standard release declared for package compatibility checks."""

    standard_id: str
    version: str


class CanonicalMappingPackageValidationRequest(BaseModel):
    """Request body for package validation against installed standards."""

    compatibility_mode: Literal["strict", "partial"] = "partial"
    installed_standard_releases: list[InstalledStandardReleaseRequest] | None = None


class CanonicalMappingPackageImportRequest(BaseModel):
    """Request body for an internal package import job."""

    validation_id: str | None = None
    compatibility_mode: Literal["strict", "partial"] = "strict"
    installed_standard_releases: list[InstalledStandardReleaseRequest] | None = None
    dry_run: bool = False


class CanonicalMappingMaterializationPreviewRequest(BaseModel):
    """Request body for a pairwise materialization preview."""

    installed_standard_releases: list[InstalledStandardReleaseRequest] | None = None
    mapping_profile: str = DEFAULT_MAPPING_PROFILE
    approval_statuses: list[str] = list(DEFAULT_APPROVAL_STATUSES)
    allow_operational_subset: bool = False


@router.get(
    "/{package_id}/assertion-inspection",
    response_model=MappingAssertionPackageInspectionResponse,
)
async def inspect_mapping_assertion_package(
    package_id: str,
    include_operational: bool = Query(
        default=False,
        description="Include operational relationship candidates as well as review-only rows.",
    ),
    limit: int | None = Query(
        default=None,
        ge=1,
        le=1000,
        description="Maximum candidates to return. Null means no explicit API cap.",
    ),
    user: User = Depends(require_policy("mappings", "write")),
) -> MappingAssertionPackageInspectionResponse:
    """Inspect a canonical mapping package without DB writes or materialization."""

    package_dir = _resolve_allowed_package_id(package_id)
    report = inspect_canonical_mapping_package(
        package_dir,
        include_operational=include_operational,
        limit=limit,
    )
    payload = report.as_dict()
    validation_payload = payload["validation"]
    manifest_payload = _load_manifest_payload(package_dir)
    return MappingAssertionPackageInspectionResponse(
        api_contract=dict(INTERNAL_ASSERTION_INSPECTION_CONTRACT),
        package_id=package_id,
        source_version=_optional_text(manifest_payload.get("source_version")),
        manifest_hash=validation_payload.get("manifest_hash"),
        package_schema_version=validation_payload.get("package_schema_version"),
        operational_blockers=list(validation_payload.get("operational_blockers", [])),
        non_operational_relationship_type_counts=dict(
            validation_payload.get("non_operational_relationship_type_counts", {})
        ),
        **payload,
    )


@router.post(
    "/{package_id}/validations",
    response_model=CanonicalMappingPackageJobResponse,
    status_code=200,
)
async def validate_mapping_package_against_installed_standards(
    package_id: str,
    request_body: CanonicalMappingPackageValidationRequest,
    db: Session | None = Depends(get_db_optional),
    job_store=Depends(get_canonical_mapping_package_job_store),
    user: User = Depends(require_policy("mappings", "write")),
) -> CanonicalMappingPackageJobResponse:
    """Validate a package and show which rows can be live in this SDS instance."""

    package_dir = _resolve_allowed_package_id(package_id)
    installed_releases = _installed_release_keys_from_request(
        request_body.installed_standard_releases,
        db=db,
    )
    compatibility = validate_canonical_mapping_installed_standard_compatibility(
        package_dir=package_dir,
        db=db,
        installed_standard_releases=installed_releases,
        compatibility_mode=request_body.compatibility_mode,
        require_installed_datapoints=db is not None,
    )
    result_body = {
        "validation": compatibility.validation.as_dict(),
        "compatibility": compatibility.as_dict(),
    }
    total_rows = sum(compatibility.status_counts.values())
    accepted_rows = compatibility.importable_assertion_group_count
    rejected_rows = total_rows - accepted_rows
    return job_store.create(
        job_id=str(uuid.uuid4()),
        job_type="validation",
        package_id=package_id,
        submitted_by=getattr(user, "username", "anonymous"),
        request_metadata={
            "compatibility_mode": request_body.compatibility_mode,
            "db_compared": db is not None,
            "installed_standard_releases": _installed_release_payload(
                installed_releases
            ),
        },
        status="completed",
        result_body=result_body,
        total_rows=total_rows,
        accepted_rows=accepted_rows,
        rejected_rows=rejected_rows,
        committed=False,
    )


@router.get(
    "/validations/{job_id}",
    response_model=CanonicalMappingPackageJobResponse,
)
async def get_mapping_package_validation_job(
    job_id: str,
    job_store=Depends(get_canonical_mapping_package_job_store),
    user: User = Depends(require_policy("mappings", "write")),
) -> CanonicalMappingPackageJobResponse:
    """Return a retained mapping package validation job."""

    job = job_store.get(job_id)
    if job is None or job.job_type != "validation":
        raise HTTPException(
            status_code=404,
            detail=f"Canonical mapping package validation not found: {job_id}",
        )
    return job


@router.post(
    "/{package_id}/import-jobs",
    response_model=CanonicalMappingPackageJobResponse,
    status_code=202,
)
async def submit_mapping_package_import_job(
    package_id: str,
    request_body: CanonicalMappingPackageImportRequest,
    db: Session | None = Depends(get_db_optional),
    job_store=Depends(get_canonical_mapping_package_job_store),
    user: User = Depends(require_policy("mappings", "write")),
) -> CanonicalMappingPackageJobResponse:
    """Import only the installed-compatible canonical mapping package slice."""

    if db is None:
        raise HTTPException(
            status_code=503,
            detail="Canonical mapping package imports require database-backed mode.",
        )
    package_dir = _resolve_allowed_package_id(package_id)
    installed_releases = _installed_release_keys_from_request(
        request_body.installed_standard_releases,
        db=db,
    )
    if request_body.validation_id:
        validation = job_store.get(request_body.validation_id)
        if (
            validation is None
            or validation.job_type != "validation"
            or validation.package_id != package_id
        ):
            raise HTTPException(
                status_code=404,
                detail=(
                    "Canonical mapping package validation not found for package "
                    f"{package_id}: {request_body.validation_id}"
                ),
            )
        _assert_validation_matches_import(
            validation,
            request_body=request_body,
            installed_releases=installed_releases,
        )

    allow_partial = request_body.compatibility_mode == "partial"

    job_store.acquire_import_submission_lock()
    if job_store.has_active_import():
        raise HTTPException(
            status_code=409,
            detail="Another canonical mapping package import is pending or running.",
        )

    job_id = str(uuid.uuid4())
    submitted_by = getattr(user, "username", "anonymous")
    job_store.create(
        job_id=job_id,
        job_type="import",
        package_id=package_id,
        submitted_by=submitted_by,
        request_metadata={
            "compatibility_mode": request_body.compatibility_mode,
            "dry_run": request_body.dry_run,
            "transaction": "all_or_nothing_installed_slice",
            "installed_standard_releases": _installed_release_payload(
                installed_releases
            ),
        },
        status="pending",
        validation_job_id=request_body.validation_id,
    )
    if job_store.mark_running(job_id) is None:
        raise HTTPException(
            status_code=500,
            detail="Canonical mapping package import job could not be marked running.",
        )

    try:
        report = import_canonical_mapping_package_to_db(
            package_dir=package_dir,
            db=db,
            dry_run=request_body.dry_run,
            created_by=getattr(user, "username", "canonical_mapping_package_api"),
            installed_standard_releases=installed_releases,
            allow_partial_installed_standards=allow_partial,
        )
    except Exception as exc:
        job_store.fail(
            job_id=job_id,
            error_message=str(exc),
            committed=False,
        )
        raise

    compatibility = report.compatibility or {}
    total_rows = sum(compatibility.get("status_counts", {}).values())
    accepted_rows = int(compatibility.get("importable_assertion_group_count") or 0)
    rejected_rows = total_rows - accepted_rows
    completed = job_store.complete(
        job_id=job_id,
        result_body=report.as_dict(),
        total_rows=total_rows,
        accepted_rows=accepted_rows,
        rejected_rows=rejected_rows,
        committed=report.committed,
        error_message="; ".join(report.blockers) if report.blocked else None,
    )
    if completed is None:
        raise HTTPException(
            status_code=500,
            detail="Canonical mapping package import job could not be completed.",
        )
    return completed


@router.get(
    "/import-jobs/{job_id}",
    response_model=CanonicalMappingPackageJobResponse,
)
async def get_mapping_package_import_job(
    job_id: str,
    job_store=Depends(get_canonical_mapping_package_job_store),
    user: User = Depends(require_policy("mappings", "write")),
) -> CanonicalMappingPackageJobResponse:
    """Return a retained mapping package import job."""

    job = job_store.get(job_id)
    if job is None or job.job_type != "import":
        raise HTTPException(
            status_code=404,
            detail=f"Canonical mapping package import job not found: {job_id}",
        )
    return job


@router.post(
    "/materialization-previews",
    response_model=dict[str, Any],
    status_code=200,
)
async def preview_mapping_package_materialization(
    request_body: CanonicalMappingMaterializationPreviewRequest,
    db: Session | None = Depends(get_db_optional),
    user: User = Depends(require_policy("mappings", "write")),
) -> dict[str, Any]:
    """Preview pairwise materialization under the installed-standard filter."""

    if db is None:
        raise HTTPException(
            status_code=503,
            detail="Canonical mapping materialization previews require database mode.",
        )
    installed_releases = _installed_release_keys_from_request(
        request_body.installed_standard_releases,
        db=db,
    )
    report = materialize_pairwise_mappings(
        db=db,
        dry_run=True,
        mapping_profile=request_body.mapping_profile,
        approval_statuses=tuple(request_body.approval_statuses),
        allow_operational_subset=request_body.allow_operational_subset,
        installed_standard_releases=installed_releases,
    )
    return report.as_dict()


def _resolve_allowed_package_id(package_id: str) -> Path:
    if not package_id or package_id in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid package_id.")
    if any(separator in package_id for separator in ("/", "\\")):
        raise HTTPException(status_code=400, detail="Invalid package_id.")

    allowed_roots = _allowed_inspection_roots()
    if not allowed_roots:
        raise HTTPException(
            status_code=403,
            detail=(
                "Internal mapping assertion package inspection is not configured. "
                "Set CANONICAL_MAPPING_INSPECTION_ROOTS to one or more package roots."
            ),
        )

    for root in allowed_roots:
        candidate = (root / package_id).resolve()
        if (
            _is_relative_to(candidate, root)
            and candidate.exists()
            and candidate.is_dir()
        ):
            return candidate
    raise HTTPException(
        status_code=404,
        detail="Canonical mapping package id was not found under configured roots.",
    )


def _allowed_inspection_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    for raw_root in settings.canonical_mapping_inspection_roots:
        root = Path(raw_root).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        try:
            roots.append(root.resolve())
        except OSError:
            continue
    return tuple(roots)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _load_manifest_payload(package_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            (package_dir / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _installed_release_keys_from_request(
    releases: list[InstalledStandardReleaseRequest] | None,
    *,
    db: Session | None,
) -> set[StandardReleaseKey]:
    if releases is not None:
        return {
            (item.standard_id.strip(), item.version.strip())
            for item in releases
            if item.standard_id.strip() and item.version.strip()
        }
    if db is not None:
        return active_standard_release_keys_from_db(db)
    return set()


def _installed_release_payload(
    releases: set[StandardReleaseKey],
) -> list[dict[str, str]]:
    return [
        {"standard_id": standard_id, "version": version}
        for standard_id, version in sorted(releases)
    ]


def _normalize_release_payload(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    keys: set[StandardReleaseKey] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        standard_id = str(item.get("standard_id") or "").strip()
        version = str(item.get("version") or "").strip()
        if standard_id and version:
            keys.add((standard_id, version))
    return _installed_release_payload(keys)


def _assert_validation_matches_import(
    validation: CanonicalMappingPackageJobResponse,
    *,
    request_body: CanonicalMappingPackageImportRequest,
    installed_releases: set[StandardReleaseKey],
) -> None:
    if validation.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="Canonical mapping package validation is not completed.",
        )

    metadata = validation.request_metadata or {}
    validation_mode = str(metadata.get("compatibility_mode") or "").strip()
    if validation_mode != request_body.compatibility_mode:
        raise HTTPException(
            status_code=409,
            detail=(
                "Canonical mapping package validation compatibility_mode does not "
                "match the import request."
            ),
        )

    expected_releases = _installed_release_payload(installed_releases)
    validation_releases = _normalize_release_payload(
        metadata.get("installed_standard_releases")
    )
    if not validation_releases:
        compatibility = (validation.result_body or {}).get("compatibility") or {}
        validation_releases = _normalize_release_payload(
            compatibility.get("installed_standard_releases")
        )
    if validation_releases != expected_releases:
        raise HTTPException(
            status_code=409,
            detail=(
                "Canonical mapping package validation installed_standard_releases "
                "do not match the import request."
            ),
        )
