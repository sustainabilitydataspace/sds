"""
Authentication endpoints for login, token management, and user operations.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials

import structlog
from src.api.rate_limit import limiter
from src.auth.authorization import require_bearer_authentication
from src.auth.dependencies import (
    get_current_active_user,
    require_permission,
    require_role,
    security,
)
from src.auth.jwt_handler import jwt_handler
from src.auth.models import (
    AdminPasswordReset,
    AdminUserUpdate,
    APIKeyCreate,
    APIKeyInfoResponse,
    APIKeyResponse,
    PasswordChangeRequest,
    Permission,
    RefreshTokenRequest,
    Token,
    User,
    UserCreate,
    UserLogin,
    UserResponse,
    UserRole,
    UserUpdate,
)
from src.auth.rbac import rbac
from src.config.settings import settings
from src.services.api_key_store import get_api_key_store
from src.services.user_store import get_user_store

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post(
    "/login",
    response_model=Token,
    summary="User login",
    description="Authenticate user and return JWT tokens",
)
@limiter.limit("5/minute")
async def login(
    request: Request,
    user_credentials: Annotated[
        UserLogin,
        Body(
            openapi_examples={
                "credentials": {
                    "summary": "Environment-specific account credentials",
                    "description": (
                        "Replace both placeholders with an account provisioned for "
                        "the target environment. No runnable password is published."
                    ),
                    "value": {
                        "username": "your-account",
                        "password": "[REDACTED]",
                    },
                }
            }
        ),
    ],
    store=Depends(get_user_store),
) -> Token:
    """
    Authenticate user and return access and refresh tokens.

    - **username**: User's username
    - **password**: User's password
    """
    try:
        logger.info("Login attempt")

        user = store.authenticate(
            username=user_credentials.username,
            password=user_credentials.password,
        )

        if user is None:
            logger.warning("Authentication failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Create tokens
        access_token = jwt_handler.create_access_token(
            user_id=user.id,
            username=user.username,
            role=user.role,
            company_id=user.company_id,
            auth_version=user.auth_version,
        )

        refresh_token = jwt_handler.create_refresh_token(
            user_id=user.id,
            username=user.username,
            role=user.role,
            company_id=user.company_id,
            auth_version=user.auth_version,
        )

        logger.info("Login successful")

        return Token(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=jwt_handler.access_token_expire_minutes * 60,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Login failed", error_type=type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed due to internal error",
        )


@router.post(
    "/refresh",
    response_model=Dict[str, Any],
    summary="Refresh access token",
    description="Get new access token using refresh token",
)
@limiter.limit("10/minute")
async def refresh_token(
    request: Request,
    refresh_request: Annotated[
        RefreshTokenRequest,
        Body(),
    ],
    store=Depends(get_user_store),
) -> Dict[str, Any]:
    """
    Refresh access token using refresh token.

    - **refresh_token**: Valid refresh token
    """
    try:
        logger.info("Token refresh attempt")

        def _lookup(user_id: str):
            return store.get_user_by_id(user_id=user_id)

        def _invalidate_replayed_session(user_id: str) -> None:
            user = store.get_user_by_id(user_id=user_id)
            if user is not None:
                store.invalidate_sessions(username=user.username)

        db = None
        consume_token: Optional[Callable[[str, datetime], bool]] = None
        if getattr(settings, "require_database", False):
            from src.database.session import SessionLocal

            db = SessionLocal()

            def _consume_token(token: str, expires_at: datetime) -> bool:
                return jwt_handler.consume_token_persistent(token, expires_at, db)

            consume_token = _consume_token

        try:
            result = jwt_handler.refresh_access_token(
                refresh_request.refresh_token,
                user_lookup=_lookup,
                consume_token=consume_token,
                on_replay=_invalidate_replayed_session,
                require_user=settings.require_database,
            )
        finally:
            if db is not None:
                db.close()

        if not result:
            logger.warning("Invalid refresh token provided")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        logger.info("Token refreshed successfully")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Token refresh failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token refresh failed",
        )


@router.post(
    "/logout", summary="User logout", description="Invalidate current access token"
)
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: User = Depends(get_current_active_user),
    logout_request: Optional[RefreshTokenRequest] = Body(default=None),
    store=Depends(get_user_store),
) -> Dict[str, str]:
    """
    Logout user and invalidate token.

    Optionally include the `refresh_token` in the body to revoke it too, so a
    leaked refresh token cannot keep minting access tokens after logout.
    """
    try:
        logger.info("Logout attempt", user_id=current_user.id)

        # Invalidate the access token in-process.
        jwt_handler.invalidate_token(credentials.credentials)

        # Persist revocation to DB when database is available. A failure here is
        # NOT swallowed: logout fails closed (500) so the caller cannot believe a
        # token was revoked when it was not.
        if getattr(settings, "require_database", False):
            from src.database.session import SessionLocal

            db = SessionLocal()
            try:
                token_info = jwt_handler.get_token_expiry_info(credentials.credentials)
                if token_info and "expires_at" in token_info:
                    expires_at = datetime.fromisoformat(token_info["expires_at"])
                else:
                    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
                jwt_handler.revoke_token_persistent(
                    credentials.credentials, expires_at, db
                )

                # Revoke the refresh token too when the caller supplies it.
                # Guard with isinstance: when logout() is called directly (not via
                # FastAPI DI) the default is a Body(...) marker, not None.
                if (
                    isinstance(logout_request, RefreshTokenRequest)
                    and logout_request.refresh_token
                ):
                    refresh = logout_request.refresh_token
                    jwt_handler.invalidate_token(refresh)
                    rt_info = jwt_handler.get_token_expiry_info(refresh)
                    if rt_info and "expires_at" in rt_info:
                        rt_expires = datetime.fromisoformat(rt_info["expires_at"])
                    else:
                        rt_expires = datetime.now(timezone.utc) + timedelta(days=7)
                    jwt_handler.revoke_token_persistent(refresh, rt_expires, db)
            finally:
                db.close()

        if hasattr(store, "invalidate_sessions") and not store.invalidate_sessions(
            username=current_user.username
        ):
            raise RuntimeError("Unable to invalidate user sessions")

        logger.info("Logout successful", user_id=current_user.id)

        return {"message": "Successfully logged out"}

    except Exception as e:
        logger.error("Logout failed", error=str(e), user_id=current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Logout failed"
        )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
    description="Get current authenticated user information",
)
async def get_current_user_info(
    current_user: User = Depends(get_current_active_user),
    store=Depends(get_user_store),
) -> UserResponse:
    """
    Get current user information.
    """
    persisted = store.get_user(username=current_user.username)
    user = persisted or current_user

    return UserResponse(
        id=current_user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        company_id=user.company_id,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        permissions=current_user.permissions,
    )


@router.put(
    "/me",
    response_model=UserResponse,
    summary="Update current user",
    description="Update current user information",
)
async def update_current_user(
    user_update: UserUpdate,
    current_user: User = Depends(get_current_active_user),
    store=Depends(get_user_store),
) -> UserResponse:
    """
    Update current user information.
    """
    try:
        require_bearer_authentication(current_user)
        logger.info("User update attempt", user_id=current_user.id)

        admin_update_fields = {"company_id", "role", "is_active"}
        requested_admin_fields = admin_update_fields.intersection(
            user_update.model_fields_set
        )
        allow_admin_fields = (
            current_user.role == UserRole.ADMIN
            and Permission.MANAGE_USERS in current_user.permissions
        )
        if requested_admin_fields and not allow_admin_fields:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Administrative user fields require manage_users permission",
            )

        try:
            updated = store.update_user(
                username=current_user.username,
                update=user_update,
                allow_admin_fields=allow_admin_fields,
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already in use",
            )

        updated_user = updated or current_user

        logger.info("User updated successfully", user_id=current_user.id)

        return UserResponse(
            id=updated_user.id,
            username=updated_user.username,
            email=updated_user.email,
            full_name=updated_user.full_name,
            company_id=updated_user.company_id,
            role=updated_user.role,
            is_active=updated_user.is_active,
            created_at=updated_user.created_at,
            last_login=updated_user.last_login,
            permissions=current_user.permissions,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("User update failed", error=str(e), user_id=current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User update failed",
        )


@router.post(
    "/change-password",
    summary="Change password",
    description="Change current user password",
)
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    password_change: PasswordChangeRequest,
    current_user: User = Depends(get_current_active_user),
    store=Depends(get_user_store),
) -> Dict[str, str]:
    """
    Change current user password.
    """
    try:
        require_bearer_authentication(current_user)
        logger.info("Password change attempt", user_id=current_user.id)

        ok = store.change_password(
            username=current_user.username,
            current_password=password_change.current_password,
            new_password=password_change.new_password,
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid current password",
            )

        logger.info("Password changed successfully", user_id=current_user.id)

        return {"message": "Password changed successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Password change failed", error=str(e), user_id=current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password change failed",
        )


@router.get(
    "/token/info",
    summary="Get token information",
    description="Get information about current token",
)
async def get_token_info(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Get information about the current token.
    """
    try:
        token_info = jwt_handler.get_token_expiry_info(credentials.credentials)

        if not token_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            )

        return token_info

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get token info", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get token information",
        )


