"""Admin calculation-contract import and unit-catalog repair endpoints.

Every route requires a bearer ADMIN holding ``manage_system``; API keys and
other roles are refused. Validation errors never echo submitted values.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

import structlog
from src.api.rate_limit import limiter
from src.api.safe_validation_route import SecretSafeValidationRoute
from src.auth.authorization import require_bearer_authentication
from src.auth.dependencies import get_current_active_user, require_permission
from src.auth.models import Permission, User, UserRole
from src.database.session import get_db_optional
from src.services import admin_catalog
from src.services.calculation_contract_import import RETIREMENT_SCOPES

logger = structlog.get_logger(__name__)

router = APIRouter(
    route_class=SecretSafeValidationRoute,
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM))],
)

_CONTRACT_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "description": (
                        "The exact sds_calculation_contract.json package "
                        "(bytes are hashed as received)."
                    ),
                }
            }
        },
    }
}


class RepairPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_id: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    deactivate_unit_id: int = Field(..., ge=1)
    retain_unit_id: int = Field(..., ge=1)
    reason: str = Field(..., min_length=1, max_length=500)


class RepairCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Dict[str, Any]
    plan_digest: str = Field(..., pattern=r"^[0-9a-f]{64}$")


def _admin_actor(current_user: User, request: Request) -> admin_catalog.Actor:
    require_bearer_authentication(current_user)
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Catalog administration requires an admin bearer token",
        )
    return admin_catalog.Actor(
        user_id=current_user.id,
        auth_method=current_user.auth_method or "bearer",
        request_id=getattr(request.state, "request_id", None),
    )


def _require_db(db: Optional[Session]) -> Session:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Catalog administration requires the database",
        )
    return db


def _require_confirm(confirm: bool) -> None:
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set confirm=true to apply this change",
        )


def _raise(exc: admin_catalog.AdminCatalogError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message)


def _refresh_converter(request: Request) -> None:
    """Reload this process's converter now; other processes follow the revision."""
    converter = getattr(request.app.state, "unit_converter", None)
    if converter is None:
        return
    converter.reload_from_storage()
    converter.get_physical_converter()


@router.post(
    "/calculation-contracts/validations",
    summary="Validate a calculation contract package",
    description=(
        "Dry-run validation of an sds_calculation_contract.json package against "
        "the live indicator catalog. Writes no contract rows."
    ),
    openapi_extra=_CONTRACT_BODY,
)
@limiter.limit("20/minute")
async def validate_calculation_contract(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Optional[Session] = Depends(get_db_optional),
) -> Dict[str, Any]:
    actor = _admin_actor(current_user, request)
    db = _require_db(db)
    raw = await request.body()
    try:
        outcome = admin_catalog.validate_contract(db, raw, actor)
    except admin_catalog.AdminCatalogError as exc:
        logger.warning(
            "admin_contract_validation_rejected", actor_user_id=actor.user_id
        )
        _raise(exc)
    logger.info(
        "admin_contract_validated",
        actor_user_id=actor.user_id,
        package_hash=outcome.package_hash,
    )
    return {
        "valid": True,
        "package_hash": outcome.package_hash,
        "contract_sha256": outcome.contract_sha256,
        "counts": outcome.counts,
    }


