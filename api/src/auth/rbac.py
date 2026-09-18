"""
Role-Based Access Control (RBAC) system.
"""

from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import Depends, HTTPException, status

import structlog
from src.auth.dependencies import get_current_active_user
from src.auth.models import ROLE_PERMISSIONS, Permission, User, UserRole

logger = structlog.get_logger(__name__)


class RoleBasedAccessControl:
    """Role-Based Access Control manager."""

    def __init__(self):
        """Initialize RBAC system."""
        self.logger = logger.bind(component="RBAC")

        # Role hierarchy (higher number = more permissions)
        self.role_hierarchy = {
            UserRole.VIEWER: 1,
            UserRole.ANALYST: 2,
            UserRole.DATA_MANAGER: 3,
            UserRole.ADMIN: 4,
        }

        # Permission groups for easier management
        self.permission_groups = {
            "values": [
                Permission.CREATE_VALUES,
                Permission.READ_VALUES,
                Permission.UPDATE_VALUES,
                Permission.DELETE_VALUES,
            ],
            "calculations": [
                Permission.EXECUTE_CALCULATIONS,
                Permission.VIEW_CALCULATION_TRACE,
            ],
            "hierarchies": [Permission.MANAGE_HIERARCHIES, Permission.VIEW_HIERARCHIES],
            "ontology": [
                Permission.QUERY_ONTOLOGY,
                Permission.EXECUTE_SPARQL,
                Permission.MANAGE_ONTOLOGY,
            ],
            "units": [Permission.CONVERT_UNITS, Permission.MANAGE_UNITS],
            "system": [
                Permission.MANAGE_USERS,
                Permission.VIEW_SYSTEM_HEALTH,
                Permission.MANAGE_SYSTEM,
            ],
        }

    def has_role(self, user: User, required_role: UserRole) -> bool:
        """Check if user has required role or higher."""
        user_level = self.role_hierarchy.get(user.role, 0)
        required_level = self.role_hierarchy.get(required_role, 0)
        return user_level >= required_level

    def has_permission(self, user: User, permission: Permission) -> bool:
        """Check if user has specific permission."""
        return permission in user.permissions

    def has_any_permission(self, user: User, permissions: List[Permission]) -> bool:
        """Check if user has any of the specified permissions."""
        return any(permission in user.permissions for permission in permissions)

    def has_all_permissions(self, user: User, permissions: List[Permission]) -> bool:
        """Check if user has all specified permissions."""
        return all(permission in user.permissions for permission in permissions)

    def get_user_permissions(self, role: UserRole) -> List[Permission]:
        """Get all permissions for a role."""
        return ROLE_PERMISSIONS.get(role, [])

    def can_access_company_data(
        self, user: User, company_id: str, allow_admin_override: bool = True
    ) -> bool:
        """Check if user can access data for specific company."""
        # Admin can access all companies (if override allowed)
        if allow_admin_override and user.role == UserRole.ADMIN:
            return True

        # User must belong to the same company
        return user.company_id == company_id

    def get_accessible_companies(self, user: User) -> List[str]:
        """Get list of companies user can access."""
        if user.role == UserRole.ADMIN:
            # Admin can access all companies
            return ["*"]  # Wildcard for all companies
        else:
            # Regular users can only access their own company
            return [user.company_id] if user.company_id else []

    def check_resource_access(
        self,
        user: User,
        resource_type: str,
        action: str,
        resource_company_id: Optional[str] = None,
    ) -> bool:
        """Check if user can perform action on resource type."""

        # Map actions to permissions
        action_permission_map = {
            "values": {
                "create": Permission.CREATE_VALUES,
                "read": Permission.READ_VALUES,
                "update": Permission.UPDATE_VALUES,
                "delete": Permission.DELETE_VALUES,
            },
            "calculations": {
                "execute": Permission.EXECUTE_CALCULATIONS,
                "view_trace": Permission.VIEW_CALCULATION_TRACE,
            },
            "hierarchies": {
                "manage": Permission.MANAGE_HIERARCHIES,
                "view": Permission.VIEW_HIERARCHIES,
            },
            "ontology": {
                "query": Permission.QUERY_ONTOLOGY,
                "sparql": Permission.EXECUTE_SPARQL,
                "manage": Permission.MANAGE_ONTOLOGY,
            },
            "units": {
                "convert": Permission.CONVERT_UNITS,
                "manage": Permission.MANAGE_UNITS,
            },
            "system": {
                "manage_users": Permission.MANAGE_USERS,
                "view_health": Permission.VIEW_SYSTEM_HEALTH,
                "manage": Permission.MANAGE_SYSTEM,
            },
        }

        # Check permission
        resource_actions = action_permission_map.get(resource_type, {})
        required_permission = resource_actions.get(action)

        if not required_permission:
            self.logger.warning(
                "Unknown resource action", resource_type=resource_type, action=action
            )
            return False

        if not self.has_permission(user, required_permission):
            return False

        # Check company access if resource has company association
        if resource_company_id:
            return self.can_access_company_data(user, resource_company_id)

        return True

    def filter_by_company_access(
        self, user: User, items: List[Dict[str, Any]], company_field: str = "company_id"
    ) -> List[Dict[str, Any]]:
        """Filter items based on user's company access."""
        if user.role == UserRole.ADMIN:
            # Admin can see all items
            return items

        # Filter items for user's company only
        user_company = user.company_id
        if not user_company:
            return []

        return [item for item in items if item.get(company_field) == user_company]

    def log_access_attempt(
        self,
        user: User,
        resource: str,
        action: str,
        success: bool,
        additional_info: Optional[Dict[str, Any]] = None,
    ):
        """Log access attempt for audit purposes."""

        log_data = {
            "user_id": user.id,
            "username": user.username,
            "role": user.role.value,
            "company_id": user.company_id,
            "resource": resource,
            "action": action,
            "success": success,
        }

        if additional_info:
            log_data.update(additional_info)

        if success:
            self.logger.info("Access granted", **log_data)
        else:
            self.logger.warning("Access denied", **log_data)


