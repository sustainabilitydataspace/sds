"""
API router for hierarchy management endpoints.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

import structlog
from src.api.models import (
    HierarchyConfiguration,
    HierarchyLevel,
    PaginatedResponse,
    SuccessResponse,
)
from src.auth.dependencies import get_current_active_user, require_permission
from src.auth.models import Permission, User, UserRole
from src.services.hierarchy_store import get_hierarchy_store

logger = structlog.get_logger(__name__)

router = APIRouter()


def _is_admin_user(current_user: User) -> bool:
    role = getattr(current_user, "role", None)
    role_value = getattr(role, "value", role)
    return role_value == UserRole.ADMIN.value


def _authorized_company_id(
    company_id: Optional[str],
    current_user: User,
    *,
    required: bool = False,
) -> Optional[str]:
    """Resolve the company a hierarchy operation may touch (tenant scoping).

    Admins may target any company_id; non-admins are forced to their own
    company_id and may not specify a different one. Mirrors the values-revision
    tenant handling so the hierarchy API can no longer cross the tenant boundary
    (codex F05 M1).
    """
    if _is_admin_user(current_user):
        if required and not company_id:
            raise HTTPException(status_code=400, detail="company_id is required")
        return company_id
    user_company = getattr(current_user, "company_id", None)
    if not user_company:
        raise HTTPException(
            status_code=403,
            detail="Access denied: user is not assigned to a company",
        )
    if company_id is not None and company_id != user_company:
        raise HTTPException(
            status_code=403,
            detail="Access denied: company does not match authenticated user",
        )
    return company_id or user_company


def _assert_config_company(config, current_user: User) -> None:
    """404 a config outside the caller's authorized company (no existence leak)."""
    if _is_admin_user(current_user):
        return
    user_company = getattr(current_user, "company_id", None)
    if not user_company or getattr(config, "company_id", None) != user_company:
        raise HTTPException(status_code=404, detail="Hierarchy not found")


