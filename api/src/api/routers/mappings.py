"""API router for standard-agnostic mappings (replaces crosswalks)."""

import csv
import io
import json
from datetime import datetime
from typing import Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
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
)
from src.auth.models import User
from src.config.settings import settings
from src.database.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)
from src.database.session import get_db, get_db_optional
from src.policies.policy_enforcer import require_policy
from src.services.change_feed import (
    decode_change_feed_cursor,
    encode_change_feed_cursor,
)
from src.services.dataset_history import build_dataset_item_index, diff_dataset_indexes
from src.services.dataset_manifest import build_dataset_manifest
from src.services.dataset_serialization import serialize_mapping
from src.services.parquet_export import parquet_bytes_from_rows
from src.services.standard_mapping_store import StandardMappingStore

router = APIRouter()
logger = structlog.get_logger(__name__)


def _require_mapping_db(db: Optional[Session], operation: str) -> Optional[Session]:
    if db is None:
        if settings.require_database:
            raise HTTPException(
                status_code=503,
                detail=f"Canonical mapping data is unavailable for {operation}: no database session is available.",
            )
        return None
    return db


def _handle_mapping_error(operation: str, error: Exception) -> None:
    if settings.require_database:
        logger.error(
            "Mapping catalog unavailable", operation=operation, error=str(error)
        )
        raise HTTPException(
            status_code=503,
            detail=f"Canonical mapping data is unavailable for {operation}.",
        ) from error
    raise error


class MappingResponse(BaseModel):
    """Response model for a single mapping."""

    id: int
    source_standard: str
    source_code: str
    source_label: Optional[str] = None
    target_standard: str
    target_code: Optional[str] = None
    target_label: Optional[str] = None
    esg_dimension: Optional[str] = None
    relationship_type: Optional[str] = None
    confidence: Optional[float] = None
    dataset: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class MappingListResponse(BaseModel):
    """Response model for paginated mapping list."""

    items: List[MappingResponse]
    total: int
    limit: int
    offset: int


class SupportedStandardsResponse(BaseModel):
    """Response with list of supported standards."""

    standards: List[str]


def _mapping_last_modified(mappings: Iterable[object]) -> Optional[datetime]:
    timestamps = []
    for mapping in mappings:
        updated_at = getattr(mapping, "updated_at", None) or getattr(
            mapping, "created_at", None
        )
        if updated_at is not None:
            timestamps.append(updated_at)
    return max(timestamps) if timestamps else None


def _current_mapping_payload(
    store: StandardMappingStore,
) -> tuple[list[object], list[dict]]:
    total = store.count()
    mappings = store.get_all(limit=max(total, 1), offset=0)
    payload = [serialize_mapping(item) for item in mappings]
    return mappings, payload


def _decode_mapping_changes_cursor(cursor: Optional[str]):
    if not cursor:
        return None
    try:
        decoded = decode_change_feed_cursor(cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if decoded.dataset != ChangeFeedDataset.MAPPINGS.value:
        raise HTTPException(
            status_code=400, detail="Changed-feed cursor does not belong to mappings."
        )
    return decoded


@router.get("/standards", response_model=SupportedStandardsResponse)
async def list_supported_standards(
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("mappings", "read")),
):
    """List all supported sustainability standards."""
    # NOTE: This endpoint uses sync DB operations in an async handler. Full async migration is deferred.
    db = _require_mapping_db(db, "list_supported_standards")
    store = StandardMappingStore(db=db)
    try:
        standards = store.get_supported_standards()
    except Exception as exc:
        _handle_mapping_error("list_supported_standards", exc)
    return SupportedStandardsResponse(standards=standards)


