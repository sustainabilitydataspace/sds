"""Interoperability readiness endpoints for real SDS runtime flows."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.api.models import RuntimeReadinessResponse
from src.auth.dependencies import get_current_active_user, require_permissions
from src.auth.models import Permission, User
from src.calculation.contracts import RuntimeCalculationContractResolver
from src.calculation.unit_converter import UnitConverter
from src.database.session import get_db_optional
from src.services.concept_service import ConceptService
from src.services.runtime_readiness import build_interoperability_runtime_readiness
from src.services.standard_mapping_store import StandardMappingStore

router = APIRouter()


async def get_unit_converter(request: Request) -> UnitConverter:
    return request.app.state.unit_converter


@router.get(
    "/readiness",
    response_model=RuntimeReadinessResponse,
    summary="Check SDS interoperability runtime readiness",
    description=(
        "Verify whether the real SDS runtime has the catalogues, mappings, "
        "calculation contract, and unit-conversion behavior needed for the "
        "manual operational Swagger checks."
    ),
    dependencies=[
        Depends(
            require_permissions(
                Permission.READ_VALUES,
                Permission.EXECUTE_CALCULATIONS,
                Permission.CONVERT_UNITS,
                Permission.READ_MAPPINGS,
                Permission.QUERY_ONTOLOGY,
            )
        )
    ],
)
async def readiness(
    unit_converter: UnitConverter = Depends(get_unit_converter),
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
) -> RuntimeReadinessResponse:
    """Report readiness without mutating runtime data."""
    concept_service = ConceptService(db=db) if db is not None else None
    try:
        return build_interoperability_runtime_readiness(
            unit_converter=unit_converter,
            mapping_store=StandardMappingStore(db=db) if db is not None else None,
            contract_resolver=(
                RuntimeCalculationContractResolver(db=db) if db is not None else None
            ),
            concept_service=concept_service,
        )
    finally:
        if concept_service is not None:
            concept_service.close()