@router.post(
    "",
    response_model=HierarchyConfiguration,
    status_code=201,
    summary="Create hierarchy configuration",
    description="Create a new organizational hierarchy configuration",
    dependencies=[Depends(require_permission(Permission.MANAGE_HIERARCHIES))],
)
async def create_hierarchy(
    config: HierarchyConfiguration,
    store=Depends(get_hierarchy_store),
    current_user: User = Depends(get_current_active_user),
) -> HierarchyConfiguration:
    """
    Create a new organizational hierarchy configuration.

    - **company_id**: Unique company identifier
    - **hierarchy_type**: Type of hierarchy (organizational, temporal, geographical)
    - **name**: Configuration name
    - **levels**: List of hierarchy levels with parent-child relationships
    - **active**: Whether the configuration is active
    """
    try:
        logger.info(
            "Creating hierarchy configuration",
            company_id=config.company_id,
            hierarchy_type=config.hierarchy_type,
            name=config.name,
            levels_count=len(config.levels),
        )

        # Validate the body first (reject malformed input regardless of tenant).
        _validate_hierarchy_structure(config.levels)

        # Tenant scoping: force/validate the target company (codex F05 M1).
        config.company_id = _authorized_company_id(
            config.company_id, current_user, required=True
        )

        config_id = str(uuid.uuid4())
        created = store.create(
            config,
            config_id=config_id,
            created_by=getattr(current_user, "username", None),
        )

        logger.info(
            "Hierarchy configuration created successfully",
            config_id=config_id,
            company_id=config.company_id,
        )

        return created

    except ValueError as e:
        logger.warning(
            "Invalid hierarchy configuration",
            error=str(e),
            company_id=config.company_id,
        )
        raise HTTPException(
            status_code=400, detail=f"Invalid hierarchy configuration: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to create hierarchy configuration",
            error=str(e),
            company_id=config.company_id,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "",
    response_model=PaginatedResponse,
    summary="List hierarchy configurations",
    description="Get list of hierarchy configurations with optional filtering",
    dependencies=[Depends(require_permission(Permission.VIEW_HIERARCHIES))],
)
async def list_hierarchies(
    company_id: Optional[str] = Query(None, description="Filter by company ID"),
    hierarchy_type: Optional[str] = Query(None, description="Filter by hierarchy type"),
    active: Optional[bool] = Query(None, description="Filter by active status"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    store=Depends(get_hierarchy_store),
    current_user: User = Depends(get_current_active_user),
) -> PaginatedResponse:
    """
    List hierarchy configurations with optional filtering.

    - **company_id**: Filter by company ID
    - **hierarchy_type**: Filter by hierarchy type
    - **active**: Filter by active status
    - **limit**: Maximum results (1-1000)
    - **offset**: Results to skip for pagination
    """
    try:
        logger.info(
            "Listing hierarchy configurations",
            company_id=company_id,
            hierarchy_type=hierarchy_type,
            active=active,
            limit=limit,
            offset=offset,
        )

        # Tenant scoping: non-admins are confined to their own company (codex F05 M1).
        scoped_company_id = _authorized_company_id(company_id, current_user)

        paginated_configs, total = store.list(
            company_id=scoped_company_id,
            hierarchy_type=hierarchy_type,
            active=active,
            limit=limit,
            offset=offset,
        )

        response = PaginatedResponse(
            items=paginated_configs,
            total=total,
            page=(offset // limit) + 1,
            size=len(paginated_configs),
            pages=(total + limit - 1) // limit,
        )

        logger.info(
            "Hierarchy configurations listed successfully",
            total=total,
            returned=len(paginated_configs),
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list hierarchy configurations", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/{hierarchy_id}",
    response_model=HierarchyConfiguration,
    summary="Get hierarchy configuration",
    description="Get a specific hierarchy configuration by ID",
    dependencies=[Depends(require_permission(Permission.VIEW_HIERARCHIES))],
)
async def get_hierarchy(
    hierarchy_id: str = Path(..., description="Hierarchy ID"),
    store=Depends(get_hierarchy_store),
    current_user: User = Depends(get_current_active_user),
) -> HierarchyConfiguration:
    """
    Get a specific hierarchy configuration by ID.

    - **hierarchy_id**: Unique hierarchy identifier
    """
    try:
        logger.info("Getting hierarchy configuration", hierarchy_id=hierarchy_id)

        config = store.get(hierarchy_id)
        if not config:
            raise HTTPException(
                status_code=404, detail=f"Hierarchy not found: {hierarchy_id}"
            )
        # Tenant scoping: do not reveal another company's config (codex F05 M1).
        _assert_config_company(config, current_user)

        return config

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get hierarchy configuration",
            error=str(e),
            hierarchy_id=hierarchy_id,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put(
    "/{hierarchy_id}",
    response_model=HierarchyConfiguration,
    summary="Update hierarchy configuration",
    description="Update an existing hierarchy configuration",
    dependencies=[Depends(require_permission(Permission.MANAGE_HIERARCHIES))],
)
async def update_hierarchy(
    hierarchy_id: str = Path(..., description="Hierarchy ID"),
    config: HierarchyConfiguration = ...,
    store=Depends(get_hierarchy_store),
    current_user: User = Depends(get_current_active_user),
) -> HierarchyConfiguration:
    """
    Update an existing hierarchy configuration.

    - **hierarchy_id**: Hierarchy ID to update
    - **config**: Updated configuration data
    """
    try:
        logger.info(
            "Updating hierarchy configuration",
            hierarchy_id=hierarchy_id,
            company_id=config.company_id,
        )

        # Validate the body first (reject malformed input regardless of target).
        _validate_hierarchy_structure(config.levels)

        # Tenant scoping: the target config must belong to the caller's company,
        # and the update may not move it to another company (codex F05 M1).
        existing = store.get(hierarchy_id)
        if not existing:
            raise HTTPException(
                status_code=404, detail=f"Hierarchy not found: {hierarchy_id}"
            )
        _assert_config_company(existing, current_user)
        config.company_id = _authorized_company_id(
            config.company_id, current_user, required=True
        )
        if config.company_id != existing.company_id:
            raise HTTPException(
                status_code=403,
                detail="Access denied: cannot move a hierarchy to another company",
            )

        updated = store.update(hierarchy_id, config)
        if not updated:
            raise HTTPException(
                status_code=404, detail=f"Hierarchy not found: {hierarchy_id}"
            )

        logger.info(
            "Hierarchy configuration updated successfully", hierarchy_id=hierarchy_id
        )
        return updated

    except ValueError as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid hierarchy configuration: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to update hierarchy configuration",
            error=str(e),
            hierarchy_id=hierarchy_id,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete(
    "/{hierarchy_id}",
    response_model=SuccessResponse,
    summary="Delete hierarchy configuration",
    description="Delete a hierarchy configuration",
    dependencies=[Depends(require_permission(Permission.MANAGE_HIERARCHIES))],
)
async def delete_hierarchy(
    hierarchy_id: str = Path(..., description="Hierarchy ID"),
    store=Depends(get_hierarchy_store),
    current_user: User = Depends(get_current_active_user),
) -> SuccessResponse:
    """
    Delete a hierarchy configuration.

    - **hierarchy_id**: Hierarchy ID to delete
    """
    try:
        logger.info("Deleting hierarchy configuration", hierarchy_id=hierarchy_id)

        # Tenant scoping: only delete a config in the caller's company (codex F05 M1).
        existing = store.get(hierarchy_id)
        if not existing:
            raise HTTPException(
                status_code=404, detail=f"Hierarchy not found: {hierarchy_id}"
            )
        _assert_config_company(existing, current_user)

        deleted = store.delete(hierarchy_id)
        if not deleted:
            raise HTTPException(
                status_code=404, detail=f"Hierarchy not found: {hierarchy_id}"
            )

        logger.info(
            "Hierarchy configuration deleted successfully", hierarchy_id=hierarchy_id
        )

        return SuccessResponse(
            success=True, message=f"Hierarchy {hierarchy_id} deleted successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to delete hierarchy configuration",
            error=str(e),
            hierarchy_id=hierarchy_id,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{hierarchy_id}/activate",
    response_model=SuccessResponse,
    summary="Activate hierarchy configuration",
    description="Activate a hierarchy configuration for a company",
    dependencies=[Depends(require_permission(Permission.MANAGE_HIERARCHIES))],
)
async def activate_hierarchy(
    hierarchy_id: str = Path(..., description="Hierarchy ID"),
    store=Depends(get_hierarchy_store),
    current_user: User = Depends(get_current_active_user),
) -> SuccessResponse:
    """
    Activate a hierarchy configuration.

    - **hierarchy_id**: Hierarchy ID to activate
    """
    try:
        logger.info("Activating hierarchy configuration", hierarchy_id=hierarchy_id)

        # Tenant scoping: only activate a config in the caller's company (codex F05 M1).
        existing = store.get(hierarchy_id)
        if not existing:
            raise HTTPException(
                status_code=404, detail=f"Hierarchy not found: {hierarchy_id}"
            )
        _assert_config_company(existing, current_user)

        activated = store.activate(hierarchy_id)
        if not activated:
            raise HTTPException(
                status_code=404, detail=f"Hierarchy not found: {hierarchy_id}"
            )

        logger.info(
            "Hierarchy configuration activated successfully", hierarchy_id=hierarchy_id
        )

        return SuccessResponse(
            success=True, message=f"Hierarchy {hierarchy_id} activated successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to activate hierarchy configuration",
            error=str(e),
            hierarchy_id=hierarchy_id,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


def _validate_hierarchy_structure(levels: List[HierarchyLevel]):
    """Validate hierarchy structure for consistency."""
    if not levels:
        raise ValueError("At least one level is required")

    # Check for duplicate IDs
    ids = [level.id for level in levels]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate level IDs found")

    # Check for valid parent references
    level_ids = set(ids)
    for level in levels:
        if level.parent and level.parent not in level_ids:
            raise ValueError(
                f"Invalid parent reference: {level.parent} for level {level.id}"
            )

    # Check for cycles
    def has_cycle(level_id: str, visited: set, rec_stack: set) -> bool:
        visited.add(level_id)
        rec_stack.add(level_id)

        # Find level
        level = next((l for l in levels if l.id == level_id), None)
        if level and level.parent:
            if level.parent in rec_stack:
                return True
            if level.parent not in visited:
                if has_cycle(level.parent, visited, rec_stack):
                    return True

        rec_stack.remove(level_id)
        return False

    visited = set()
    for level in levels:
        if level.id not in visited:
            if has_cycle(level.id, visited, set()):
                raise ValueError("Circular dependency detected in hierarchy")

    # Validate level numbers are consistent
    level_map = {level.id: level for level in levels}
    for level in levels:
        if level.parent:
            parent = level_map.get(level.parent)
            if parent and parent.level >= level.level:
                raise ValueError(
                    f"Invalid level hierarchy: parent {level.parent} must have lower level than child {level.id}"
                )
