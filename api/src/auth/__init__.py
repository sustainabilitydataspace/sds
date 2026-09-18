"""
Authentication and authorization module for SustainabilityDataSpace API.
"""

from .dependencies import get_current_active_user, get_current_user
from .jwt_handler import JWTHandler
from .models import Token, TokenData, User, UserCreate, UserResponse
from .rbac import RoleBasedAccessControl, require_permission, require_role

__all__ = [
    "JWTHandler",
    "User",
    "UserCreate",
    "UserResponse",
    "Token",
    "TokenData",
    "get_current_user",
    "get_current_active_user",
    "RoleBasedAccessControl",
    "require_role",
    "require_permission",
]
