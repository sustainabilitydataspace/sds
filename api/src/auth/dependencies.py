"""
FastAPI dependencies for authentication and authorization.
"""

from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

import structlog
from src.auth.jwt_handler import jwt_handler
from src.auth.models import Permission, TokenData, User, UserRole
from src.config.settings import settings
from src.services.api_key_store import get_api_key_store
from src.services.user_store import get_user_store

logger = structlog.get_logger(__name__)

# Security scheme
_BEARER_DESCRIPTION = (
    "Paste only the access_token returned by POST /auth/login. "
    "Do not paste the refresh_token here, and do not add a Bearer prefix manually. "
    "Use refresh_token only in the POST /auth/refresh request body."
)
security = HTTPBearer(description=_BEARER_DESCRIPTION)
security_optional = HTTPBearer(description=_BEARER_DESCRIPTION, auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_token_data(
    request: Request = None,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
    api_key: Optional[str] = Depends(api_key_header),
    api_keys=Depends(get_api_key_store),
    users=Depends(get_user_store),
) -> TokenData:
    """Extract and validate token data from request."""

    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    missing_credentials = HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authenticated",
    )

    try:
        if credentials is not None:
            token = credentials.credentials
            token_data = jwt_handler.verify_token(token)

            if token_data is None:
                logger.warning("Invalid token provided")
                raise invalid_credentials

            # Check persistent revocation when DB is available
            if getattr(settings, "require_database", False):
                try:
                    from src.database.session import SessionLocal

                    db = SessionLocal()
                    try:
                        if jwt_handler.is_revoked_persistent(token, db):
                            raise invalid_credentials
                    finally:
                        db.close()
                except HTTPException:
                    raise
                except Exception as e:
                    logger.error(
                        "Persistent token revocation check failed", error=str(e)
                    )
                    raise invalid_credentials

            if settings.require_database:
                user = users.get_user_by_id(user_id=token_data.user_id)
                if user is None or not user.is_active:
                    raise invalid_credentials
                return TokenData(
                    user_id=user.id,
                    username=user.username,
                    role=user.role,
                    company_id=user.company_id,
                    permissions=user.permissions,
                    auth_method="bearer",
                    exp=token_data.exp,
                    iat=token_data.iat,
                )

            return token_data

        if api_key:
            resolved = api_keys.authenticate(api_key)
            if resolved is None:
                logger.warning("Invalid API key provided")
                raise invalid_credentials

            user = users.get_user_by_id(user_id=resolved.user_id)
            if user is None or not user.is_active:
                raise invalid_credentials

            effective_permissions = [
                permission
                for permission in resolved.permissions
                if permission in user.permissions
            ]
            return TokenData(
                user_id=user.id,
                username=user.username,
                role=user.role,
                company_id=user.company_id,
                permissions=effective_permissions,
                auth_method="api_key",
                exp=None,
                iat=None,
            )

        raise missing_credentials

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error validating token", error=str(e))
        raise invalid_credentials


async def get_current_user(
    token_data: TokenData = Depends(get_token_data),
    users=Depends(get_user_store),
) -> User:
    """Get current user from token data."""
    if getattr(settings, "require_database", False):
        db_user = users.get_user_by_id(user_id=token_data.user_id)
        if db_user is None or not db_user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if getattr(token_data, "auth_method", "bearer") == "api_key":
            return db_user.model_copy(
                update={"permissions": list(token_data.permissions)}
            )
        return db_user

    # Offline mode: fabricated user from token
    user = User(
        id=token_data.user_id,
        username=token_data.username,
        email=f"{token_data.username}@example.com",
        full_name=None,
        company_id=token_data.company_id,
        role=token_data.role,
        is_active=True,
        created_at=token_data.iat or datetime.now(),
        updated_at=datetime.now(),
        last_login=None,
        permissions=token_data.permissions,
    )
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Get current active user."""

    if not current_user.is_active:
        logger.warning("Inactive user attempted access", user_id=current_user.id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user"
        )

    return current_user


def require_role(required_role: UserRole):
    """Dependency factory to require specific role."""

    async def role_checker(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        # Define role hierarchy (higher roles include lower role permissions)
        role_hierarchy = {
            UserRole.VIEWER: 1,
            UserRole.ANALYST: 2,
            UserRole.DATA_MANAGER: 3,
            UserRole.ADMIN: 4,
        }

        user_role_level = role_hierarchy.get(current_user.role, 0)
        required_role_level = role_hierarchy.get(required_role, 0)

        if user_role_level < required_role_level:
            logger.warning(
                "Insufficient role for access",
                user_id=current_user.id,
                user_role=current_user.role.value,
                required_role=required_role.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role: {required_role.value}",
            )

        return current_user

    return role_checker


def require_permission(required_permission: Permission):
    """Dependency factory to require specific permission."""

    async def permission_checker(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        if required_permission not in current_user.permissions:
            logger.warning(
                "Insufficient permissions for access",
                user_id=current_user.id,
                user_role=current_user.role.value,
                required_permission=required_permission.value,
                user_permissions=[p.value for p in current_user.permissions],
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {required_permission.value}",
            )

        return current_user

    return permission_checker


def require_permissions(*required_permissions: Permission):
    """Dependency factory to require multiple permissions (ALL required)."""

    async def permissions_checker(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        missing_permissions = []

        for permission in required_permissions:
            if permission not in current_user.permissions:
                missing_permissions.append(permission.value)

        if missing_permissions:
            logger.warning(
                "Missing required permissions",
                user_id=current_user.id,
                missing_permissions=missing_permissions,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permissions: {', '.join(missing_permissions)}",
            )

        return current_user

    return permissions_checker


def require_any_permission(*required_permissions: Permission):
    """Dependency factory to require any of the specified permissions (OR logic)."""

    async def any_permission_checker(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        has_permission = any(
            permission in current_user.permissions
            for permission in required_permissions
        )

        if not has_permission:
            logger.warning(
                "No required permissions found",
                user_id=current_user.id,
                required_permissions=[p.value for p in required_permissions],
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(p.value for p in required_permissions)}",
            )

        return current_user

    return any_permission_checker


def require_company_access(allow_admin_override: bool = True):
    """Dependency factory to require access to specific company data."""

    async def company_checker(
        company_id: str, current_user: User = Depends(get_current_active_user)
    ) -> User:
        # Admin can access all companies (if override allowed)
        if allow_admin_override and current_user.role == UserRole.ADMIN:
            return current_user

        # User must belong to the same company
        if current_user.company_id != company_id:
            logger.warning(
                "Unauthorized company access attempt",
                user_id=current_user.id,
                user_company=current_user.company_id,
                requested_company=company_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: insufficient company permissions",
            )

        return current_user

    return company_checker


# Optional authentication (for public endpoints that can benefit from user context)
async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(
        HTTPBearer(auto_error=False)
    ),
    users=Depends(get_user_store),
) -> Optional[User]:
    """Get user if authenticated, None otherwise (for optional auth endpoints)."""

    if not credentials:
        return None

    try:
        token_data = jwt_handler.verify_token(credentials.credentials)
        if token_data:
            # Pass the resolved store explicitly: get_current_user is called
            # directly here (not via FastAPI DI), so its Depends default would
            # otherwise resolve to an unusable marker in require_database mode.
            return await get_current_user(token_data, users=users)
    except Exception:
        # Silently fail for optional auth
        pass

    return None
