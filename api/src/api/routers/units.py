"""
API router for unit conversion endpoints.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

import structlog
from src.api.models import UnitConversionRequest, UnitConversionResponse, UnitInfo
from src.auth.dependencies import get_current_active_user, require_permission
from src.auth.models import Permission, User
from src.calculation.unit_converter import UnitConversionError, UnitConverter

logger = structlog.get_logger(__name__)

router = APIRouter()


async def get_unit_converter(request: Request) -> UnitConverter:
    """Dependency to get unit converter from app state."""
    has_converter = hasattr(request.app.state, "unit_converter")
    logger.info("Resolving unit converter dependency", has_unit_converter=has_converter)
    if not has_converter:
        raise HTTPException(status_code=503, detail="Unit converter not initialized")
    return request.app.state.unit_converter


@router.post(
    "/convert",
    response_model=UnitConversionResponse,
    summary="Convert between units",
    description="Convert a value from one unit to another compatible unit",
    dependencies=[Depends(require_permission(Permission.CONVERT_UNITS))],
)
async def convert_units(
    request: UnitConversionRequest,
    unit_converter: UnitConverter = Depends(get_unit_converter),
    current_user: User = Depends(get_current_active_user),
) -> UnitConversionResponse:
    """
    Convert between compatible units.

    - **value**: Numeric value to convert
    - **from_unit**: Source unit (e.g., 'kg', 'L', 'kWh')
    - **to_unit**: Target unit (e.g., 't', 'm³', 'MJ')
    """
    try:
        logger.info(
            "Converting units",
            value=float(request.value),
            from_unit=request.from_unit,
            to_unit=request.to_unit,
        )

        # Perform conversion using UnitConverter
        result = unit_converter.convert(
            value=request.value, from_unit=request.from_unit, to_unit=request.to_unit
        )

        # Create response
        response = UnitConversionResponse(
            original_value=result.original_value,
            original_unit=result.original_unit,
            converted_value=result.converted_value,
            converted_unit=result.converted_unit,
            conversion_factor=result.conversion_factor,
            formula_used=result.formula_used,
        )

        logger.info(
            "Unit conversion completed",
            original_value=float(result.original_value),
            converted_value=float(result.converted_value),
            conversion_factor=(
                None
                if result.conversion_factor is None
                else float(result.conversion_factor)
            ),
        )

        return response

    except UnitConversionError as e:
        logger.warning(
            "Unit conversion failed",
            error=str(e),
            from_unit=request.from_unit,
            to_unit=request.to_unit,
        )
        raise HTTPException(status_code=400, detail=f"Unit conversion failed: {str(e)}")
    except Exception as e:
        logger.error("Unexpected error during unit conversion", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/units",
    response_model=List[UnitInfo],
    summary="List supported units",
    description="Get list of all supported units, optionally filtered by category",
    dependencies=[Depends(require_permission(Permission.CONVERT_UNITS))],
)
async def list_units(
    category: Optional[str] = None,
    unit_converter: UnitConverter = Depends(get_unit_converter),
    current_user: User = Depends(get_current_active_user),
) -> List[UnitInfo]:
    """
    List supported units.

    - **category**: Optional category filter (mass, energy, volume, etc.)
    """
    try:
        logger.info("Listing supported units", category=category)

        # Get units from converter using the correct method
        from src.calculation.unit_converter import UnitCategory

        # Convert category string to enum if provided
        category_enum = None
        if category:
            try:
                category_enum = UnitCategory(category)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail=f"Invalid category: {category}"
                )

        # Get units from converter
        units_data = unit_converter.get_supported_units(category=category_enum)

        # Convert to response format
        units = [
            UnitInfo(
                symbol=unit_data["symbol"],
                name=unit_data["name"],
                category=unit_data["category"],
                base_unit=unit_data.get("base_unit", ""),
                aliases=unit_data.get("aliases", []),
            )
            for unit_data in units_data
        ]

        logger.info("Units listed successfully", count=len(units), category=category)

        return units

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list units", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/units/validate",
    summary="Validate unit compatibility",
    description="Check if two units are compatible for conversion",
    dependencies=[Depends(require_permission(Permission.CONVERT_UNITS))],
)
async def validate_units(
    from_unit: str,
    to_unit: str,
    unit_converter: UnitConverter = Depends(get_unit_converter),
    current_user: User = Depends(get_current_active_user),
):
    """
    Validate unit compatibility.

    - **from_unit**: Source unit
    - **to_unit**: Target unit
    """
    try:
        logger.info(
            "Validating unit compatibility", from_unit=from_unit, to_unit=to_unit
        )

        # Normalize first so unknown-unit validation matches /units/convert.
        unit_converter.normalize_unit_symbol(from_unit)
        unit_converter.normalize_unit_symbol(to_unit)

        conversion_path = unit_converter.get_conversion_path(from_unit, to_unit)
        compatible = bool(conversion_path) and unit_converter.are_units_compatible(
            from_unit, to_unit
        )
        if not compatible:
            conversion_path = []

        response = {
            "from_unit": from_unit,
            "to_unit": to_unit,
            "compatible": compatible,
            "conversion_path": conversion_path,
        }

        logger.info(
            "Unit compatibility validated",
            compatible=compatible,
            conversion_path=conversion_path,
        )

        return response

    except UnitConversionError as e:
        logger.warning("Unit compatibility validation failed", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to validate unit compatibility", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")