@router.get("", response_model=MappingListResponse)
async def list_mappings(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    changed_since: Optional[datetime] = Query(
        default=None,
        description="Return only mappings created or updated on/after this timestamp",
    ),
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("mappings", "read")),
):
    """List all mappings with pagination."""
    # NOTE: This endpoint uses sync DB operations in an async handler. Full async migration is deferred.
    db = _require_mapping_db(db, "list_mappings")
    store = StandardMappingStore(db=db)
    try:
        mappings = store.get_all(
            limit=limit, offset=offset, changed_since=changed_since
        )
        total = store.count(changed_since=changed_since)
    except Exception as exc:
        _handle_mapping_error("list_mappings", exc)
    return MappingListResponse(
        items=[MappingResponse.model_validate(m) for m in mappings],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/search", response_model=MappingListResponse)
async def search_mappings(
    source_standard: Optional[str] = Query(
        default=None, description="Source standard (ESRS, GRI, ISSB, etc.)"
    ),
    source_code: Optional[str] = Query(default=None, description="Source code prefix"),
    target_standard: Optional[str] = Query(default=None, description="Target standard"),
    target_code: Optional[str] = Query(default=None, description="Target code prefix"),
    dimension: Optional[str] = Query(
        default=None, description="ESG dimension (E, S, G)"
    ),
    min_confidence: Optional[float] = Query(
        default=None, ge=0, le=1, description="Minimum confidence"
    ),
    changed_since: Optional[datetime] = Query(
        default=None,
        description="Return only mappings created or updated on/after this timestamp",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("mappings", "read")),
):
    """Search mappings with flexible filters."""
    # NOTE: This endpoint uses sync DB operations in an async handler. Full async migration is deferred.
    db = _require_mapping_db(db, "search_mappings")
    store = StandardMappingStore(db=db)
    try:
        mappings = store.search(
            source_standard=source_standard,
            source_code=source_code,
            target_standard=target_standard,
            target_code=target_code,
            dimension=dimension,
            min_confidence=min_confidence,
            limit=limit,
            changed_since=changed_since,
        )
    except Exception as exc:
        _handle_mapping_error("search_mappings", exc)
    return MappingListResponse(
        items=[MappingResponse.model_validate(m) for m in mappings],
        total=len(mappings),
        limit=limit,
        offset=0,
    )


@router.get("/manifest", response_model=DatasetManifestResponse)
async def mapping_manifest(
    source_standard: Optional[str] = Query(
        default=None, description="Source standard (ESRS, GRI, ISSB, etc.)"
    ),
    source_code: Optional[str] = Query(default=None, description="Source code prefix"),
    target_standard: Optional[str] = Query(default=None, description="Target standard"),
    target_code: Optional[str] = Query(default=None, description="Target code prefix"),
    dimension: Optional[str] = Query(
        default=None, description="ESG dimension (E, S, G)"
    ),
    min_confidence: Optional[float] = Query(
        default=None, ge=0, le=1, description="Minimum confidence"
    ),
    changed_since: Optional[datetime] = Query(
        default=None,
        description="Slice only mappings created or updated on/after this timestamp",
    ),
    limit: int = Query(default=10000, ge=1, le=50000),
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("mappings", "read")),
):
    """Return the deterministic version/audit manifest for a mapping export slice."""
    store = StandardMappingStore(db=db)
    try:
        if any(
            [
                source_standard,
                source_code,
                target_standard,
                target_code,
                dimension,
                min_confidence is not None,
            ]
        ):
            mappings = store.search(
                source_standard=source_standard,
                source_code=source_code,
                target_standard=target_standard,
                target_code=target_code,
                dimension=dimension,
                min_confidence=min_confidence,
                limit=limit,
                changed_since=changed_since,
            )
        else:
            mappings = store.get_all(limit=limit, offset=0, changed_since=changed_since)
    except Exception as exc:
        _handle_mapping_error("mapping_manifest", exc)

    payload = [serialize_mapping(item) for item in mappings]
    manifest = build_dataset_manifest(
        dataset="mappings",
        items=payload,
        last_modified=_mapping_last_modified(mappings),
    )
    return DatasetManifestResponse(**manifest.__dict__)


