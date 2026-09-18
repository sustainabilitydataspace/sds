"""E6 Policy Enforcer for FastAPI dependency injection."""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import Depends, HTTPException, status

import structlog
from src.auth.dependencies import get_current_active_user
from src.auth.models import Permission, User
from src.policies.policy_service import PolicyService, get_policy_service

logger = structlog.get_logger(__name__)


class PolicyEnforcer:
    """Enforces ODRL policies for resource access."""

    def __init__(self, policy_service: PolicyService):
        self.policy_service = policy_service

    def check_access(
        self,
        resource: str,
        action: str,
        user: User,
    ) -> bool:
        """Check if user has access to perform action on resource."""
        return self.policy_service.evaluate_access(
            resource=resource,
            action=action,
            user_role=user.role,
            user_permissions=user.permissions,
        )

    def enforce(
        self,
        resource: str,
        action: str,
        user: User,
    ) -> None:
        """Enforce policy, raising HTTPException if access denied."""
        if not self.check_access(resource, action, user):
            required_perm = self.policy_service.get_required_permission_for_action(
                resource, action
            )
            logger.warning(
                "Policy enforcement: access denied",
                user_id=user.id,
                username=user.username,
                role=user.role.value,
                resource=resource,
                action=action,
                required_permission=required_perm.value if required_perm else None,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: insufficient permissions for {action} on {resource}",
            )

        logger.debug(
            "Policy enforcement: access granted",
            user_id=user.id,
            resource=resource,
            action=action,
        )


def get_policy_enforcer(
    policy_service: PolicyService = Depends(get_policy_service),
) -> PolicyEnforcer:
    """Dependency to get PolicyEnforcer instance."""
    return PolicyEnforcer(policy_service)


def require_policy(resource: str, action: str) -> Callable:
    """
    Dependency factory for policy enforcement.

    Usage:
        @router.get("/indicators")
        async def list_indicators(
            user: User = Depends(require_policy("indicators", "read"))
        ):
            ...
    """

    async def policy_dependency(
        user: User = Depends(get_current_active_user),
        enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    ) -> User:
        enforcer.enforce(resource, action, user)
        return user

    return policy_dependency


def require_indicator_read() -> Callable:
    """Convenience dependency for indicator read access."""
    return require_policy("indicators", "read")


def require_indicator_write() -> Callable:
    """Convenience dependency for indicator write access."""
    return require_policy("indicators", "write")


def require_crosswalk_read() -> Callable:
    """Convenience dependency for crosswalk read access."""
    return require_policy("crosswalks", "read")


def require_crosswalk_write() -> Callable:
    """Convenience dependency for crosswalk write access."""
    return require_policy("crosswalks", "write")


def require_mapping_read() -> Callable:
    """Convenience dependency for mappings read access."""
    return require_policy("mappings", "read")


def require_mapping_write() -> Callable:
    """Convenience dependency for mappings write access."""
    return require_policy("mappings", "write")
