"""
Application settings and configuration.
"""

from typing import List, Literal, Optional, Union

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Application
    app_name: str = "SustainabilityDataSpace API"
    app_version: str = "2.1.0"
    debug: bool = False

    # Server
    host: str = "127.0.0.1"
    port: int = 8090

    # Database
    database_url: SecretStr = SecretStr("postgresql://sds:password@localhost:5432/sds")
    require_database: bool = True  # Full local/API runtime is DB-backed by default.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    seed_reference_data_on_startup: bool = True
    reference_indicators_path: Optional[str] = (
        "samples/public-demo/reference_indicators.json"
    )
    reference_mappings_path: Optional[str] = (
        "samples/public-demo/reference_mappings.json"
    )
    currencies_seed_path: Optional[str] = None
    semantic_bundle_path: Optional[str] = None

    # Value versioning
    value_revision_api_enabled: bool = True
    value_revision_primary_read_path: Literal["legacy", "revision"] = "revision"
    value_revision_dual_write_enabled: bool = False
    value_revision_default_tenant_id: str = "sds_default"

    # Indicator CSV import guards
    indicator_import_max_bytes: int = 10 * 1024 * 1024
    indicator_import_max_rows: int = 10_000
    indicator_import_active_timeout_minutes: int = 24 * 60
    indicator_import_validation_payload_retention_hours: int = 24
    value_import_max_bytes: int = 10 * 1024 * 1024
    value_import_max_rows: int = 1000

    # Internal canonical mapping package inspection
    canonical_mapping_inspection_roots: Union[List[str], str] = []
    mapping_surface: Literal["canonical"] = "canonical"

    # CORS
    allowed_origins: Union[List[str], str] = []
    cors_allow_credentials: bool = False

    # Logging
    log_level: str = "INFO"

    # Unit converter
    units_database_path: str = "samples/public-demo/units_database.json"

    # Units Storage
    use_postgres_units: Optional[bool] = (
        False  # False=default offline, None=auto-detect, True=force PostgreSQL
    )
    units_json_path: Optional[str] = None

    # JWT Authentication
    jwt_secret_key: SecretStr  # Required — no default; must be set via env or .env
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    # API Keys
    api_key_expire_days: int = 365
    trusted_proxy_ips: Union[List[str], str] = []

    # Rate limiting. Default in-memory storage is per-process; set a shared
    # backend (e.g. "redis://host:6379") for correct limiting across multiple
    # workers/instances (requires the redis extra).
    rate_limit_storage_uri: str = "memory://"
    rate_limit_default: str = "100/minute"

    # Export signing
    export_signing_secret: Optional[SecretStr] = None
    export_signing_key_id: str = "sds-default"

    # DB bootstrap (VM/human testing)
    seed_default_users: bool = False
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: Optional[SecretStr] = None
    # Same-origin prototype portal
    portal_mount_enabled: bool = True
    portal_mount_path: str = "/portal"
    portal_mount_directory: Optional[str] = None

    @field_validator(
        "allowed_origins",
        "canonical_mapping_inspection_roots",
        "trusted_proxy_ips",
        mode="before",
    )
    @classmethod
    def _split_csv_list(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("jwt_secret_key")
    @classmethod
    def _validate_jwt_secret_key(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return value


# Global settings instance
settings = Settings()