# Global RBAC instance
rbac = RoleBasedAccessControl()


# Decorator functions for easy use
def require_role(required_role: UserRole):
    """Decorator to require specific role."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract user from dependencies
            current_user = None
            for arg in args:
                if isinstance(arg, User):
                    current_user = arg
                    break

            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                )

            if not rbac.has_role(current_user, required_role):
                rbac.log_access_attempt(
                    current_user,
                    func.__name__,
                    "role_check",
                    False,
                    {"required_role": required_role.value},
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Insufficient permissions. Required role: {required_role.value}",
                )

            rbac.log_access_attempt(current_user, func.__name__, "role_check", True)
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def require_permission(required_permission: Permission):
    """Decorator to require specific permission."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract user from dependencies
            current_user = None
            for arg in args:
                if isinstance(arg, User):
                    current_user = arg
                    break

            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                )

            if not rbac.has_permission(current_user, required_permission):
                rbac.log_access_attempt(
                    current_user,
                    func.__name__,
                    "permission_check",
                    False,
                    {"required_permission": required_permission.value},
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Insufficient permissions. Required: {required_permission.value}",
                )

            rbac.log_access_attempt(
                current_user, func.__name__, "permission_check", True
            )
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def require_company_access(
    company_id_param: str = "company_id", allow_admin_override: bool = True
):
    """Decorator to require company access."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract user and company_id
            current_user = None
            company_id = kwargs.get(company_id_param)

            for arg in args:
                if isinstance(arg, User):
                    current_user = arg
                    break

            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                )

            if not company_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Company ID required",
                )

            if not rbac.can_access_company_data(
                current_user, company_id, allow_admin_override
            ):
                rbac.log_access_attempt(
                    current_user,
                    func.__name__,
                    "company_access",
                    False,
                    {"requested_company": company_id},
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: insufficient company permissions",
                )

            rbac.log_access_attempt(current_user, func.__name__, "company_access", True)
            return await func(*args, **kwargs)

        return wrapper

    return decorator


# FastAPI dependency factories
def create_role_dependency(required_role: UserRole):
    """Create FastAPI dependency for role requirement."""

    async def role_dependency(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        if not rbac.has_role(current_user, required_role):
            rbac.log_access_attempt(
                current_user,
                "role_dependency",
                "check",
                False,
                {"required_role": required_role.value},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role: {required_role.value}",
            )
        return current_user

    return role_dependency


def create_permission_dependency(required_permission: Permission):
    """Create FastAPI dependency for permission requirement."""

    async def permission_dependency(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        if not rbac.has_permission(current_user, required_permission):
            rbac.log_access_attempt(
                current_user,
                "permission_dependency",
                "check",
                False,
                {"required_permission": required_permission.value},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {required_permission.value}",
            )
        return current_user

    return permission_dependency