@router.post(
    "/calculation-contracts/imports",
    summary="Import a calculation contract package",
    description=(
        "Import an sds_calculation_contract.json package in one transaction. "
        "Requires confirm=true; re-importing the same package hash returns "
        "already_imported without new rows."
    ),
    openapi_extra=_CONTRACT_BODY,
)
@limiter.limit("5/minute")
async def import_calculation_contract(
    request: Request,
    confirm: bool = Query(False),
    retirement_scope: str = Query("incoming_keys"),
    current_user: User = Depends(get_current_active_user),
    db: Optional[Session] = Depends(get_db_optional),
) -> Dict[str, Any]:
    actor = _admin_actor(current_user, request)
    _require_confirm(confirm)
    if retirement_scope not in RETIREMENT_SCOPES:
        raise HTTPException(
            status_code=422,
            detail="retirement_scope must be one of: "
            + ", ".join(sorted(RETIREMENT_SCOPES)),
        )
    db = _require_db(db)
    raw = await request.body()
    try:
        outcome = admin_catalog.import_contract(
            db, raw, actor, retirement_scope=retirement_scope
        )
    except admin_catalog.AdminCatalogError as exc:
        logger.warning("admin_contract_import_rejected", actor_user_id=actor.user_id)
        _raise(exc)
    except Exception as exc:
        logger.error(
            "admin_contract_import_failed",
            actor_user_id=actor.user_id,
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Contract import failed")
    logger.info(
        "admin_contract_imported",
        actor_user_id=actor.user_id,
        package_hash=outcome.package_hash,
        status=outcome.status,
    )
    return {
        "status": outcome.status,
        "committed": outcome.committed,
        "package_hash": outcome.package_hash,
        "contract_sha256": outcome.contract_sha256,
        "counts": outcome.counts,
    }


@router.get(
    "/unit-catalog/conflicts",
    summary="List unit catalog conflicts",
    description=(
        "Active-unit conflicts exactly as the physical unit converter detects "
        "them (expression tokens, canonical symbols, aliases)."
    ),
)
@limiter.limit("20/minute")
async def list_unit_catalog_conflicts(
    request: Request,
    limit: int = Query(admin_catalog.MAX_CONFLICTS_PAGE, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_active_user),
    db: Optional[Session] = Depends(get_db_optional),
) -> Dict[str, Any]:
    _admin_actor(current_user, request)
    db = _require_db(db)
    return admin_catalog.list_conflicts(db, limit=limit, offset=offset)


@router.post(
    "/unit-catalog/repairs/preview",
    summary="Preview a unit catalog repair",
    description=(
        "Check every precondition for deactivating one duplicate unit in favour "
        "of another and return a signed plan. Changes nothing."
    ),
)
@limiter.limit("20/minute")
async def preview_unit_catalog_repair(
    request: Request,
    body: RepairPreviewRequest,
    current_user: User = Depends(get_current_active_user),
    db: Optional[Session] = Depends(get_db_optional),
) -> Dict[str, Any]:
    _admin_actor(current_user, request)
    db = _require_db(db)
    try:
        return admin_catalog.preview_repair(
            db,
            conflict_id=body.conflict_id,
            deactivate_unit_id=body.deactivate_unit_id,
            retain_unit_id=body.retain_unit_id,
            reason=body.reason,
        )
    except admin_catalog.AdminCatalogError as exc:
        db.rollback()
        _raise(exc)


@router.post(
    "/unit-catalog/repairs/commit",
    summary="Apply a previewed unit catalog repair",
    description=(
        "Apply an unexpired, unmodified preview plan (confirm=true). Every API "
        "process observes the change within one second."
    ),
)
@limiter.limit("5/minute")
async def commit_unit_catalog_repair(
    request: Request,
    body: RepairCommitRequest,
    confirm: bool = Query(False),
    current_user: User = Depends(get_current_active_user),
    db: Optional[Session] = Depends(get_db_optional),
) -> Dict[str, Any]:
    actor = _admin_actor(current_user, request)
    _require_confirm(confirm)
    db = _require_db(db)
    try:
        result = admin_catalog.commit_repair(
            db, plan=body.plan, digest=body.plan_digest, actor=actor
        )
    except admin_catalog.AdminCatalogError as exc:
        db.rollback()
        _raise(exc)
    try:
        _refresh_converter(request)
    except Exception as exc:
        logger.error(
            "admin_unit_repair_health_gate_failed",
            actor_user_id=actor.user_id,
            repair_id=result["repair_id"],
            error_type=type(exc).__name__,
        )
        admin_catalog.reverse_repair(db, repair_id=result["repair_id"], actor=actor)
        _refresh_converter(request)
        raise HTTPException(
            status_code=500,
            detail="Repair reverted: the unit converter could not be rebuilt",
        )
    logger.info(
        "admin_unit_repair_committed",
        actor_user_id=actor.user_id,
        repair_id=result["repair_id"],
        catalog_revision=result["catalog_revision"],
    )
    return dict(result, propagation_seconds=1)


@router.post(
    "/unit-catalog/repairs/{repair_id}/reverse",
    summary="Reverse a unit catalog repair",
    description=(
        "Restore the exact pre-repair unit row when no later unit-catalog "
        "operation exists (confirm=true)."
    ),
)
@limiter.limit("5/minute")
async def reverse_unit_catalog_repair(
    request: Request,
    repair_id: str = Path(..., pattern=r"^[0-9a-f]{32}$"),
    confirm: bool = Query(False),
    current_user: User = Depends(get_current_active_user),
    db: Optional[Session] = Depends(get_db_optional),
) -> Dict[str, Any]:
    actor = _admin_actor(current_user, request)
    _require_confirm(confirm)
    db = _require_db(db)
    try:
        result = admin_catalog.reverse_repair(db, repair_id=repair_id, actor=actor)
    except admin_catalog.AdminCatalogError as exc:
        db.rollback()
        _raise(exc)
    _refresh_converter(request)
    logger.info(
        "admin_unit_repair_reversed",
        actor_user_id=actor.user_id,
        repair_id=repair_id,
        catalog_revision=result["catalog_revision"],
    )
    return dict(result, propagation_seconds=1)
