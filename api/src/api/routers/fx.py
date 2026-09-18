"""API router for FX catalog, coverage, conversion preview, and imports."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

import structlog
from src.api.models import (
    FXConversionRequest,
    FXConversionResponse,
    FXCurrencyResponse,
    FXPolicyResponse,
    FXRateCoverageResponse,
    FXRateImportRequest,
    FXRateImportResponse,
    _normalize_fx_currency_code,
)
from src.auth.dependencies import get_current_active_user, require_permission
from src.auth.models import Permission, User
from src.calculation.conversion.fx import FXAmbiguousRateError, FXMissingRateError
from src.database.models import Currency, FXPolicy
from src.database.repositories.fx_repository import FXRepository
from src.database.session import get_db
from src.services.fx_service import FXService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/fx")


def _field(row: Any, field_name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(field_name)
    return getattr(row, field_name, None)


def _rate_row_payload(row: Any) -> FXRateCoverageResponse:
    return FXRateCoverageResponse(
        id=_field(row, "id"),
        base_currency=_field(row, "base_currency"),
        quote_currency=_field(row, "quote_currency"),
        rate_date=_field(row, "rate_date"),
        rate_value=_field(row, "rate_value"),
        provider=_field(row, "provider"),
        rate_type=_field(row, "rate_type"),
        source_hash=_field(row, "source_hash"),
    )


def _batch_id(batch: Any) -> Any:
    return batch.get("id") if isinstance(batch, Mapping) else getattr(batch, "id", None)


@router.get(
    "/currencies",
    response_model=list[FXCurrencyResponse],
    summary="List active currencies",
    dependencies=[Depends(require_permission(Permission.CONVERT_UNITS))],
)
async def list_currencies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[FXCurrencyResponse]:
    """List active currencies from the database catalog."""
    rows = (
        db.query(Currency)
        .filter(Currency.is_active.is_(True))
        .order_by(Currency.code)
        .all()
    )
    return [
        FXCurrencyResponse(
            code=row.code,
            numeric_code=row.numeric_code,
            name=row.name,
            minor_units=row.minor_units,
            valid_from=row.valid_from,
            valid_to=row.valid_to,
        )
        for row in rows
    ]


@router.get(
    "/policies",
    response_model=list[FXPolicyResponse],
    summary="List active FX policies",
    dependencies=[Depends(require_permission(Permission.CONVERT_UNITS))],
)
async def list_policies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[FXPolicyResponse]:
    """List active FX conversion policies."""
    rows = (
        db.query(FXPolicy)
        .filter(FXPolicy.is_active.is_(True))
        .order_by(FXPolicy.id)
        .all()
    )
    return [
        FXPolicyResponse(
            id=row.id,
            name=row.name,
            provider=row.provider,
            rate_type=row.rate_type,
            selection_mode=row.selection_mode,
            business_day_rule=row.business_day_rule,
            triangulation_allowed=bool(row.triangulation_allowed),
            fallback_behavior=row.fallback_behavior,
            rounding_scale=row.rounding_scale,
            rounding_mode=row.rounding_mode,
        )
        for row in rows
    ]


@router.get(
    "/rates/coverage",
    response_model=list[FXRateCoverageResponse],
    summary="Check FX rate coverage",
    dependencies=[Depends(require_permission(Permission.CONVERT_UNITS))],
)
async def rate_coverage(
    provider: str,
    rate_type: str,
    base_currency: str,
    quote_currency: str,
    start: date,
    end: date,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[FXRateCoverageResponse]:
    """Return FX observations matching the requested coverage window."""
    try:
        base_currency = _normalize_fx_currency_code(base_currency)
        quote_currency = _normalize_fx_currency_code(quote_currency)
        service = FXService(FXRepository(db))
        rows = service.coverage(
            provider=provider,
            rate_type=rate_type,
            base_currency=base_currency,
            quote_currency=quote_currency,
            start=start,
            end=end,
        )
        return [_rate_row_payload(row) for row in rows]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("FX coverage lookup failed", error=str(exc))
        raise HTTPException(
            status_code=500, detail="FX coverage lookup failed"
        ) from exc


@router.post(
    "/convert",
    response_model=FXConversionResponse,
    summary="Preview FX conversion",
    dependencies=[Depends(require_permission(Permission.CONVERT_UNITS))],
)
async def convert_fx(
    request: FXConversionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> FXConversionResponse:
    """Convert a value using an explicit FX policy and return its trace."""
    try:
        result = FXService(FXRepository(db)).convert(
            request.value,
            from_currency=request.from_currency,
            to_currency=request.to_currency,
            value_date=request.value_date,
            period_start=request.period_start,
            period_end=request.period_end,
            policy_id=request.fx_policy_id,
        )
    except (FXMissingRateError, FXAmbiguousRateError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail=f"FX conversion failed: {exc}"
        ) from exc
    except Exception as exc:
        logger.error("Unexpected FX conversion error", error=str(exc))
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return FXConversionResponse(
        original_value=request.value,
        converted_value=result.value,
        from_currency=request.from_currency,
        to_currency=result.currency,
        trace=result.trace,
    )


@router.post(
    "/rates/import",
    response_model=FXRateImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import controlled FX rate rows",
    dependencies=[Depends(require_permission(Permission.MANAGE_FX_RATES))],
)
async def import_rates(
    request: FXRateImportRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> FXRateImportResponse:
    """Import validated CSV-like FX rate rows."""
    rows = [row.model_dump() for row in request.rows]
    try:
        result = FXService(FXRepository(db)).import_rates_csv_result(
            rows,
            provider=request.provider,
            rate_type=request.rate_type,
            base_currency=request.base_currency,
            source_hash=request.source_hash,
            created_by=getattr(current_user, "username", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("FX rate import failed", error=str(exc))
        raise HTTPException(status_code=500, detail="FX rate import failed") from exc

    if result.batch is None:
        response.status_code = status.HTTP_200_OK

    return FXRateImportResponse(
        batch_id=_batch_id(result.batch),
        imported_rows=int(getattr(result, "submitted_rows", len(request.rows))),
        created_rows=int(getattr(result, "created_rows", 0)),
        updated_rows=int(getattr(result, "updated_rows", 0)),
        unchanged_rows=int(getattr(result, "unchanged_rows", 0)),
        provider=request.provider,
        rate_type=request.rate_type,
        base_currency=request.base_currency,
    )
