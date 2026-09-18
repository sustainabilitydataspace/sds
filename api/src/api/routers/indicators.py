"""API router for E1 indicators (E5 integration with E6 policy enforcement)."""

import csv
import io
import json
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, model_serializer
from sqlalchemy.orm import Session

import structlog
from src.api.models import (
    ChangeFeedDataset,
    ChangeFeedEventResponse,
    ChangeFeedResponse,
    DatasetDiffResponse,
    DatasetExportFormat,
    DatasetManifestResponse,
    DatasetSnapshotHistoryResponse,
    DatasetSnapshotResponse,
    IndicatorImportErrorsResponse,
    IndicatorImportJobResponse,
)
from src.auth.models import User
from src.config.settings import settings
from src.database.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)
from src.database.repositories.indicator_repository import IndicatorRepository
from src.database.session import get_db, get_db_optional
from src.policies.policy_enforcer import require_policy
from src.services.change_feed import (
    decode_change_feed_cursor,
    encode_change_feed_cursor,
)
from src.services.dataset_history import build_dataset_item_index, diff_dataset_indexes
from src.services.dataset_manifest import build_dataset_manifest
from src.services.dataset_serialization import serialize_indicator
from src.services.indicator_import import (
    IndicatorCsvContractError,
    sha256_text,
    validate_indicator_import_text,
)
from src.services.indicator_import_job_runner import run_indicator_import_job
from src.services.indicator_import_job_store import get_indicator_import_job_store
from src.services.indicator_store import IndicatorStore
from src.services.localization_service import LocalizationRepository
from src.services.parquet_export import parquet_bytes_from_rows

router = APIRouter()
logger = structlog.get_logger(__name__)


def _require_catalog_db(db: Optional[Session], operation: str) -> Optional[Session]:
    if db is None:
        if settings.require_database:
            raise HTTPException(
                status_code=503,
                detail=f"Canonical indicator data is unavailable for {operation}: no database session is available.",
            )
        return None
    return db


def _handle_catalog_error(operation: str, error: Exception) -> None:
    if settings.require_database:
        logger.error(
            "Indicator catalog unavailable", operation=operation, error=str(error)
        )
        raise HTTPException(
            status_code=503,
            detail=f"Canonical indicator data is unavailable for {operation}.",
        ) from error
    raise error


# Response models
class IndicatorResponse(BaseModel):
    """Response model for a single indicator."""

    id: str
    identifier: str
    title: str
    indicator_name: Optional[str] = None
    description: Optional[str] = None
    dimension: str
    unit_name: Optional[str] = None
    unit_type: Optional[str] = None
    periodicity: Optional[str] = None
    period_type: Optional[str] = None
    source_ref: Optional[str] = None
    code_esrs: Optional[str] = None
    code_gri: Optional[str] = None
    code_gri_expanded: Optional[str] = None
    evidence_path: Optional[str] = None
    source_row: Optional[int] = None
    owner: Optional[str] = None
    access_rights: Optional[str] = None
    validation_method: Optional[str] = None
    double_materiality: Optional[str] = None
    value_type: Optional[str] = None
    # Additive localization (V1): populated only when ?lang is requested; canonical
    # title/indicator_name/description are never replaced.
    display_title: Optional[str] = None
    display_indicator_name: Optional[str] = None
    display_description: Optional[str] = None
    localization: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)

    @model_serializer(mode="wrap")
    def _drop_unset_localization(self, handler):
        """Preserve the flat V1 response contract: the response-only localization fields appear
        only when ?lang populated them. When unset (no language requested) they are omitted, so a
        default response is byte-identical to the pre-localization contract (codex LOC-3 M1).
        """
        data = handler(self)
        for name in _LOCALIZATION_RESPONSE_FIELDS:
            if data.get(name) is None:
                data.pop(name, None)
        return data


class IndicatorListResponse(BaseModel):
    """Response model for paginated indicator list."""

    items: List[IndicatorResponse]
    total: int
    limit: int
    offset: int


# Response-only localization fields. They are populated solely when ?lang is requested and MUST
# NOT enter the flat export contract (CSV columns / JSON keys) nor appear on default responses.
_LOCALIZATION_RESPONSE_FIELDS = (
    "display_title",
    "display_indicator_name",
    "display_description",
    "localization",
)
# Canonical, pre-localization export field set — exports stay pinned to this regardless of any
# response-only fields added to IndicatorResponse (codex LOC-3 M2).
_INDICATOR_EXPORT_FIELDS = [
    name
    for name in IndicatorResponse.model_fields
    if name not in _LOCALIZATION_RESPONSE_FIELDS
]


