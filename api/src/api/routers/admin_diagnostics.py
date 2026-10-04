"""Admin read access to sanitized server-error diagnostics.

Bearer ADMIN with ``manage_system`` only. Records hold exception types, frame
locations, a fixed classification and allow-listed database fields; never
messages, request data, SQL or parameters.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy.orm import Session

import structlog
from src.api.rate_limit import limiter
from src.api.safe_validation_route import SecretSafeValidationRoute
from src.auth.authorization import require_bearer_authentication
from src.auth.dependencies import get_current_active_user, require_permission
from src.auth.models import Permission, User, UserRole
from src.database.session import get_db_optional
from src.services import error_diagnostics

logger = structlog.get_logger(__name__)

router = APIRouter(
    route_class=SecretSafeValidationRoute,
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM))],
)

_NOT_FOUND = "diagnostic not found"


def _admin(current_user: User) -> None:
    require_bearer_authentication(current_user)
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diagnostics require an admin bearer token",
        )


def _require_db(db: Optional[Session]) -> Session:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Diagnostics require the database",
        )
    return db


@router.get(
    "/diagnostics/errors/{request_id}",
    summary="Get a sanitized server error diagnostic",
    description=(
        "Return the sanitized diagnostic recorded for the request id of a "
        "server error (exception types, in-repository frames, classification, "
        "allow-listed database fields). Records expire after 14 days."
    ),
)
@limiter.limit("20/minute")
async def get_error_diagnostic(
    request: Request,
    request_id: str = Path(..., min_length=1, max_length=100),
    current_user: User = Depends(get_current_active_user),
    db: Optional[Session] = Depends(get_db_optional),
) -> Dict[str, Any]:
    _admin(current_user)
    db = _require_db(db)
    record = error_diagnostics.get_error_diagnostic(db, request_id)
    logger.info(
        "admin_diagnostics_read",
        actor_user_id=current_user.id,
        diagnostic_request_id=request_id if record is not None else None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return record


@router.get(
    "/diagnostics/errors",
    summary="List recent sanitized server error diagnostics",
    description="Newest first; at most 50 records from the last 14 days.",
)
@limiter.limit("20/minute")
async def list_error_diagnostics(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(get_current_active_user),
    db: Optional[Session] = Depends(get_db_optional),
) -> Dict[str, Any]:
    _admin(current_user)
    db = _require_db(db)
    items = error_diagnostics.list_error_diagnostics(db, limit=limit)
    logger.info(
        "admin_diagnostics_read", actor_user_id=current_user.id, count=len(items)
    )
    return {"items": items}