@router.get("/export")
async def export_mappings(
    request: Request,
    format: DatasetExportFormat = Query(
        DatasetExportFormat.CSV, description="Export format"
    ),
    source_standard: Optional[str] = Query(
        default=None, description="Source standard (ESRS, GRI, ISSB, etc.)"
    ),
    source_code: Optional[str] = Query(default=None, description="Source code prefix"),
    target_standard: Optional[str] = Query(default=None, description="Target standard"),
    target_code: Optional[str] = Query(default=None, description="Target code prefix"),
    dimension: Optional[str] = Query(
        default=None, description="ESG dimension (E, S, G)"
    ),
    min_confidence: Optional[float] = Query(
        default=None, ge=0, le=1, description="Minimum confidence"
    ),
    changed_since: Optional[datetime] = Query(
        default=None,
        description="Export only mappings created or updated on/after this timestamp",
    ),
    limit: int = Query(default=10000, ge=1, le=50000),
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("mappings", "read")),
):
    """Export mappings as a flat dataset in CSV or JSON."""
    store = StandardMappingStore(db=db)
    try:
        if any(
            [
                source_standard,
                source_code,
                target_standard,
                target_code,
                dimension,
                min_confidence is not None,
            ]
        ):
            mappings = store.search(
                source_standard=source_standard,
                source_code=source_code,
                target_standard=target_standard,
                target_code=target_code,
                dimension=dimension,
                min_confidence=min_confidence,
                limit=limit,
                changed_since=changed_since,
            )
        else:
            mappings = store.get_all(limit=limit, offset=0, changed_since=changed_since)
    except Exception as exc:
        _handle_mapping_error("export_mappings", exc)

    items = [MappingResponse.model_validate(m) for m in mappings]
    payload = [serialize_mapping(m) for m in mappings]
    manifest = build_dataset_manifest(
        dataset="mappings",
        items=payload,
        last_modified=_mapping_last_modified(mappings),
    )
    if manifest.is_not_modified(
        if_none_match=request.headers.get("if-none-match"),
        if_modified_since=request.headers.get("if-modified-since"),
    ):
        return Response(
            status_code=304, headers=manifest.response_headers(include_filename=False)
        )

    filename = f"mappings_export.{format.value}"
    headers = manifest.response_headers(filename=filename)
    if format == DatasetExportFormat.JSON:
        return StreamingResponse(
            _stream_mapping_json(items),
            media_type="application/json; charset=utf-8",
            headers=headers,
        )
    if format == DatasetExportFormat.PARQUET:
        return Response(
            content=parquet_bytes_from_rows(payload),
            media_type="application/vnd.apache.parquet",
            headers=headers,
        )

    return StreamingResponse(
        _stream_mapping_csv(items),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


@router.get("/history", response_model=DatasetSnapshotHistoryResponse)
async def mapping_history(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_policy("mappings", "read")),
):
    """Return persistent catalog snapshot history for mappings."""
    repo = DatasetSnapshotRepository(db)
    snapshots = repo.list_recent("mappings", limit=limit)
    total = repo.count("mappings")
    return DatasetSnapshotHistoryResponse(
        dataset="mappings",
        total=total,
        limit=limit,
        items=[
            DatasetSnapshotResponse.model_validate(snapshot, from_attributes=True)
            for snapshot in snapshots
        ],
    )


@router.get("/changes", response_model=ChangeFeedResponse)
async def mapping_changes(
    limit: int = Query(default=100, ge=1, le=500),
    cursor: Optional[str] = Query(
        default=None,
        description="Opaque cursor returned by the previous mappings change page",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(require_policy("mappings", "read")),
):
    """Return persisted mapping snapshot transitions in ascending change order."""
    repo = DatasetSnapshotRepository(db)
    decoded_cursor = _decode_mapping_changes_cursor(cursor)
    snapshots = repo.list_feed(
        datasets=[ChangeFeedDataset.MAPPINGS.value],
        limit=limit + 1,
        cursor_occurred_at=decoded_cursor.occurred_at if decoded_cursor else None,
        cursor_dataset=decoded_cursor.dataset if decoded_cursor else None,
        cursor_event_id=decoded_cursor.event_id if decoded_cursor else None,
    )
    has_more = len(snapshots) > limit
    selected = snapshots[:limit]
    items: list[ChangeFeedEventResponse] = []
    for snapshot in selected:
        previous = repo.get_previous(ChangeFeedDataset.MAPPINGS.value, snapshot.id)
        diff = diff_dataset_indexes(
            dataset="mappings",
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
                    dataset=ChangeFeedDataset.MAPPINGS.value,
                    event_id=str(snapshot.id),
                ),
                dataset=ChangeFeedDataset.MAPPINGS,
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
        datasets=[ChangeFeedDataset.MAPPINGS],
        limit=limit,
        items=items,
        next_cursor=items[-1].cursor if has_more and items else None,
        has_more=has_more,
    )


