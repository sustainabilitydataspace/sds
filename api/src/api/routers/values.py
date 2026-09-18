"""
API router for value management endpoints.
"""

import csv
import io
import json
import uuid
from datetime import date, datetime
from typing import Iterable, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from rdflib import Graph
from sqlalchemy.orm import Session

import structlog
from src.api.models import (
    ChangeFeedDataset,
    ChangeFeedEventResponse,
    ChangeFeedResponse,
    DatasetManifestResponse,
    PaginatedResponse,
    SuccessResponse,
    ValueBulkImportRequest,
    ValueBulkImportResponse,
    ValueContextLineageResponse,
    ValueCreate,
    ValueExportFormat,
    ValueImportJobResponse,
    ValueResolveRequest,
    ValueResolveResponse,
    ValueResponse,
    ValueRevisionChangeFeedResponse,
    ValueRevisionEventResponse,
    ValueRevisionLineageResponse,
    ValueRevisionResponse,
)
from src.auth.dependencies import (
    get_current_active_user,
    require_permission,
    require_permissions,
)
from src.auth.models import Permission, User, UserRole
from src.calculation.unit_converter import UnitConversionError, UnitConverter
from src.config.settings import settings
from src.database.session import get_db_optional
from src.ontology.local_graph import get_ontology_graph
from src.services.canonical_concept_store import CanonicalConceptStore
from src.services.change_feed import (
    decode_change_feed_cursor,
    encode_change_feed_cursor,
)
from src.services.dataset_manifest import build_dataset_manifest
from src.services.hierarchy_store import get_hierarchy_store
from src.services.indicator_store import IndicatorStore
from src.services.parquet_export import parquet_bytes_from_rows
from src.services.runtime_execution import build_conversion_engine
from src.services.standard_mapping_store import StandardMappingStore
from src.services.value_batch import execute_value_batch
from src.services.value_csv_import import ValueCsvContractError, load_values_from_handle
from src.services.value_idempotency_store import (
    IdempotencyClaim,
    IdempotencyReplay,
    build_request_hash,
    get_value_idempotency_store,
)
from src.services.value_import_job_runner import run_value_import_job
from src.services.value_import_job_store import get_value_import_job_store
from src.services.value_ingest import ValueIngestError, as_http_exception, ingest_value
from src.services.value_pagination import encode_value_cursor
from src.services.value_resolution import resolve_value_request
from src.services.value_revision_store import (
    ValueRevisionStore,
    event_to_payload,
    revision_to_payload,
)
from src.services.value_store import (
    get_value_store,
    resolve_value_store_tenant_id,
    revision_value_store_needs_tenant,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


def _canonical_concept_store_for(db: Optional[Session], store):
    if db is None or not hasattr(store, "_db"):
        return None
    return CanonicalConceptStore(db=db)


def _indicator_store_for(db: Optional[Session], store):
    if db is None or not hasattr(store, "_db"):
        return None
    return IndicatorStore(db=db)


async def _read_bounded_csv_upload(file: UploadFile) -> bytes:
    """Read a values CSV without retaining more than the configured byte cap."""
    max_bytes = settings.value_import_max_bytes
    remaining = max(max_bytes, 0)
    chunks: list[bytes] = []
    too_large_detail = (
        f"Values CSV exceeds the maximum upload size of {max_bytes} bytes."
    )

    while remaining:
        chunk = await file.read(min(1024 * 1024, remaining))
        if not chunk:
            return b"".join(chunks)
        if len(chunk) > remaining:
            raise HTTPException(
                status_code=413,
                detail=too_large_detail,
            )
        chunks.append(chunk)
        remaining -= len(chunk)

    if max_bytes < 0 or await file.read(1):
        raise HTTPException(
            status_code=413,
            detail=too_large_detail,
        )

    return b"".join(chunks)


def _value_last_modified(values: Iterable[ValueResponse]) -> Optional[datetime]:
    timestamps = []
    for value in values:
        updated_at = getattr(value, "updated_at", None) or getattr(
            value, "created_at", None
        )
        if updated_at is not None:
            timestamps.append(updated_at)
    return max(timestamps) if timestamps else None


def _decode_value_changes_cursor(cursor: Optional[str]):
    if not cursor:
        return None
    try:
        decoded = decode_change_feed_cursor(cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if decoded.dataset != ChangeFeedDataset.VALUES.value:
        raise HTTPException(
            status_code=400, detail="Changed-feed cursor does not belong to values."
        )
    return decoded


def _require_revision_db(db: Optional[Session]) -> Session:
    if not settings.value_revision_api_enabled:
        raise HTTPException(
            status_code=404,
            detail="Value revision API is disabled",
        )
    if db is None:
        raise HTTPException(
            status_code=503,
            detail="Value revision database is not initialized",
        )
    return db


def _authorized_revision_tenant_id(
    tenant_id: Optional[str],
    current_user: User,
    *,
    required: bool = False,
) -> Optional[str]:
    if _is_admin_user(current_user):
        if required and not tenant_id:
            raise HTTPException(status_code=400, detail="tenant_id is required")
        return tenant_id

    user_tenant = getattr(current_user, "company_id", None)
    if not user_tenant:
        raise HTTPException(
            status_code=403,
            detail="Access denied: user is not assigned to a tenant",
        )
    if tenant_id is not None and tenant_id != user_tenant:
        raise HTTPException(
            status_code=403,
            detail="Access denied: tenant does not match authenticated user",
        )
    return tenant_id or user_tenant


def _assert_revision_tenant_access(tenant_id: str, current_user: User) -> None:
    _authorized_revision_tenant_id(tenant_id, current_user, required=True)


def _is_admin_user(current_user: User) -> bool:
    role = getattr(current_user, "role", None)
    role_value = getattr(role, "value", role)
    return role_value == UserRole.ADMIN.value


def _value_store_tenant_id(current_user: User) -> Optional[str]:
    if revision_value_store_needs_tenant():
        return resolve_value_store_tenant_id(current_user)
    if not settings.require_database or _is_admin_user(current_user):
        return None
    return resolve_value_store_tenant_id(current_user)


async def get_unit_converter(request: Request) -> UnitConverter:
    """Dependency to get unit converter from app state."""
    converter = getattr(request.app.state, "unit_converter", None)
    if converter is None:
        raise HTTPException(status_code=503, detail="Unit converter not initialized")
    return converter


def get_authenticated_value_store(
    request: Request,
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
):
    tenant_id = _value_store_tenant_id(current_user)
    return get_value_store(request=request, db=db, tenant_id=tenant_id)


@router.post(
    "/values",
    response_model=ValueResponse,
    status_code=201,
    summary="Create a new value",
    description="Insert a new value with automatic unit conversion to standard units",
    dependencies=[Depends(require_permission(Permission.CREATE_VALUES))],
)
async def create_value(
    value_data: ValueCreate,
    converter: UnitConverter = Depends(get_unit_converter),
    store=Depends(get_authenticated_value_store),
    idempotency_store=Depends(get_value_idempotency_store),
    hierarchy_store=Depends(get_hierarchy_store),
    db: Optional[Session] = Depends(get_db_optional),
    graph: Graph = Depends(get_ontology_graph),
    current_user: User = Depends(get_current_active_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
) -> ValueResponse:
    """
    Create a new value with automatic unit conversion.

    - **concept**: Concept URI (e.g., 'urn:sds:reg:esrs:e3_5_01')
    - **entity**: Entity ID (e.g., 'nh_es_valencia_plant')
    - **period**: Period date
    - **value**: Numeric value
    - **unit**: Unit of measurement
    - **metadata**: Optional additional metadata
    """
    endpoint_owns_transaction = _endpoint_owns_value_write_transaction(
        db=db,
        store=store,
        idempotency_store=idempotency_store,
        idempotency_key=idempotency_key,
    )
    claim = None
    deferred_claim = False
    try:
        logger.info(
            "Creating new value",
            concept=value_data.concept,
            entity=value_data.entity,
            period=str(value_data.period),
            value=value_data.value,
            unit=value_data.unit,
        )

        claim = _claim_idempotency(
            store=idempotency_store,
            current_user=current_user,
            scope="values:create",
            idempotency_key=idempotency_key,
            payload=value_data.model_dump(mode="json"),
            commit=not endpoint_owns_transaction,
        )
        if isinstance(claim, JSONResponse):
            return claim
        deferred_claim = endpoint_owns_transaction and isinstance(
            claim, IdempotencyClaim
        )

        value_id = str(uuid.uuid4())
        conversion_engine = (
            build_conversion_engine(db, converter) if db is not None else None
        )
        response = ingest_value(
            value_id=value_id,
            value_data=value_data,
            converter=converter,
            conversion_engine=conversion_engine,
            store=store,
            hierarchy_store=hierarchy_store,
            indicator_store=_indicator_store_for(db, store),
            canonical_concept_store=_canonical_concept_store_for(db, store),
            graph=graph,
            created_by=getattr(current_user, "username", None),
            company_id=getattr(current_user, "company_id", None),
            strict=bool(settings.require_database),
            **({"commit": False} if deferred_claim else {}),
        )
        _complete_idempotency(
            store=idempotency_store,
            current_user=current_user,
            scope="values:create",
            idempotency_key=idempotency_key,
            claim=claim,
            response_status=201,
            response_body=response.model_dump(mode="json"),
            commit=not deferred_claim,
        )
        if deferred_claim:
            db.commit()

        logger.info(
            "Value created successfully",
            value_id=value_id,
            concept=value_data.concept,
            entity=value_data.entity,
        )

        return response

    except HTTPException:
        _rollback_or_abandon_idempotency(
            db=db,
            deferred_claim=deferred_claim,
            store=idempotency_store,
            current_user=current_user,
            scope="values:create",
            idempotency_key=idempotency_key,
            claim=claim,
        )
        raise
    except ValueIngestError as e:
        _rollback_or_abandon_idempotency(
            db=db,
            deferred_claim=deferred_claim,
            store=idempotency_store,
            current_user=current_user,
            scope="values:create",
            idempotency_key=idempotency_key,
            claim=claim,
        )
        raise as_http_exception(e) from e
    except UnitConversionError as e:
        _rollback_or_abandon_idempotency(
            db=db,
            deferred_claim=deferred_claim,
            store=idempotency_store,
            current_user=current_user,
            scope="values:create",
            idempotency_key=idempotency_key,
            claim=claim,
        )
        logger.error(
            "Failed to create value due to unit conversion error",
            error=str(e),
            concept=value_data.concept,
            entity=value_data.entity,
        )
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        _rollback_or_abandon_idempotency(
            db=db,
            deferred_claim=deferred_claim,
            store=idempotency_store,
            current_user=current_user,
            scope="values:create",
            idempotency_key=idempotency_key,
            claim=claim,
        )
        logger.error(
            "Failed to create value",
            error=str(e),
            concept=value_data.concept,
            entity=value_data.entity,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/values/import",
    response_model=ValueBulkImportResponse,
    status_code=201,
    summary="Import values as one batch",
    description="Validate, normalize, and persist up to 1000 values as one transactional batch",
    dependencies=[Depends(require_permission(Permission.CREATE_VALUES))],
)
async def import_values(
    import_data: ValueBulkImportRequest,
    converter: UnitConverter = Depends(get_unit_converter),
    store=Depends(get_authenticated_value_store),
    idempotency_store=Depends(get_value_idempotency_store),
    hierarchy_store=Depends(get_hierarchy_store),
    db: Optional[Session] = Depends(get_db_optional),
    graph: Graph = Depends(get_ontology_graph),
    current_user: User = Depends(get_current_active_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Bulk import values with row-level reporting and request-level atomic persistence."""
    endpoint_owns_transaction = _endpoint_owns_value_write_transaction(
        db=db,
        store=store,
        idempotency_store=idempotency_store,
        idempotency_key=idempotency_key,
    )
    claim = _claim_idempotency(
        store=idempotency_store,
        current_user=current_user,
        scope="values:import-json",
        idempotency_key=idempotency_key,
        payload=import_data.model_dump(mode="json"),
        commit=not endpoint_owns_transaction,
    )
    if isinstance(claim, JSONResponse):
        return claim
    deferred_claim = endpoint_owns_transaction and isinstance(claim, IdempotencyClaim)

    try:
        conversion_engine = (
            build_conversion_engine(db, converter) if db is not None else None
        )
        response = execute_value_batch(
            items=import_data.items,
            converter=converter,
            conversion_engine=conversion_engine,
            store=store,
            hierarchy_store=hierarchy_store,
            indicator_store=_indicator_store_for(db, store),
            canonical_concept_store=_canonical_concept_store_for(db, store),
            graph=graph,
            created_by=getattr(current_user, "username", None),
            company_id=getattr(current_user, "company_id", None),
            strict=bool(settings.require_database),
            **({"commit": False} if deferred_claim else {}),
        )
    except Exception:
        _rollback_or_abandon_idempotency(
            db=db,
            deferred_claim=deferred_claim,
            store=idempotency_store,
            current_user=current_user,
            scope="values:import-json",
            idempotency_key=idempotency_key,
            claim=claim,
        )
        raise

    if not response.committed:
        _rollback_or_abandon_idempotency(
            db=db,
            deferred_claim=deferred_claim,
            store=idempotency_store,
            current_user=current_user,
            scope="values:import-json",
            idempotency_key=idempotency_key,
            claim=claim,
        )
        return JSONResponse(status_code=400, content=response.model_dump(mode="json"))

    try:
        _complete_idempotency(
            store=idempotency_store,
            current_user=current_user,
            scope="values:import-json",
            idempotency_key=idempotency_key,
            claim=claim,
            response_status=201,
            response_body=response.model_dump(mode="json"),
            commit=not deferred_claim,
        )
        if deferred_claim:
            db.commit()
    except Exception:
        _rollback_or_abandon_idempotency(
            db=db,
            deferred_claim=deferred_claim,
            store=idempotency_store,
            current_user=current_user,
            scope="values:import-json",
            idempotency_key=idempotency_key,
            claim=claim,
        )
        raise
    return response


@router.post(
    "/values/import-csv",
    response_model=ValueBulkImportResponse,
    status_code=201,
    summary="Import values from CSV",
    description="Import values from a CSV file using the strict operational values contract",
    dependencies=[Depends(require_permission(Permission.CREATE_VALUES))],
)
async def import_values_csv(
    file: UploadFile = File(
        ...,
        description="CSV file with concept,entity,period,value,unit[,metadata_json]",
    ),
    default_entity: Optional[str] = Form(
        None, description="Default entity when CSV entity cells are blank"
    ),
    converter: UnitConverter = Depends(get_unit_converter),
    store=Depends(get_authenticated_value_store),
    idempotency_store=Depends(get_value_idempotency_store),
    hierarchy_store=Depends(get_hierarchy_store),
    db: Optional[Session] = Depends(get_db_optional),
    graph: Graph = Depends(get_ontology_graph),
    current_user: User = Depends(get_current_active_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Bulk import values from the strict CSV operational contract."""
    raw_bytes = await _read_bounded_csv_upload(file)
    try:
        decoded = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=400, detail="Values CSV must be valid UTF-8 text"
        ) from error

    try:
        items = load_values_from_handle(
            io.StringIO(decoded),
            default_entity=default_entity,
            max_rows=settings.value_import_max_rows,
        )
    except ValueCsvContractError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    endpoint_owns_transaction = _endpoint_owns_value_write_transaction(
        db=db,
        store=store,
        idempotency_store=idempotency_store,
        idempotency_key=idempotency_key,
    )
    claim = _claim_idempotency(
        store=idempotency_store,
        current_user=current_user,
        scope="values:import-csv",
        idempotency_key=idempotency_key,
        payload={
            "csv_sha256": build_request_hash(
                raw_bytes.decode("utf-8", errors="replace")
            ),
            "default_entity": default_entity,
        },
        commit=not endpoint_owns_transaction,
    )
    if isinstance(claim, JSONResponse):
        return claim
    deferred_claim = endpoint_owns_transaction and isinstance(claim, IdempotencyClaim)

    try:
        conversion_engine = (
            build_conversion_engine(db, converter) if db is not None else None
        )
        response = execute_value_batch(
            items=items,
            converter=converter,
            conversion_engine=conversion_engine,
            store=store,
            hierarchy_store=hierarchy_store,
            indicator_store=_indicator_store_for(db, store),
            canonical_concept_store=_canonical_concept_store_for(db, store),
            graph=graph,
            created_by=getattr(current_user, "username", None),
            company_id=getattr(current_user, "company_id", None),
            strict=bool(settings.require_database),
            **({"commit": False} if deferred_claim else {}),
        )
    except Exception:
        _rollback_or_abandon_idempotency(
            db=db,
            deferred_claim=deferred_claim,
            store=idempotency_store,
            current_user=current_user,
            scope="values:import-csv",
            idempotency_key=idempotency_key,
            claim=claim,
        )
        raise

    if not response.committed:
        _rollback_or_abandon_idempotency(
            db=db,
            deferred_claim=deferred_claim,
            store=idempotency_store,
            current_user=current_user,
            scope="values:import-csv",
            idempotency_key=idempotency_key,
            claim=claim,
        )
        return JSONResponse(status_code=400, content=response.model_dump(mode="json"))

    try:
        _complete_idempotency(
            store=idempotency_store,
            current_user=current_user,
            scope="values:import-csv",
            idempotency_key=idempotency_key,
            claim=claim,
            response_status=201,
            response_body=response.model_dump(mode="json"),
            commit=not deferred_claim,
        )
        if deferred_claim:
            db.commit()
    except Exception:
        _rollback_or_abandon_idempotency(
            db=db,
            deferred_claim=deferred_claim,
            store=idempotency_store,
            current_user=current_user,
            scope="values:import-csv",
            idempotency_key=idempotency_key,
            claim=claim,
        )
        raise
    return response


@router.post(
    "/values/import-jobs",
    response_model=ValueImportJobResponse,
    status_code=202,
    summary="Submit an async values import job",
    description="Persist a values import job and execute it asynchronously in the API runtime",
    dependencies=[Depends(require_permission(Permission.CREATE_VALUES))],
)
async def import_values_job(
    import_data: ValueBulkImportRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    job_store=Depends(get_value_import_job_store),
    idempotency_store=Depends(get_value_idempotency_store),
    current_user: User = Depends(get_current_active_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
) -> ValueImportJobResponse | JSONResponse:
    claim = _claim_idempotency(
        store=idempotency_store,
        current_user=current_user,
        scope="values:import-job-json",
        idempotency_key=idempotency_key,
        payload=import_data.model_dump(mode="json"),
    )
    if isinstance(claim, JSONResponse):
        return claim

    job_id = str(uuid.uuid4())
    response = job_store.create(
        job_id=job_id,
        source_format="json",
        submitted_by=getattr(current_user, "username", "anonymous"),
        request_metadata={"row_count": len(import_data.items)},
    )
    background_tasks.add_task(
        run_value_import_job,
        app=request.app,
        job_id=job_id,
        items=import_data.items,
        submitted_by=getattr(current_user, "username", "anonymous"),
        company_id=getattr(current_user, "company_id", None),
    )
    _complete_idempotency(
        store=idempotency_store,
        current_user=current_user,
        scope="values:import-job-json",
        idempotency_key=idempotency_key,
        claim=claim,
        response_status=202,
        response_body=response.model_dump(mode="json"),
    )
    return response


@router.post(
    "/values/import-csv-jobs",
    response_model=ValueImportJobResponse,
    status_code=202,
    summary="Submit an async CSV values import job",
    description="Persist a CSV values import job and execute it asynchronously in the API runtime",
    dependencies=[Depends(require_permission(Permission.CREATE_VALUES))],
)
async def import_values_csv_job(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(
        ...,
        description="CSV file with concept,entity,period,value,unit[,metadata_json]",
    ),
    default_entity: Optional[str] = Form(
        None, description="Default entity when CSV entity cells are blank"
    ),
    job_store=Depends(get_value_import_job_store),
    idempotency_store=Depends(get_value_idempotency_store),
    current_user: User = Depends(get_current_active_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    raw_bytes = await _read_bounded_csv_upload(file)
    try:
        decoded = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=400, detail="Values CSV must be valid UTF-8 text"
        ) from error

    try:
        items = load_values_from_handle(
            io.StringIO(decoded),
            default_entity=default_entity,
            max_rows=settings.value_import_max_rows,
        )
    except ValueCsvContractError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    claim = _claim_idempotency(
        store=idempotency_store,
        current_user=current_user,
        scope="values:import-job-csv",
        idempotency_key=idempotency_key,
        payload={
            "csv_sha256": build_request_hash(
                raw_bytes.decode("utf-8", errors="replace")
            ),
            "default_entity": default_entity,
        },
    )
    if isinstance(claim, JSONResponse):
        return claim

    job_id = str(uuid.uuid4())
    response = job_store.create(
        job_id=job_id,
        source_format="csv",
        submitted_by=getattr(current_user, "username", "anonymous"),
        source_filename=file.filename,
        request_metadata={"row_count": len(items), "default_entity": default_entity},
    )
    background_tasks.add_task(
        run_value_import_job,
        app=request.app,
        job_id=job_id,
        items=items,
        submitted_by=getattr(current_user, "username", "anonymous"),
        company_id=getattr(current_user, "company_id", None),
    )
    _complete_idempotency(
        store=idempotency_store,
        current_user=current_user,
        scope="values:import-job-csv",
        idempotency_key=idempotency_key,
        claim=claim,
        response_status=202,
        response_body=response.model_dump(mode="json"),
    )
    return response


@router.get(
    "/values/import-jobs/{job_id}",
    response_model=ValueImportJobResponse,
    summary="Get value import job status",
    description="Retrieve the current status and result of an async value import job",
    dependencies=[Depends(require_permission(Permission.READ_VALUES))],
)
async def get_value_import_job(
    job_id: str,
    job_store=Depends(get_value_import_job_store),
    current_user: User = Depends(get_current_active_user),
) -> ValueImportJobResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail=f"Value import job not found: {job_id}"
        )
    if (
        job.submitted_by != getattr(current_user, "username", None)
        and getattr(current_user, "role", None) != "admin"
    ):
        raise HTTPException(
            status_code=403, detail="Not authorized to inspect this value import job"
        )
    return job


@router.get(
    "/values",
    response_model=PaginatedResponse,
    summary="Get values with filters",
    description="Retrieve values with optional filtering by concept, entity, period, etc.",
    dependencies=[Depends(require_permission(Permission.READ_VALUES))],
)
async def get_values(
    concept: Optional[str] = Query(None, description="Filter by concept URI"),
    entity: Optional[str] = Query(None, description="Filter by entity ID"),
    period_start: Optional[date] = Query(
        None, description="Start date for period filter"
    ),
    period_end: Optional[date] = Query(None, description="End date for period filter"),
    unit: Optional[str] = Query(None, description="Filter by unit"),
    changed_since: Optional[datetime] = Query(
        None,
        description="Return only values created or updated on/after this timestamp",
    ),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    cursor: Optional[str] = Query(
        None,
        description=(
            "Opaque cursor for stable seek pagination. Leave blank on the first "
            "request; only paste a next_cursor returned by a previous values response."
        ),
    ),
    store=Depends(get_authenticated_value_store),
    current_user: User = Depends(get_current_active_user),
) -> PaginatedResponse:
    """
    Get values with optional filtering.

    - **concept**: Filter by concept URI
    - **entity**: Filter by entity ID
    - **period_start**: Start date for period range
    - **period_end**: End date for period range
    - **unit**: Filter by unit
    - **limit**: Maximum results (1-1000)
    - **offset**: Results to skip for pagination
    - **cursor**: Opaque cursor returned as `next_cursor`; leave blank on the first page
    """
    try:
        logger.info(
            "Retrieving values",
            concept=concept,
            entity=entity,
            period_start=str(period_start) if period_start else None,
            period_end=str(period_end) if period_end else None,
            unit=unit,
            changed_since=changed_since.isoformat() if changed_since else None,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )

        if cursor:
            paginated_values, total, next_cursor, has_more = store.list_cursor(
                concept=concept,
                entity=entity,
                period_start=period_start,
                period_end=period_end,
                unit=unit,
                changed_since=changed_since,
                limit=limit,
                cursor=cursor,
            )
            page = 1
            pages = (total + limit - 1) // limit if total else 0
        else:
            paginated_values, total = store.list(
                concept=concept,
                entity=entity,
                period_start=period_start,
                period_end=period_end,
                unit=unit,
                changed_since=changed_since,
                limit=limit,
                offset=offset,
            )
            has_more = (offset + len(paginated_values)) < total
            next_cursor = None
            if has_more and paginated_values:
                last = paginated_values[-1]
                next_cursor = encode_value_cursor(
                    period=last.period,
                    created_at=last.created_at,
                    value_id=last.id,
                )
            page = (offset // limit) + 1
            pages = (total + limit - 1) // limit if total else 0

        response = PaginatedResponse(
            items=paginated_values,
            total=total,
            page=page,
            size=len(paginated_values),
            pages=pages,
            next_cursor=next_cursor,
            has_more=has_more,
        )

        logger.info(
            "Values retrieved successfully", total=total, returned=len(paginated_values)
        )

        return response

    except Exception as e:
        if isinstance(e, ValueError):
            raise HTTPException(status_code=400, detail=str(e)) from e
        logger.error("Failed to retrieve values", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/values/resolve",
    response_model=ValueResolveResponse,
    summary="Resolve one value through SDS runtime evidence",
    description=(
        "Resolve a requested concept/entity/period value from direct stored values, "
        "calculation contracts, exact/equivalent mappings, and supported unit "
        "conversions. Non-equivalent mappings and unsupported conversions return "
        "structured refusals unless a certified bridge contract explicitly "
        "authorizes a calculation route."
    ),
    dependencies=[
        Depends(
            require_permissions(
                Permission.READ_VALUES,
                Permission.EXECUTE_CALCULATIONS,
                Permission.CONVERT_UNITS,
                Permission.READ_MAPPINGS,
            )
        )
    ],
)
async def resolve_value(
    request_data: ValueResolveRequest,
    http_request: Request,
    store=Depends(get_authenticated_value_store),
    unit_converter: UnitConverter = Depends(get_unit_converter),
    graph: Graph = Depends(get_ontology_graph),
    hierarchy_store=Depends(get_hierarchy_store),
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
) -> ValueResolveResponse:
    """Resolve one requested value using the real SDS runtime surfaces."""
    from src.api.routers import calculations

    conversion_engine = (
        build_conversion_engine(db, unit_converter) if db is not None else None
    )
    mapping_store = StandardMappingStore(db=db) if db is not None else None
    hierarchy_company_id = calculations._hierarchy_scope_company_id(current_user)

    async def calculate(calc_request):
        return await calculations._run_calculation(
            calc_request,
            graph=graph,
            store=store,
            db=db,
            unit_normalizer=unit_converter,
            conversion_engine=conversion_engine,
            hierarchy_store=hierarchy_store,
            hierarchy_company_id=hierarchy_company_id,
            mapping_store=mapping_store,
        )

    return await resolve_value_request(
        request_data,
        value_store=store,
        unit_converter=unit_converter,
        mapping_store=mapping_store,
        calculate=calculate,
    )


@router.get(
    "/values/changes",
    response_model=ChangeFeedResponse,
    summary="Read the persisted values changed feed",
    description=(
        "Return a current-state compatibility feed in ascending updated-at order. "
        "For append-only tenant-local CDC without timestamp commit-order ambiguity, "
        "use /api/v1/values/revisions/changes."
    ),
    dependencies=[Depends(require_permission(Permission.READ_VALUES))],
)
async def get_value_changes(
    concept: Optional[str] = Query(None, description="Filter by concept URI"),
    entity: Optional[str] = Query(None, description="Filter by entity ID"),
    period_start: Optional[date] = Query(
        None, description="Start date for period filter"
    ),
    period_end: Optional[date] = Query(None, description="End date for period filter"),
    unit: Optional[str] = Query(None, description="Filter by unit"),
    changed_since: Optional[datetime] = Query(
        None, description="Return only value changes on/after this timestamp"
    ),
    limit: int = Query(
        100, ge=1, le=500, description="Maximum number of changed records"
    ),
    cursor: Optional[str] = Query(
        None, description="Opaque cursor returned by the previous values change page"
    ),
    store=Depends(get_authenticated_value_store),
    current_user: User = Depends(get_current_active_user),
) -> ChangeFeedResponse:
    decoded_cursor = _decode_value_changes_cursor(cursor)
    try:
        changed_values, next_cursor_tuple, has_more = store.list_changes(
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
            unit=unit,
            changed_since=changed_since,
            limit=limit,
            cursor=decoded_cursor,
        )
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.error("Failed to retrieve value changes", error=str(exc))
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    items = []
    for value in changed_values:
        cursor_value = encode_change_feed_cursor(
            occurred_at=value.updated_at,
            dataset=ChangeFeedDataset.VALUES.value,
            event_id=value.id,
        )
        items.append(
            ChangeFeedEventResponse(
                cursor=cursor_value,
                dataset=ChangeFeedDataset.VALUES,
                event_type="row_changed",
                occurred_at=value.updated_at,
                event_id=value.id,
                record_count=1,
                operation=(
                    "created" if value.created_at == value.updated_at else "updated"
                ),
                record=value.model_dump(mode="json"),
            )
        )

    next_cursor = None
    if has_more and next_cursor_tuple is not None:
        next_cursor = encode_change_feed_cursor(
            occurred_at=datetime.fromisoformat(next_cursor_tuple[0]),
            dataset=ChangeFeedDataset.VALUES.value,
            event_id=next_cursor_tuple[1],
        )

    return ChangeFeedResponse(
        datasets=[ChangeFeedDataset.VALUES],
        limit=limit,
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/values/export",
    summary="Export values",
    description="Export filtered values as CSV or JSON using deterministic ordering",
    dependencies=[Depends(require_permission(Permission.READ_VALUES))],
)
async def export_values(
    request: Request,
    concept: Optional[str] = Query(None, description="Filter by concept URI"),
    entity: Optional[str] = Query(None, description="Filter by entity ID"),
    period_start: Optional[date] = Query(
        None, description="Start date for period filter"
    ),
    period_end: Optional[date] = Query(None, description="End date for period filter"),
    unit: Optional[str] = Query(None, description="Filter by unit"),
    changed_since: Optional[datetime] = Query(
        None,
        description="Export only values created or updated on/after this timestamp",
    ),
    format: ValueExportFormat = Query(
        ValueExportFormat.CSV, description="Export format"
    ),
    store=Depends(get_authenticated_value_store),
    current_user: User = Depends(get_current_active_user),
):
    """Export values using the operational values contract."""
    values = list(
        store.iterate(
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
            unit=unit,
            changed_since=changed_since,
        )
    )
    payload = [value.model_dump(mode="json") for value in values]
    manifest = build_dataset_manifest(
        dataset="values",
        items=payload,
        last_modified=_value_last_modified(values),
    )
    if manifest.is_not_modified(
        if_none_match=request.headers.get("if-none-match"),
        if_modified_since=request.headers.get("if-modified-since"),
    ):
        return Response(
            status_code=304, headers=manifest.response_headers(include_filename=False)
        )

    filename = f"values_export.{format.value}"
    headers = manifest.response_headers(filename=filename)
    if format == ValueExportFormat.JSON:
        return StreamingResponse(
            _stream_values_json(values),
            media_type="application/json; charset=utf-8",
            headers=headers,
        )
    if format == ValueExportFormat.PARQUET:
        return Response(
            content=parquet_bytes_from_rows(payload),
            media_type="application/vnd.apache.parquet",
            headers=headers,
        )

    return StreamingResponse(
        _stream_values_csv(values),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


@router.get(
    "/values/manifest",
    response_model=DatasetManifestResponse,
    summary="Value export manifest",
    description="Return the deterministic manifest for a filtered values export",
    dependencies=[Depends(require_permission(Permission.READ_VALUES))],
)
async def values_manifest(
    concept: Optional[str] = Query(None, description="Filter by concept URI"),
    entity: Optional[str] = Query(None, description="Filter by entity ID"),
    period_start: Optional[date] = Query(
        None, description="Start date for period filter"
    ),
    period_end: Optional[date] = Query(None, description="End date for period filter"),
    unit: Optional[str] = Query(None, description="Filter by unit"),
    changed_since: Optional[datetime] = Query(
        None, description="Slice only values created or updated on/after this timestamp"
    ),
    store=Depends(get_authenticated_value_store),
    current_user: User = Depends(get_current_active_user),
) -> DatasetManifestResponse:
    values = list(
        store.iterate(
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
            unit=unit,
            changed_since=changed_since,
        )
    )
    manifest = build_dataset_manifest(
        dataset="values",
        items=[value.model_dump(mode="json") for value in values],
        last_modified=_value_last_modified(values),
    )
    return DatasetManifestResponse(**manifest.__dict__)


@router.get(
    "/values/revisions",
    response_model=PaginatedResponse,
    summary="List value revisions",
    description="Read immutable value revisions without changing the current /values compatibility surface.",
    dependencies=[Depends(require_permission(Permission.READ_VALUES))],
)
async def get_value_revisions(
    tenant_id: Optional[str] = Query(None, description="Filter by tenant identifier"),
    context_id: Optional[int] = Query(None, description="Filter by value context ID"),
    state: Optional[str] = Query(None, description="Filter by revision state"),
    limit: int = Query(100, ge=1, le=500, description="Maximum revisions to return"),
    offset: int = Query(0, ge=0, description="Number of revisions to skip"),
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
) -> PaginatedResponse:
    revision_store = ValueRevisionStore(_require_revision_db(db))
    scoped_tenant_id = _authorized_revision_tenant_id(tenant_id, current_user)
    revisions = revision_store.list_revisions(
        tenant_id=scoped_tenant_id,
        context_id=context_id,
        state=state,
        limit=limit,
        offset=offset,
    )
    total = revision_store.count_revisions(
        tenant_id=scoped_tenant_id,
        context_id=context_id,
        state=state,
    )
    has_more = offset + len(revisions) < total
    return PaginatedResponse(
        items=[
            ValueRevisionResponse(**revision_to_payload(revision))
            for revision in revisions
        ],
        total=total,
        page=(offset // limit) + 1,
        size=len(revisions),
        pages=(total + limit - 1) // limit if total else 0,
        next_cursor=None,
        has_more=has_more,
    )


@router.get(
    "/values/revisions/changes",
    response_model=ValueRevisionChangeFeedResponse,
    summary="Read value revision change events",
    description="Return append-only value revision events in tenant-local event sequence order.",
    dependencies=[Depends(require_permission(Permission.READ_VALUES))],
)
async def get_value_revision_changes(
    tenant_id: str = Query(..., description="Tenant identifier"),
    after_event_seq: Optional[int] = Query(
        None,
        ge=0,
        description="Return events strictly after this tenant-local sequence",
    ),
    limit: int = Query(100, ge=1, le=500, description="Maximum events to return"),
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
) -> ValueRevisionChangeFeedResponse:
    revision_store = ValueRevisionStore(_require_revision_db(db))
    scoped_tenant_id = _authorized_revision_tenant_id(
        tenant_id,
        current_user,
        required=True,
    )
    raw_events = revision_store.list_events(
        tenant_id=scoped_tenant_id,
        after_event_seq=after_event_seq,
        limit=limit + 1,
    )
    events = raw_events[:limit]
    has_more = len(raw_events) > limit
    return ValueRevisionChangeFeedResponse(
        tenant_id=scoped_tenant_id or tenant_id,
        limit=limit,
        items=[
            ValueRevisionEventResponse(**event_to_payload(event)) for event in events
        ],
        next_after_event_seq=events[-1].event_seq if has_more and events else None,
        has_more=has_more,
    )


@router.get(
    "/values/revisions/{revision_id}/lineage",
    response_model=ValueRevisionLineageResponse,
    summary="Read value revision lineage",
    description="Return context identity, parent revision, and calculation/input revision lineage for one value revision.",
    dependencies=[Depends(require_permission(Permission.READ_VALUES))],
)
async def get_value_revision_lineage(
    revision_id: str,
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
) -> ValueRevisionLineageResponse:
    revision_store = ValueRevisionStore(_require_revision_db(db))
    try:
        lineage = revision_store.get_revision_lineage(revision_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _assert_revision_tenant_access(lineage["context"]["tenant_id"], current_user)
    return ValueRevisionLineageResponse(**lineage)


@router.get(
    "/values/contexts/{context_id}/lineage",
    response_model=ValueContextLineageResponse,
    summary="Read value context lineage",
    description="Return all revisions, events, and the current pointer for one value context.",
    dependencies=[Depends(require_permission(Permission.READ_VALUES))],
)
async def get_value_context_lineage(
    context_id: int,
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
) -> ValueContextLineageResponse:
    revision_store = ValueRevisionStore(_require_revision_db(db))
    try:
        lineage = revision_store.get_context_lineage(context_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _assert_revision_tenant_access(lineage["context"]["tenant_id"], current_user)
    return ValueContextLineageResponse(**lineage)


@router.get(
    "/values/{value_id}",
    response_model=ValueResponse,
    summary="Get value by ID",
    description="Retrieve a specific value by its ID",
    dependencies=[Depends(require_permission(Permission.READ_VALUES))],
)
async def get_value_by_id(
    value_id: str,
    store=Depends(get_authenticated_value_store),
    current_user: User = Depends(get_current_active_user),
) -> ValueResponse:
    """
    Get a specific value by ID.

    - **value_id**: Unique value identifier
    """
    try:
        logger.info("Retrieving value by ID", value_id=value_id)

        # Simulate value lookup
        if not _is_valid_uuid(value_id):
            raise HTTPException(status_code=400, detail="Invalid value ID format")

        value = store.get(value_id)
        if not value:
            raise HTTPException(status_code=404, detail=f"Value not found: {value_id}")

        logger.info("Value retrieved successfully", value_id=value_id)
        return value

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to retrieve value", error=str(e), value_id=value_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete(
    "/values/{value_id}",
    response_model=SuccessResponse,
    summary="Delete value by ID",
    description="Delete a specific value by its ID",
    dependencies=[Depends(require_permission(Permission.DELETE_VALUES))],
)
async def delete_value(
    value_id: str,
    store=Depends(get_authenticated_value_store),
    current_user: User = Depends(get_current_active_user),
) -> SuccessResponse:
    """
    Delete a specific value by ID.

    - **value_id**: Unique value identifier
    """
    try:
        logger.info("Deleting value", value_id=value_id)

        # Validate UUID format
        if not _is_valid_uuid(value_id):
            raise HTTPException(status_code=400, detail="Invalid value ID format")

        deleted = store.delete(value_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Value not found: {value_id}")

        logger.info("Value deleted successfully", value_id=value_id)

        return SuccessResponse(
            success=True, message=f"Value {value_id} deleted successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete value", error=str(e), value_id=value_id)
        raise HTTPException(status_code=500, detail="Internal server error")


def _is_valid_uuid(uuid_string: str) -> bool:
    """Validate UUID format."""
    try:
        uuid.UUID(uuid_string)
        return True
    except ValueError:
        return False


def _stream_values_json(values: Iterable[ValueResponse]):
    yield "["
    first = True
    for value in values:
        if not first:
            yield ","
        yield json.dumps(value.model_dump(mode="json"), ensure_ascii=False)
        first = False
    yield "]"


def _stream_values_csv(values: Iterable[ValueResponse]):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "concept",
            "entity",
            "period",
            "external_key",
            "value",
            "value_type",
            "unit",
            "original_unit",
            "conversion_applied",
            "metadata_json",
            "created_at",
            "updated_at",
        ]
    )
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for value in values:
        writer.writerow(
            [
                value.id,
                value.concept,
                value.entity,
                value.period.isoformat(),
                value.external_key or "",
                value.value,
                value.value_type,
                value.unit,
                value.original_unit or "",
                str(bool(value.conversion_applied)).lower(),
                json.dumps(value.metadata or {}, ensure_ascii=False),
                value.created_at.isoformat(),
                value.updated_at.isoformat(),
            ]
        )
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


def _claim_idempotency(
    *,
    store,
    current_user: User,
    scope: str,
    idempotency_key: Optional[str],
    payload,
    commit: bool = True,
) -> Optional[IdempotencyClaim | JSONResponse]:
    if not idempotency_key:
        return None

    claim_kwargs = dict(
        user_id=getattr(
            current_user, "id", getattr(current_user, "username", "anonymous")
        ),
        scope=scope,
        idempotency_key=idempotency_key,
        request_hash=build_request_hash(payload),
    )
    if not commit:
        claim_kwargs["commit"] = False
    result = store.claim(**claim_kwargs)
    if isinstance(result, IdempotencyReplay):
        return JSONResponse(status_code=result.status_code, content=result.body)
    return result


def _complete_idempotency(
    *,
    store,
    current_user: User,
    scope: str,
    idempotency_key: Optional[str],
    claim: Optional[IdempotencyClaim],
    response_status: int,
    response_body: dict,
    commit: bool = True,
) -> None:
    if not idempotency_key or claim is None:
        return
    complete_kwargs = dict(
        user_id=getattr(
            current_user, "id", getattr(current_user, "username", "anonymous")
        ),
        scope=scope,
        idempotency_key=idempotency_key,
        record_id=claim.record_id,
        response_status=response_status,
        response_body=response_body,
    )
    if not commit:
        complete_kwargs["commit"] = False
    store.complete(**complete_kwargs)


def _endpoint_owns_value_write_transaction(
    *,
    db: Optional[Session],
    store,
    idempotency_store,
    idempotency_key: Optional[str],
) -> bool:
    return bool(
        idempotency_key
        and db is not None
        and getattr(store, "_db", None) is db
        and getattr(idempotency_store, "_db", None) is db
    )


def _rollback_or_abandon_idempotency(
    *,
    db: Optional[Session],
    deferred_claim: bool,
    store,
    current_user: User,
    scope: str,
    idempotency_key: Optional[str],
    claim: Optional[IdempotencyClaim],
) -> None:
    if deferred_claim:
        assert db is not None
        db.rollback()
        return
    _abandon_idempotency(
        store=store,
        current_user=current_user,
        scope=scope,
        idempotency_key=idempotency_key,
        claim=claim,
    )


def _abandon_idempotency(
    *,
    store,
    current_user: User,
    scope: str,
    idempotency_key: Optional[str],
    claim: Optional[IdempotencyClaim],
) -> None:
    if not idempotency_key or claim is None:
        return
    store.abandon(
        user_id=getattr(
            current_user, "id", getattr(current_user, "username", "anonymous")
        ),
        scope=scope,
        idempotency_key=idempotency_key,
        record_id=claim.record_id,
    )
