"""
Authentication and authorization models.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRole(str, Enum):
    """User roles in the system."""

    ADMIN = "admin"
    DATA_MANAGER = "data_manager"
    ANALYST = "analyst"
    VIEWER = "viewer"


class Permission(str, Enum):
    """System permissions."""

    # Value management
    CREATE_VALUES = "create_values"
    READ_VALUES = "read_values"
    UPDATE_VALUES = "update_values"
    DELETE_VALUES = "delete_values"

    # Calculation permissions
    EXECUTE_CALCULATIONS = "execute_calculations"
    VIEW_CALCULATION_TRACE = "view_calculation_trace"

    # Configuration permissions
    MANAGE_HIERARCHIES = "manage_hierarchies"
    VIEW_HIERARCHIES = "view_hierarchies"

    # Ontology permissions
    QUERY_ONTOLOGY = "query_ontology"
    EXECUTE_SPARQL = "execute_sparql"
    MANAGE_ONTOLOGY = "manage_ontology"

    # Unit conversion
    CONVERT_UNITS = "convert_units"
    MANAGE_UNITS = "manage_units"

    # Currency conversion
    MANAGE_FX_RATES = "manage_fx_rates"

    # E1 Indicator permissions (E6 policy integration)
    READ_INDICATORS = "read_indicators"
    MANAGE_INDICATORS = "manage_indicators"

    # E2 Mapping permissions (E6 policy integration)
    READ_MAPPINGS = "read_mappings"
    MANAGE_MAPPINGS = "manage_mappings"

    # Legacy E2 Crosswalk permissions (deprecated)
    READ_CROSSWALKS = "read_crosswalks"
    MANAGE_CROSSWALKS = "manage_crosswalks"

    # System administration
    MANAGE_USERS = "manage_users"
    VIEW_SYSTEM_HEALTH = "view_system_health"
    MANAGE_SYSTEM = "manage_system"


class UserBase(BaseModel):
    """Base user model."""

    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    full_name: Optional[str] = Field(None, max_length=100)
    company_id: Optional[str] = Field(None, max_length=50)
    role: UserRole = UserRole.VIEWER
    is_active: bool = True


class UserCreate(UserBase):
    """User creation model."""

    password: str = Field(..., min_length=8, max_length=100)


class UserUpdate(BaseModel):
    """User update model."""

    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(None, max_length=100)
    company_id: Optional[str] = Field(None, max_length=50)
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class User(UserBase):
    """User model with database fields."""

    id: str
    created_at: datetime
    updated_at: datetime
    last_login: Optional[datetime] = None
    permissions: List[Permission] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    """User response model (without sensitive data)."""

    id: str
    username: str
    email: str
    full_name: Optional[str]
    company_id: Optional[str]
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime]
    permissions: List[Permission]


class UserLogin(BaseModel):
    """User login model."""

    username: str = Field(..., description="Account username.")
    password: str = Field(..., description="Password for the account username.")


class Token(BaseModel):
    """JWT token model."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 3600,
            }
        }
    )


class TokenData(BaseModel):
    """Token payload data."""

    user_id: str
    username: str
    role: UserRole
    company_id: Optional[str] = None
    permissions: List[Permission] = Field(default_factory=list)
    auth_method: str = "bearer"
    exp: Optional[datetime] = None
    iat: Optional[datetime] = None


class RefreshTokenRequest(BaseModel):
    """Refresh token request model."""

    refresh_token: str = Field(
        ...,
        description=(
            "Paste the refresh_token returned by POST /auth/login. "
            "The literal Swagger placeholder 'string' is not a valid token."
        ),
    )


class PasswordChangeRequest(BaseModel):
    """Password change request model."""

    current_password: str
    new_password: str = Field(..., min_length=8, max_length=100)