def _localize_indicators(
    db: Optional[Session], items: List[IndicatorResponse], lang: Optional[str]
) -> None:
    """Add display_title/display_indicator_name/display_description + localization for ``lang``.

    Additive (V1): canonical title/indicator_name/description are never replaced. No-op when no
    language is requested or no DB session is available — the default path is unchanged. The
    indicator's public ``identifier`` is the localization subject_uri (never the DB id).
    """
    if not lang or db is None or not items:
        return
    repo = LocalizationRepository(db)
    for item in items:
        source_fields: Dict[str, str] = {}
        if item.title:
            source_fields["title"] = item.title
        if item.indicator_name:
            source_fields["indicator_name"] = item.indicator_name
        if item.description:
            source_fields["description"] = item.description
        if not item.identifier or not source_fields:
            continue
        result = repo.localize(
            subject_kind="indicator",
            subject_uri=item.identifier,
            source_fields=source_fields,
            requested_language=lang,
        )
        item.display_title = result.display.get("title", item.title)
        item.display_indicator_name = result.display.get(
            "indicator_name", item.indicator_name
        )
        item.display_description = result.display.get("description", item.description)
        item.localization = result.as_metadata()


def _indicator_last_modified(indicators: Iterable[object]) -> Optional[datetime]:
    timestamps = []
    for indicator in indicators:
        updated_at = getattr(indicator, "updated_at", None) or getattr(
            indicator, "created_at", None
        )
        if updated_at is not None:
            timestamps.append(updated_at)
    return max(timestamps) if timestamps else None


def _current_indicator_payload(
    store: IndicatorStore,
) -> tuple[list[object], list[dict]]:
    total = store.count()
    indicators = store.get_all(limit=max(total, 1), offset=0)
    payload = [serialize_indicator(item) for item in indicators]
    return indicators, payload