@router.get("/diff", response_model=DatasetDiffResponse)
async def mapping_diff(
    from_snapshot_id: int = Query(
        ..., ge=1, description="Baseline snapshot identifier"
    ),
    to_snapshot_id: Optional[int] = Query(
        default=None, ge=1, description="Optional target snapshot identifier"
    ),
    db: Session = Depends(get_db),
    user: User = Depends(require_policy("mappings", "read")),
):
    """Diff two mapping snapshots, or a stored snapshot against the current live catalog."""
    snapshot_repo = DatasetSnapshotRepository(db)
    baseline = snapshot_repo.get_by_id(from_snapshot_id)
    if baseline is None or baseline.dataset != "mappings":
        raise HTTPException(
            status_code=404, detail=f"Mapping snapshot not found: {from_snapshot_id}"
        )

    if to_snapshot_id is not None:
        target = snapshot_repo.get_by_id(to_snapshot_id)
        if target is None or target.dataset != "mappings":
            raise HTTPException(
                status_code=404, detail=f"Mapping snapshot not found: {to_snapshot_id}"
            )
        diff = diff_dataset_indexes(
            dataset="mappings",
            from_snapshot_id=baseline.id,
            to_snapshot_id=target.id,
            from_manifest_hash=baseline.manifest_hash,
            to_manifest_hash=target.manifest_hash,
            from_index=baseline.item_index,
            to_index=target.item_index,
        )
        return DatasetDiffResponse(**diff.__dict__)

    store = StandardMappingStore(db=db)
    try:
        mappings, payload = _current_mapping_payload(store)
    except Exception as exc:
        _handle_mapping_error("mapping_diff", exc)
    manifest = build_dataset_manifest(
        dataset="mappings",
        items=payload,
        last_modified=_mapping_last_modified(mappings),
    )
    diff = diff_dataset_indexes(
        dataset="mappings",
        from_snapshot_id=baseline.id,
        from_manifest_hash=baseline.manifest_hash,
        to_manifest_hash=manifest.manifest_hash,
        from_index=baseline.item_index,
        to_index=build_dataset_item_index("mappings", payload),
    )
    return DatasetDiffResponse(**diff.__dict__)


@router.get("/from/{standard}/{code}", response_model=MappingListResponse)
async def get_mappings_from(
    standard: str,
    code: str,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("mappings", "read")),
):
    """Find all mappings FROM a given standard/code."""
    # NOTE: This endpoint uses sync DB operations in an async handler. Full async migration is deferred.
    db = _require_mapping_db(db, "get_mappings_from")
    store = StandardMappingStore(db=db)
    try:
        mappings = store.find_by_source(standard.upper(), code, limit=limit)
    except Exception as exc:
        _handle_mapping_error("get_mappings_from", exc)
    if not mappings:
        raise HTTPException(
            status_code=404, detail=f"No mappings found from {standard}:{code}"
        )
    return MappingListResponse(
        items=[MappingResponse.model_validate(m) for m in mappings],
        total=len(mappings),
        limit=limit,
        offset=0,
    )


@router.get("/to/{standard}/{code}", response_model=MappingListResponse)
async def get_mappings_to(
    standard: str,
    code: str,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("mappings", "read")),
):
    """Find all mappings TO a given standard/code."""
    # NOTE: This endpoint uses sync DB operations in an async handler. Full async migration is deferred.
    db = _require_mapping_db(db, "get_mappings_to")
    store = StandardMappingStore(db=db)
    try:
        mappings = store.find_by_target(standard.upper(), code, limit=limit)
    except Exception as exc:
        _handle_mapping_error("get_mappings_to", exc)
    if not mappings:
        raise HTTPException(
            status_code=404, detail=f"No mappings found to {standard}:{code}"
        )
    return MappingListResponse(
        items=[MappingResponse.model_validate(m) for m in mappings],
        total=len(mappings),
        limit=limit,
        offset=0,
    )


@router.get(
    "/between/{source_standard}/{target_standard}", response_model=MappingListResponse
)
async def get_mappings_between(
    source_standard: str,
    target_standard: str,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Optional[Session] = Depends(get_db_optional),
    user: User = Depends(require_policy("mappings", "read")),
):
    """Find all mappings between two standards."""
    # NOTE: This endpoint uses sync DB operations in an async handler. Full async migration is deferred.
    db = _require_mapping_db(db, "get_mappings_between")
    store = StandardMappingStore(db=db)
    try:
        mappings = store.find_between_standards(
            source_standard.upper(), target_standard.upper(), limit=limit
        )
    except Exception as exc:
        _handle_mapping_error("get_mappings_between", exc)
    return MappingListResponse(
        items=[MappingResponse.model_validate(m) for m in mappings],
        total=len(mappings),
        limit=limit,
        offset=0,
    )


def _stream_mapping_json(items: Iterable[MappingResponse]):
    yield "["
    first = True
    for item in items:
        if not first:
            yield ","
        first = False
        yield json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
    yield "]"


def _stream_mapping_csv(items: Iterable[MappingResponse]):
    buffer = io.StringIO()
    fieldnames = list(MappingResponse.model_fields.keys())
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for item in items:
        writer.writerow(item.model_dump(mode="json"))
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