class APIKeyCreate(BaseModel):
    """API key creation model."""

    name: str = Field(..., max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    expires_at: Optional[datetime] = None
    permissions: List[Permission] = Field(default_factory=list)


class APIKey(BaseModel):
    """API key model."""

    id: str
    name: str
    description: Optional[str]
    key_hash: str  # Hashed version of the key
    user_id: str
    created_at: datetime
    expires_at: Optional[datetime]
    last_used: Optional[datetime]
    is_active: bool
    permissions: List[Permission]


class APIKeyResponse(BaseModel):
    """API key response model."""

    id: str
    name: str
    description: Optional[str]
    key: str  # Only returned on creation
    created_at: datetime
    expires_at: Optional[datetime]
    permissions: List[Permission]


class APIKeyInfoResponse(BaseModel):
    """API key listing model (no secret material)."""

    id: str
    name: str
    description: Optional[str]
    created_at: datetime
    expires_at: Optional[datetime]
    last_used: Optional[datetime]
    is_active: bool
    permissions: List[Permission]


# Role-Permission mapping
ROLE_PERMISSIONS = {
    UserRole.ADMIN: [
        # All permissions
        Permission.CREATE_VALUES,
        Permission.READ_VALUES,
        Permission.UPDATE_VALUES,
        Permission.DELETE_VALUES,
        Permission.EXECUTE_CALCULATIONS,
        Permission.VIEW_CALCULATION_TRACE,
        Permission.MANAGE_HIERARCHIES,
        Permission.VIEW_HIERARCHIES,
        Permission.QUERY_ONTOLOGY,
        Permission.EXECUTE_SPARQL,
        Permission.MANAGE_ONTOLOGY,
        Permission.CONVERT_UNITS,
        Permission.MANAGE_UNITS,
        Permission.MANAGE_FX_RATES,
        Permission.READ_INDICATORS,
        Permission.MANAGE_INDICATORS,
        Permission.READ_MAPPINGS,
        Permission.MANAGE_MAPPINGS,
        Permission.READ_CROSSWALKS,
        Permission.MANAGE_CROSSWALKS,
        Permission.MANAGE_USERS,
        Permission.VIEW_SYSTEM_HEALTH,
        Permission.MANAGE_SYSTEM,
    ],
    UserRole.DATA_MANAGER: [
        Permission.CREATE_VALUES,
        Permission.READ_VALUES,
        Permission.UPDATE_VALUES,
        Permission.DELETE_VALUES,
        Permission.EXECUTE_CALCULATIONS,
        Permission.VIEW_CALCULATION_TRACE,
        Permission.MANAGE_HIERARCHIES,
        Permission.VIEW_HIERARCHIES,
        Permission.QUERY_ONTOLOGY,
        Permission.EXECUTE_SPARQL,
        Permission.CONVERT_UNITS,
        Permission.MANAGE_FX_RATES,
        Permission.READ_INDICATORS,
        Permission.MANAGE_INDICATORS,
        Permission.READ_MAPPINGS,
        Permission.MANAGE_MAPPINGS,
        Permission.READ_CROSSWALKS,
        Permission.MANAGE_CROSSWALKS,
        Permission.VIEW_SYSTEM_HEALTH,
    ],
    UserRole.ANALYST: [
        Permission.READ_VALUES,
        Permission.EXECUTE_CALCULATIONS,
        Permission.VIEW_CALCULATION_TRACE,
        Permission.VIEW_HIERARCHIES,
        Permission.QUERY_ONTOLOGY,
        Permission.EXECUTE_SPARQL,
        Permission.CONVERT_UNITS,
        Permission.READ_INDICATORS,
        Permission.READ_MAPPINGS,
        Permission.READ_CROSSWALKS,
    ],
    UserRole.VIEWER: [
        Permission.READ_VALUES,
        Permission.VIEW_HIERARCHIES,
        Permission.QUERY_ONTOLOGY,
        Permission.CONVERT_UNITS,
        Permission.READ_INDICATORS,
        Permission.READ_MAPPINGS,
        Permission.READ_CROSSWALKS,
    ],
}
