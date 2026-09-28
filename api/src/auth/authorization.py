"""Request-provenance containment shared by tenant and identity authorization."""

from fastapi import HTTPException, status

from src.auth.models import User, UserRole


def has_cross_tenant_admin_access(user: User) -> bool:
    """Preserve bearer ADMIN override; keys never inherit an implicit wildcard."""
    return (
        getattr(user, "auth_method", None) == "bearer"
        and getattr(user, "role", None) == UserRole.ADMIN
    )


def require_bearer_authentication(user: User) -> None:
    """Fail closed before credential or user/company identity mutations."""
    if getattr(user, "auth_method", None) != "bearer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bearer authentication required for this operation",
        )