def _decode_indicator_changes_cursor(cursor: Optional[str]):
    if not cursor:
        return None
    try:
        decoded = decode_change_feed_cursor(cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if decoded.dataset != ChangeFeedDataset.INDICATORS.value:
        raise HTTPException(
            status_code=400, detail="Changed-feed cursor does not belong to indicators."
        )
    return decoded


async def _read_limited_csv_upload(file: UploadFile) -> tuple[str, int, str]:
    """Read an uploaded CSV with a hard byte guard before decoding."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > settings.indicator_import_max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    "Indicator CSV exceeds the maximum upload size of "
                    f"{settings.indicator_import_max_bytes} bytes."
                ),
            )
        chunks.append(chunk)

    raw = b"".join(chunks)
    try:
        csv_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=400, detail="Indicator CSV must be valid UTF-8 text"
        ) from error

    return csv_text, total, sha256_text(csv_text)


def _extract_job_errors(job: IndicatorImportJobResponse) -> list[dict]:
    result = job.result_body or {}
    errors = result.get("errors") or []
    return errors if isinstance(errors, list) else []


@router.get("", response_model=IndicatorListResponse)
async def list_indicators(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    changed_since: Optional[datetime] = Query(
        default=None,
        description="Return only indicators created or updated on/after this timestamp",
    ),
    # Plain None default (not Query(...)) so the handler stays directly-callable in tests;
    # FastAPI still exposes it as an optional query param. (VARCH-8e pattern.)
    lang: Optional[str] = None,
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("indicators", "read")),
):
    """List all indicators with pagination and optional ?lang localization.

    Requires: read_indicators permission (E6 policy enforced).
    """
    # NOTE: This endpoint uses sync DB operations in an async handler. Full async migration is deferred.
    db = _require_catalog_db(db, "list_indicators")
    if db is None:
        return IndicatorListResponse(items=[], total=0, limit=limit, offset=offset)
    repo = IndicatorRepository(db)
    try:
        indicators = repo.get_all(
            limit=limit, offset=offset, changed_since=changed_since
        )
        total = repo.count(changed_since=changed_since)
    except Exception as exc:
        _handle_catalog_error("list_indicators", exc)

    items = [IndicatorResponse.model_validate(ind) for ind in indicators]
    _localize_indicators(db, items, lang)
    return IndicatorListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/search", response_model=IndicatorListResponse)
async def search_indicators(
    dimension: Optional[str] = Query(
        default=None, description="ESG dimension (E, S, G, Transversal)"
    ),
    esrs: Optional[str] = Query(default=None, description="ESRS code prefix"),
    gri: Optional[str] = Query(default=None, description="GRI code prefix"),
    q: Optional[str] = Query(
        default=None,
        description=(
            "Text or code search in identifier, title, description, ESRS/GRI "
            "codes, source references, and evidence paths"
        ),
    ),
    changed_since: Optional[datetime] = Query(
        default=None,
        description="Return only indicators created or updated on/after this timestamp",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    # Plain None default (not Query(...)) so the handler stays directly-callable in tests;
    # FastAPI still exposes it as an optional query param. (VARCH-8e pattern.)
    lang: Optional[str] = None,
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("indicators", "read")),
):
    """Search indicators by dimension, ESRS/GRI codes, or text (with ?lang localization).

    Requires: read_indicators permission (E6 policy enforced).
    """
    # NOTE: This endpoint uses sync DB operations in an async handler. Full async migration is deferred.
    db = _require_catalog_db(db, "search_indicators")
    if db is None:
        return IndicatorListResponse(items=[], total=0, limit=limit, offset=0)
    repo = IndicatorRepository(db)
    try:
        indicators = repo.search(
            dimension=dimension,
            esrs=esrs,
            gri=gri,
            query=q,
            limit=limit,
            changed_since=changed_since,
        )
    except Exception as exc:
        _handle_catalog_error("search_indicators", exc)

    items = [IndicatorResponse.model_validate(ind) for ind in indicators]
    _localize_indicators(db, items, lang)
    return IndicatorListResponse(
        items=items,
        total=len(indicators),
        limit=limit,
        offset=0,
    )


@router.get("/manifest", response_model=DatasetManifestResponse)
async def indicator_manifest(
    dimension: Optional[str] = Query(
        default=None, description="ESG dimension (E, S, G, Transversal)"
    ),
    esrs: Optional[str] = Query(default=None, description="ESRS code prefix"),
    gri: Optional[str] = Query(default=None, description="GRI code prefix"),
    q: Optional[str] = Query(
        default=None, description="Text search in title/description"
    ),
    changed_since: Optional[datetime] = Query(
        default=None,
        description="Slice only indicators created or updated on/after this timestamp",
    ),
    limit: int = Query(default=10000, ge=1, le=50000),
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("indicators", "read")),
):
    """Return the deterministic version/audit manifest for an indicator export slice."""
    store = IndicatorStore(db=db)
    try:
        if any([dimension, esrs, gri, q]):
            indicators = store.search(
                dimension=dimension,
                esrs=esrs,
                gri=gri,
                query=q,
                limit=limit,
                changed_since=changed_since,
            )
        else:
            indicators = store.get_all(
                limit=limit, offset=0, changed_since=changed_since
            )
    except Exception as exc:
        _handle_catalog_error("indicator_manifest", exc)

    payload = [serialize_indicator(ind) for ind in indicators]
    manifest = build_dataset_manifest(
        dataset="indicators",
        items=payload,
        last_modified=_indicator_last_modified(indicators),
    )
    return DatasetManifestResponse(**manifest.__dict__)


@router.get("/export")
async def export_indicators(
    request: Request,
    format: DatasetExportFormat = Query(
        DatasetExportFormat.CSV, description="Export format"
    ),
    dimension: Optional[str] = Query(
        default=None, description="ESG dimension (E, S, G, Transversal)"
    ),
    esrs: Optional[str] = Query(default=None, description="ESRS code prefix"),
    gri: Optional[str] = Query(default=None, description="GRI code prefix"),
    q: Optional[str] = Query(
        default=None, description="Text search in title/description"
    ),
    changed_since: Optional[datetime] = Query(
        default=None,
        description="Export only indicators created or updated on/after this timestamp",
    ),
    limit: int = Query(default=10000, ge=1, le=50000),
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("indicators", "read")),
):
    """Export indicators as a flat dataset in CSV or JSON."""
    store = IndicatorStore(db=db)
    try:
        if any([dimension, esrs, gri, q]):
            indicators = store.search(
                dimension=dimension,
                esrs=esrs,
                gri=gri,
                query=q,
                limit=limit,
                changed_since=changed_since,
            )
        else:
            indicators = store.get_all(
                limit=limit, offset=0, changed_since=changed_since
            )
    except Exception as exc:
        _handle_catalog_error("export_indicators", exc)

    items = [IndicatorResponse.model_validate(ind) for ind in indicators]
    items_payload = [serialize_indicator(ind) for ind in indicators]
    manifest = build_dataset_manifest(
        dataset="indicators",
        items=items_payload,
        last_modified=_indicator_last_modified(indicators),
    )
    if manifest.is_not_modified(
        if_none_match=request.headers.get("if-none-match"),
        if_modified_since=request.headers.get("if-modified-since"),
    ):
        return Response(
            status_code=304, headers=manifest.response_headers(include_filename=False)
        )

    filename = f"indicators_export.{format.value}"
    headers = manifest.response_headers(filename=filename)
    if format == DatasetExportFormat.JSON:
        return StreamingResponse(
            _stream_indicator_json(items),
            media_type="application/json; charset=utf-8",
            headers=headers,
        )
    if format == DatasetExportFormat.PARQUET:
        return Response(
            content=parquet_bytes_from_rows(items_payload),
            media_type="application/vnd.apache.parquet",
            headers=headers,
        )

    return StreamingResponse(
        _stream_indicator_csv(items),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


@router.get("/history", response_model=DatasetSnapshotHistoryResponse)
async def indicator_history(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_policy("indicators", "read")),
):
    """Return persistent catalog snapshot history for indicators."""
    repo = DatasetSnapshotRepository(db)
    snapshots = repo.list_recent("indicators", limit=limit)
    total = repo.count("indicators")
    return DatasetSnapshotHistoryResponse(
        dataset="indicators",
        total=total,
        limit=limit,
        items=[
            DatasetSnapshotResponse.model_validate(snapshot, from_attributes=True)
            for snapshot in snapshots
        ],
    )


@router.get("/changes", response_model=ChangeFeedResponse)
async def indicator_changes(
    limit: int = Query(default=100, ge=1, le=500),
    cursor: Optional[str] = Query(
        default=None,
        description="Opaque cursor returned by the previous indicators change page",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(require_policy("indicators", "read")),
):
    """Return persisted indicator snapshot transitions in ascending change order."""
    repo = DatasetSnapshotRepository(db)
    decoded_cursor = _decode_indicator_changes_cursor(cursor)
    snapshots = repo.list_feed(
        datasets=[ChangeFeedDataset.INDICATORS.value],
        limit=limit + 1,
        cursor_occurred_at=decoded_cursor.occurred_at if decoded_cursor else None,
        cursor_dataset=decoded_cursor.dataset if decoded_cursor else None,
        cursor_event_id=decoded_cursor.event_id if decoded_cursor else None,
    )
    has_more = len(snapshots) > limit
    selected = snapshots[:limit]
    items: list[ChangeFeedEventResponse] = []
    for snapshot in selected:
        previous = repo.get_previous(ChangeFeedDataset.INDICATORS.value, snapshot.id)
        diff = diff_dataset_indexes(
            dataset="indicators",
            from_snapshot_id=previous.id if previous else None,
            to_snapshot_id=snapshot.id,
            from_manifest_hash=previous.manifest_hash if previous else "",
            to_manifest_hash=snapshot.manifest_hash,
            from_index=previous.item_index if previous else {},
            to_index=snapshot.item_index,
        )
        items.append(
            ChangeFeedEventResponse(
                cursor=encode_change_feed_cursor(
                    occurred_at=snapshot.created_at,
                    dataset=ChangeFeedDataset.INDICATORS.value,
                    event_id=str(snapshot.id),
                ),
                dataset=ChangeFeedDataset.INDICATORS,
                event_type="snapshot_persisted",
                occurred_at=snapshot.created_at,
                event_id=str(snapshot.id),
                manifest_hash=snapshot.manifest_hash,
                contract_version=snapshot.contract_version,
                record_count=snapshot.record_count,
                source_ref=snapshot.source_ref,
                source_hash=snapshot.source_hash,
                snapshot_id=snapshot.id,
                previous_snapshot_id=previous.id if previous else None,
                previous_manifest_hash=previous.manifest_hash if previous else None,
                diff={
                    "added_count": diff.added_count,
                    "removed_count": diff.removed_count,
                    "changed_count": diff.changed_count,
                    "unchanged_count": diff.unchanged_count,
                    "added_keys": diff.added_keys,
                    "removed_keys": diff.removed_keys,
                    "changed_keys": diff.changed_keys,
                    "keys_truncated": diff.keys_truncated,
                },
            )
        )

    return ChangeFeedResponse(
        datasets=[ChangeFeedDataset.INDICATORS],
        limit=limit,
        items=items,
        next_cursor=items[-1].cursor if has_more and items else None,
        has_more=has_more,
    )


@router.get("/diff", response_model=DatasetDiffResponse)
async def indicator_diff(
    from_snapshot_id: int = Query(
        ..., ge=1, description="Baseline snapshot identifier"
    ),
    to_snapshot_id: Optional[int] = Query(
        default=None, ge=1, description="Optional target snapshot identifier"
    ),
    db: Session = Depends(get_db),
    user: User = Depends(require_policy("indicators", "read")),
):
    """Diff two indicator snapshots, or a stored snapshot against the current live catalog."""
    snapshot_repo = DatasetSnapshotRepository(db)
    baseline = snapshot_repo.get_by_id(from_snapshot_id)
    if baseline is None or baseline.dataset != "indicators":
        raise HTTPException(
            status_code=404, detail=f"Indicator snapshot not found: {from_snapshot_id}"
        )

    if to_snapshot_id is not None:
        target = snapshot_repo.get_by_id(to_snapshot_id)
        if target is None or target.dataset != "indicators":
            raise HTTPException(
                status_code=404,
                detail=f"Indicator snapshot not found: {to_snapshot_id}",
            )
        diff = diff_dataset_indexes(
            dataset="indicators",
            from_snapshot_id=baseline.id,
            to_snapshot_id=target.id,
            from_manifest_hash=baseline.manifest_hash,
            to_manifest_hash=target.manifest_hash,
            from_index=baseline.item_index,
            to_index=target.item_index,
        )
        return DatasetDiffResponse(**diff.__dict__)

    store = IndicatorStore(db=db)
    try:
        indicators, payload = _current_indicator_payload(store)
    except Exception as exc:
        _handle_catalog_error("indicator_diff", exc)
    manifest = build_dataset_manifest(
        dataset="indicators",
        items=payload,
        last_modified=_indicator_last_modified(indicators),
    )
    diff = diff_dataset_indexes(
        dataset="indicators",
        from_snapshot_id=baseline.id,
        from_manifest_hash=baseline.manifest_hash,
        to_manifest_hash=manifest.manifest_hash,
        from_index=baseline.item_index,
        to_index=build_dataset_item_index("indicators", payload),
    )
    return DatasetDiffResponse(**diff.__dict__)


@router.post(
    "/import-csv-validations",
    response_model=IndicatorImportJobResponse,
    status_code=200,
    summary="Validate an indicator register CSV",
    description="Upload and validate an indicator register CSV without modifying the catalog",
)
async def validate_indicator_csv_import(
    file: UploadFile = File(..., description="SDS indicator register CSV"),
    db: Optional[Session] = Depends(get_db_optional),
    job_store=Depends(get_indicator_import_job_store),
    user: User = Depends(require_policy("indicators", "write")),
) -> IndicatorImportJobResponse:
    csv_text, size_bytes, source_hash = await _read_limited_csv_upload(file)
    try:
        plan = validate_indicator_import_text(
            csv_text,
            db=db,
            source_sha256=source_hash,
            source_size_bytes=size_bytes,
            max_rows=settings.indicator_import_max_rows,
        )
    except IndicatorCsvContractError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return job_store.create(
        job_id=str(uuid.uuid4()),
        job_type="validation",
        source_format="csv",
        submitted_by=getattr(user, "username", "anonymous"),
        source_filename=file.filename,
        source_sha256=source_hash,
        source_size_bytes=size_bytes,
        source_payload=csv_text,
        request_metadata={
            "valid": plan.valid,
            "max_rows": settings.indicator_import_max_rows,
            "max_bytes": settings.indicator_import_max_bytes,
            "catalog_compared": db is not None,
        },
        status="completed",
        result_body=plan.to_result_body(),
        total_rows=plan.total_rows,
        accepted_rows=plan.accepted_rows,
        rejected_rows=plan.rejected_rows,
        committed=False,
    )


@router.post(
    "/import-csv-jobs",
    response_model=IndicatorImportJobResponse,
    status_code=202,
    summary="Submit an async indicator register import job",
    description="Confirm an indicator CSV import as a database-backed all-or-nothing background job",
)
async def submit_indicator_csv_import_job(
    request: Request,
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None, description="SDS indicator register CSV"),
    validation_id: Optional[str] = Form(
        None, description="Completed validation job to import"
    ),
    db: Optional[Session] = Depends(get_db_optional),
    job_store=Depends(get_indicator_import_job_store),
    user: User = Depends(require_policy("indicators", "write")),
) -> IndicatorImportJobResponse:
    if db is None:
        raise HTTPException(
            status_code=503, detail="Indicator imports require database-backed mode."
        )
    if bool(file) == bool(validation_id):
        raise HTTPException(
            status_code=400, detail="Submit exactly one of file or validation_id."
        )

    if hasattr(job_store, "acquire_import_submission_lock"):
        job_store.acquire_import_submission_lock()
    if hasattr(job_store, "recover_stale_active_imports"):
        job_store.recover_stale_active_imports(commit=False)
    if job_store.has_active_import(recover_stale=False):
        raise HTTPException(
            status_code=409,
            detail="Another indicator import job is already pending or running.",
        )

    source_filename = None
    validation_job_id = None
    if validation_id:
        validation = job_store.get(validation_id)
        if validation is None or validation.job_type.value != "validation":
            raise HTTPException(
                status_code=404,
                detail=f"Indicator import validation not found: {validation_id}",
            )
        if (validation.result_body or {}).get("valid") is not True:
            raise HTTPException(
                status_code=400,
                detail="Cannot import a validation result with rejected rows.",
            )
        csv_text = job_store.get_source_payload(validation_id)
        if not csv_text:
            raise HTTPException(
                status_code=410,
                detail="Validation payload is no longer available; upload the CSV again.",
            )
        source_filename = validation.source_filename
        source_hash = validation.source_sha256 or sha256_text(csv_text)
        size_bytes = validation.source_size_bytes or len(csv_text.encode("utf-8"))
        validation_job_id = validation_id
    else:
        if file is None:
            raise HTTPException(
                status_code=400,
                detail="CSV file is required when validation_id is not provided.",
            )
        csv_text, size_bytes, source_hash = await _read_limited_csv_upload(file)
        source_filename = file.filename
        try:
            direct_plan = validate_indicator_import_text(
                csv_text,
                db=db,
                source_sha256=source_hash,
                source_size_bytes=size_bytes,
                max_rows=settings.indicator_import_max_rows,
            )
        except IndicatorCsvContractError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if not direct_plan.valid:
            raise HTTPException(status_code=400, detail=direct_plan.to_result_body())

    job_id = str(uuid.uuid4())
    response = job_store.create(
        job_id=job_id,
        job_type="import",
        source_format="csv",
        submitted_by=getattr(user, "username", "anonymous"),
        source_filename=source_filename,
        source_sha256=source_hash,
        source_size_bytes=size_bytes,
        source_payload=csv_text,
        validation_job_id=validation_job_id,
        request_metadata={
            "transaction": "all_or_nothing",
            "max_rows": settings.indicator_import_max_rows,
            "max_bytes": settings.indicator_import_max_bytes,
        },
    )
    background_tasks.add_task(
        run_indicator_import_job,
        app=request.app,
        job_id=job_id,
        csv_text=csv_text,
        source_ref=f"upload:{source_filename or job_id}",
        source_hash=source_hash,
        submitted_by=getattr(user, "username", "anonymous"),
    )
    return response


@router.get(
    "/import-jobs/{job_id}",
    response_model=IndicatorImportJobResponse,
    summary="Get indicator import job status",
    description="Retrieve the current status and result of an indicator validation/import job",
)
async def get_indicator_import_job(
    job_id: str,
    job_store=Depends(get_indicator_import_job_store),
    user: User = Depends(require_policy("indicators", "write")),
) -> IndicatorImportJobResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail=f"Indicator import job not found: {job_id}"
        )
    return job


@router.get(
    "/import-jobs/{job_id}/errors",
    response_model=IndicatorImportErrorsResponse,
    summary="Get indicator import row errors",
    description="Retrieve retained row-level errors for an indicator validation/import job",
)
async def get_indicator_import_job_errors(
    job_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    job_store=Depends(get_indicator_import_job_store),
    user: User = Depends(require_policy("indicators", "write")),
) -> IndicatorImportErrorsResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail=f"Indicator import job not found: {job_id}"
        )
    errors = _extract_job_errors(job)
    return IndicatorImportErrorsResponse(
        job_id=job_id,
        total_errors=len(errors),
        offset=offset,
        limit=limit,
        items=errors[offset : offset + limit],
    )


@router.get("/{indicator_id}", response_model=IndicatorResponse)
async def get_indicator(
    indicator_id: str,
    # Plain None default (not Query(...)) so the handler stays directly-callable in tests;
    # FastAPI still exposes it as an optional query param. (VARCH-8e pattern.)
    lang: Optional[str] = None,
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("indicators", "read")),
):
    """Get a single indicator by ID or URN identifier (with ?lang localization).

    Requires: read_indicators permission (E6 policy enforced).
    """
    # NOTE: This endpoint uses sync DB operations in an async handler. Full async migration is deferred.
    db = _require_catalog_db(db, "get_indicator")
    if db is None:
        raise HTTPException(
            status_code=404, detail=f"Indicator not found: {indicator_id}"
        )
    repo = IndicatorRepository(db)

    # Public read: match by primary key OR URN, active only (fail-closed so a
    # retired indicator is not directly retrievable — codex F04 M1).
    try:
        indicator = repo.get_active_by_id_or_identifier(indicator_id)
    except Exception as exc:
        _handle_catalog_error("get_indicator", exc)

    if not indicator:
        raise HTTPException(
            status_code=404, detail=f"Indicator not found: {indicator_id}"
        )

    item = IndicatorResponse.model_validate(indicator)
    _localize_indicators(db, [item], lang)
    return item


@router.get("/esrs/{code}", response_model=IndicatorListResponse)
async def get_indicators_by_esrs(
    code: str,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("indicators", "read")),
):
    """Get indicators by ESRS code prefix.

    Requires: read_indicators permission (E6 policy enforced).
    """
    # NOTE: This endpoint uses sync DB operations in an async handler. Full async migration is deferred.
    db = _require_catalog_db(db, "get_indicators_by_esrs")
    if db is None:
        return IndicatorListResponse(items=[], total=0, limit=limit, offset=0)
    repo = IndicatorRepository(db)
    try:
        indicators = repo.search_by_esrs(code, limit=limit)
    except Exception as exc:
        _handle_catalog_error("get_indicators_by_esrs", exc)

    return IndicatorListResponse(
        items=[IndicatorResponse.model_validate(ind) for ind in indicators],
        total=len(indicators),
        limit=limit,
        offset=0,
    )


@router.get("/gri/{code}", response_model=IndicatorListResponse)
async def get_indicators_by_gri(
    code: str,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("indicators", "read")),
):
    """Get indicators by GRI code prefix.

    Requires: read_indicators permission (E6 policy enforced).
    """
    # NOTE: This endpoint uses sync DB operations in an async handler. Full async migration is deferred.
    db = _require_catalog_db(db, "get_indicators_by_gri")
    if db is None:
        return IndicatorListResponse(items=[], total=0, limit=limit, offset=0)
    repo = IndicatorRepository(db)
    try:
        indicators = repo.search_by_gri(code, limit=limit)
    except Exception as exc:
        _handle_catalog_error("get_indicators_by_gri", exc)

    return IndicatorListResponse(
        items=[IndicatorResponse.model_validate(ind) for ind in indicators],
        total=len(indicators),
        limit=limit,
        offset=0,
    )


def _stream_indicator_json(items: Iterable[IndicatorResponse]):
    yield "["
    first = True
    for item in items:
        if not first:
            yield ","
        first = False
        # Export stays on the canonical field set — never the response-only localization fields.
        payload = item.model_dump(mode="json", include=set(_INDICATOR_EXPORT_FIELDS))
        yield json.dumps(payload, ensure_ascii=False)
    yield "]"


def _stream_indicator_csv(items: Iterable[IndicatorResponse]):
    buffer = io.StringIO()
    fieldnames = list(_INDICATOR_EXPORT_FIELDS)
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for item in items:
        writer.writerow(
            item.model_dump(mode="json", include=set(_INDICATOR_EXPORT_FIELDS))
        )
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