# Admin-only endpoints
@router.post(
    "/users",
    response_model=UserResponse,
    summary="Create user",
    description="Create a new user (Admin only)",
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
async def create_user(
    user_create: UserCreate,
    current_user: User = Depends(get_current_active_user),
    store=Depends(get_user_store),
) -> UserResponse:
    """
    Create a new user (Admin only).
    """
    try:
        require_bearer_authentication(current_user)
        logger.info(
            "User creation attempt",
            username=user_create.username,
            created_by=current_user.id,
        )

        new_user = store.create_user(user_create, created_by=current_user.id)

        logger.info(
            "User created successfully",
            username=user_create.username,
            user_id=new_user.id,
        )

        return UserResponse(
            id=new_user.id,
            username=new_user.username,
            email=new_user.email,
            full_name=new_user.full_name,
            company_id=new_user.company_id,
            role=new_user.role,
            is_active=new_user.is_active,
            created_at=new_user.created_at,
            last_login=new_user.last_login,
            permissions=new_user.permissions,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "User creation failed", error=str(e), username=user_create.username
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User creation failed",
        )


class _SecretSafeValidationRoute(APIRoute):
    """Return 422 details without echoing submitted values (e.g. passwords)."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def secret_safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                errors = [
                    {k: v for k, v in error.items() if k not in ("input", "ctx")}
                    for error in exc.errors()
                ]
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    content={"detail": jsonable_encoder(errors)},
                )

        return secret_safe_handler


admin_users_router = APIRouter(route_class=_SecretSafeValidationRoute)


def _require_admin_bearer(current_user: User) -> None:
    require_bearer_authentication(current_user)
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative user fields require manage_users permission",
        )


def _deny(event: str, reason: str, current_user: User, username: str) -> None:
    logger.warning(
        event,
        reason=reason,
        actor_user_id=current_user.id,
        target_username=username,
    )


@admin_users_router.put(
    "/users/{username}",
    response_model=UserResponse,
    summary="Update another user's profile, tenant, role or active state",
    description=(
        "Set email, full_name, company_id (tenant), role or is_active of another "
        "existing user (Admin bearer only). Revokes the target's existing "
        "sessions. Use /auth/me for your own account and "
        "/auth/users/{username}/reset-password for passwords."
    ),
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
@limiter.limit("5/minute")
async def admin_update_user(
    request: Request,
    username: str,
    user_update: AdminUserUpdate,
    current_user: User = Depends(get_current_active_user),
    store=Depends(get_user_store),
) -> UserResponse:
    """
    Update profile, tenant, role or active state of another user (Admin only).
    """
    requested_fields = sorted(user_update.model_fields_set)
    try:
        _require_admin_bearer(current_user)
        logger.info(
            "admin_user_update_attempt",
            actor_user_id=current_user.id,
            target_username=username,
            requested_fields=requested_fields,
        )

        if username == current_user.username:
            _deny("admin_user_update_denied", "self_target", current_user, username)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Use /auth/me to update your own account",
            )

        not_found = HTTPException(status_code=404, detail="User not found")
        if store.get_user(username=username) is None:
            _deny("admin_user_update_denied", "not_found", current_user, username)
            raise not_found

        try:
            updated = store.update_user(
                username=username,
                update=UserUpdate(**user_update.model_dump(exclude_unset=True)),
                allow_admin_fields=True,
                revoke_sessions=True,
            )
        except ValueError:
            _deny("admin_user_update_denied", "email_conflict", current_user, username)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already in use",
            )
        if updated is None:
            if store.get_user(username=username) is None:
                _deny("admin_user_update_denied", "not_found", current_user, username)
                raise not_found
            raise RuntimeError("user update was not persisted")

        # Email and full_name values are personal data: log field names only.
        logger.info(
            "admin_user_updated",
            actor_user_id=current_user.id,
            target_user_id=updated.id,
            changed_fields=requested_fields,
            company_id=updated.company_id,
            role=updated.role.value,
            is_active=updated.is_active,
            auth_version=updated.auth_version,
        )

        return UserResponse(
            id=updated.id,
            username=updated.username,
            email=updated.email,
            full_name=updated.full_name,
            company_id=updated.company_id,
            role=updated.role,
            is_active=updated.is_active,
            created_at=updated.created_at,
            last_login=updated.last_login,
            permissions=updated.permissions,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "admin_user_update_failed",
            actor_user_id=current_user.id,
            target_username=username,
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User update failed",
        )


@admin_users_router.post(
    "/users/{username}/reset-password",
    summary="Reset another user's password",
    description=(
        "Set a new password for another existing user (Admin bearer only). The "
        "password must meet the application policy and is never returned. "
        "Revokes the target's existing sessions; an inactive user stays inactive. "
        "Use /auth/change-password for your own password."
    ),
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
@limiter.limit("5/minute")
async def admin_reset_password(
    request: Request,
    username: str,
    password_reset: AdminPasswordReset,
    current_user: User = Depends(get_current_active_user),
    store=Depends(get_user_store),
) -> Dict[str, str]:
    """
    Reset the password of another user (Admin only).
    """
    try:
        _require_admin_bearer(current_user)
        logger.info(
            "admin_user_password_reset_attempt",
            actor_user_id=current_user.id,
            target_username=username,
        )

        if username == current_user.username:
            _deny(
                "admin_user_password_reset_denied",
                "self_target",
                current_user,
                username,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Use /auth/change-password to change your own password",
            )

        not_found = HTTPException(status_code=404, detail="User not found")
        if store.get_user(username=username) is None:
            _deny(
                "admin_user_password_reset_denied", "not_found", current_user, username
            )
            raise not_found

        updated = store.admin_set_password(
            username=username, new_password=password_reset.new_password
        )
        if updated is None:
            if store.get_user(username=username) is None:
                _deny(
                    "admin_user_password_reset_denied",
                    "not_found",
                    current_user,
                    username,
                )
                raise not_found
            _deny(
                "admin_user_password_reset_denied",
                "concurrent_update",
                current_user,
                username,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Concurrent update, retry",
            )

        logger.info(
            "admin_user_password_reset_succeeded",
            actor_user_id=current_user.id,
            target_user_id=updated.id,
            auth_version=updated.auth_version,
        )
        return {"message": "Password reset"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "admin_user_password_reset_failed",
            actor_user_id=current_user.id,
            target_username=username,
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password reset failed",
        )


router.include_router(admin_users_router)


@router.post(
    "/api-keys",
    response_model=APIKeyResponse,
    summary="Create API key",
    description="Create a new API key for programmatic access",
)
async def create_api_key(
    api_key_create: APIKeyCreate,
    current_user: User = Depends(get_current_active_user),
    store=Depends(get_api_key_store),
) -> APIKeyResponse:
    """
    Create a new API key for the current user.
    """
    try:
        require_bearer_authentication(current_user)
        requested_permissions = set(api_key_create.permissions)
        allowed_permissions = set(current_user.permissions)
        disallowed_permissions = sorted(
            permission.value
            for permission in requested_permissions
            if permission not in allowed_permissions
        )
        if disallowed_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "API key permissions exceed current user permissions: "
                    + ", ".join(disallowed_permissions)
                ),
            )

        logger.info(
            "API key creation attempt",
            user_id=current_user.id,
            key_name=api_key_create.name,
        )

        response = store.create_api_key(user_id=current_user.id, request=api_key_create)
        logger.info(
            "API key created successfully",
            user_id=current_user.id,
            key_name=api_key_create.name,
        )
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error("API key creation failed", error=str(e), user_id=current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key creation failed",
        )


@router.get(
    "/api-keys",
    response_model=List[APIKeyInfoResponse],
    summary="List API keys",
    description="List API keys for the current user",
)
async def list_api_keys(
    current_user: User = Depends(get_current_active_user),
    store=Depends(get_api_key_store),
) -> List[APIKeyInfoResponse]:
    return store.list_api_keys(user_id=current_user.id)


@router.delete(
    "/api-keys/{api_key_id}",
    summary="Revoke API key",
    description="Revoke (disable) an API key for the current user",
)
async def revoke_api_key(
    api_key_id: str,
    current_user: User = Depends(get_current_active_user),
    store=Depends(get_api_key_store),
) -> Dict[str, str]:
    require_bearer_authentication(current_user)
    ok = store.revoke_api_key(user_id=current_user.id, key_id=api_key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"message": "API key revoked"}
