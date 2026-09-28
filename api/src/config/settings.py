"""
Application settings and configuration.
"""

from typing import List, Literal, Optional, Union

from pydantic import SecretStr, field_validator, model_validator
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
    database_url: SecretStr = SecretStr("postgresql://sds:***@localhost:5432/sds")
    require_database: bool = True  # Full local/API runtime is DB-backed by default.
    schema_migrations_externally_managed: bool = False
    allow_in_memory_auth: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20
    seed_reference_data_on_startup: bool = True

    # Value versioning
    value_revision_api_enabled: bool = True
    value_revision_primary_read_path: Literal["legacy", "revision"] = "legacy"
    value_revision_dual_write_enabled: bool = False
    value_revision_default_tenant_id: str = "nordhaven_components_group"

    # Indicator CSV import guards
    indicator_import_max_bytes: int = 10 * 1024 * 1024
    indicator_import_max_rows: int = 10_000
    indicator_import_active_timeout_minutes: int = 24 * 60
    indicator_import_validation_payload_retention_hours: int = 24
    value_import_max_bytes: int = 10 * 1024 * 1024
    value_import_max_rows: int = 1000
    request_max_body_bytes: int = 10 * 1024 * 1024

    # Internal canonical mapping package inspection
    canonical_mapping_inspection_roots: Union[List[str], str] = []
    mapping_surface: Literal["canonical"] = "canonical"

    # CORS
    allowed_origins: Union[List[str], str] = []
    cors_allow_credentials: bool = False

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Unit converter
    units_database_path: str = "src/data/units_database.json"

    # Units Storage
    use_postgres_units: Optional[bool] = (
        False  # False=default offline, None=auto-detect, True=force PostgreSQL
    )
    units_json_path: Optional[str] = None  # Custom path to units JSON file

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

    # Local-only custom SPARQL execution guards. Production DB mode returns 501.
    sparql_query_timeout_seconds: float = 5.0
    sparql_max_results: int = 1000
    sparql_max_graph_bytes: int = 32 * 1024 * 1024
    sparql_max_result_bytes: int = 2 * 1024 * 1024

    # Export signing
    export_signing_secret: SecretStr
    export_signing_key_id: str = "sds-default"

    # DB bootstrap (VM/human testing)
    seed_default_users: bool = False
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: Optional[SecretStr] = None
    # Same-origin prototype portal
    portal_mount_enabled: bool = True
    portal_mount_path: str = "/portal"
    portal_mount_directory: Optional[str] = None

    @model_validator(mode="after")
    def _validate_postgres_pool_capacity(self):
        if self.db_pool_size < 1 or self.db_max_overflow < 0:
            raise ValueError("PostgreSQL pool size and overflow must be nonnegative")
        if self.require_database and self.db_pool_size + self.db_max_overflow < 2:
            raise ValueError(
                "DB-first canonical import requires at least two PostgreSQL connections "
                "(request session and independent lifecycle guard)"
            )
        return self

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
        if len(set(secret)) < 12:
            raise ValueError("JWT_SECRET_KEY must have sufficient character diversity")
        return value

    @field_validator("export_signing_secret")
    @classmethod
    def _validate_export_signing_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < 32:
            raise ValueError("EXPORT_SIGNING_SECRET must be at least 32 characters")
        if len(set(secret)) < 12:
            raise ValueError(
                "EXPORT_SIGNING_SECRET must have sufficient character diversity"
            )
        return value

    @field_validator(
        "value_import_max_bytes", "value_import_max_rows", "request_max_body_bytes"
    )
    @classmethod
    def _validate_value_import_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(
                "request and value import limits must be greater than zero"
            )
        return value


# Global settings instance
settings = Settings()
