"""SQLAlchemy models for PostgreSQL database."""

from enum import Enum

import sqlalchemy as sa
from sqlalchemy import ARRAY, BigInteger, Boolean, Column, Date, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class ConceptState(str, Enum):
    """Semantic lifecycle for a catalogued indicator/concept."""

    CATALOGUED = "catalogued"
    SEMANTICALLY_MODELLED = "semantically_modelled"
    CALCULABLE = "calculable"


CONCEPT_STATE_ENUM = SAEnum(
    ConceptState,
    name="concept_state",
    native_enum=False,
    validate_strings=True,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class HierarchyConfiguration(Base):
    """Database model for hierarchy configurations."""

    __tablename__ = "hierarchy_configurations"

    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False, index=True)
    hierarchy_type = Column(
        String, nullable=False
    )  # 'organizational' or 'geographical'
    name = Column(String, nullable=False)
    description = Column(Text)
    configuration = Column(Text, nullable=False)  # JSON string
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(String)


class ESGValue(Base):
    """Database model for persisted ESG values."""

    __tablename__ = "esg_values"

    id = Column(String, primary_key=True)
    concept = Column(String, nullable=False, index=True)
    entity = Column(String, nullable=False, index=True)
    period = Column(Date, nullable=False, index=True)
    external_key = Column(String(255))
    value = Column(Numeric(30, 10), nullable=True)
    original_value = Column(Numeric(30, 10), nullable=True)
    value_type = Column(
        String(20), nullable=False, default="numeric", server_default="numeric"
    )
    text_value = Column(Text)
    boolean_value = Column(Boolean)
    unit = Column(String, nullable=False)
    original_unit = Column(String)
    conversion_applied = Column(Boolean, default=False)
    currency = Column(String(3))
    original_currency = Column(String(3))
    currency_conversion_applied = Column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    conversion_trace = Column(JSONB)
    value_date = Column(Date)
    period_start = Column(Date)
    period_end = Column(Date)
    value_metadata = Column(JSONB)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(String)

    __table_args__ = (
        Index("ix_esg_values_concept_entity_period", "concept", "entity", "period"),
        Index(
            "ix_esg_values_read_cursor",
            "concept",
            "entity",
            "period",
            "created_at",
            "id",
        ),
        Index("ix_esg_values_change_cursor", "updated_at", "id"),
        Index(
            "ix_esg_values_external_key_unique",
            "external_key",
            unique=True,
            postgresql_where=text("external_key IS NOT NULL"),
        ),
    )


class ValueIdempotencyKey(Base):
    """Database model for request-level idempotency on value writes."""

    __tablename__ = "value_idempotency_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False)
    scope = Column(String(50), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    request_hash = Column(String(64), nullable=False)
    state = Column(String(20), nullable=False, default="in_progress")
    response_status = Column(Integer)
    response_body = Column(JSONB)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index(
            "ix_value_idempotency_keys_user_scope_key",
            "user_id",
            "scope",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_value_idempotency_keys_expires_at", "expires_at"),
    )


class ValueImportJob(Base):
    """Persistent async import job for operational ESG values."""

    __tablename__ = "value_import_jobs"

    id = Column(String, primary_key=True)
    source_format = Column(String(20), nullable=False)  # json or csv
    status = Column(
        String(20), nullable=False, index=True
    )  # pending, running, completed, failed
    submitted_by = Column(String(100), nullable=False, index=True)
    source_filename = Column(String(255))
    request_metadata = Column(JSONB)
    total_rows = Column(Integer)
    accepted_rows = Column(Integer)
    rejected_rows = Column(Integer)
    committed = Column(Boolean)
    result_body = Column(JSONB)
    error_message = Column(Text)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    updated_at = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_value_import_jobs_submitted_created", "submitted_by", "created_at"),
    )


class IndicatorImportJob(Base):
    """Persistent validation/import job for indicator register CSV uploads."""

    __tablename__ = "indicator_import_jobs"

    id = Column(String, primary_key=True)
    job_type = Column(String(20), nullable=False)  # validation or import
    source_format = Column(String(20), nullable=False)  # csv
    status = Column(
        String(20), nullable=False, index=True
    )  # pending, running, completed, failed
    submitted_by = Column(String(100), nullable=False, index=True)
    source_filename = Column(String(255))
    source_sha256 = Column(String(64), index=True)
    source_size_bytes = Column(Integer)
    source_payload = Column(Text)
    validation_job_id = Column(String, index=True)
    request_metadata = Column(JSONB)
    total_rows = Column(Integer)
    accepted_rows = Column(Integer)
    rejected_rows = Column(Integer)
    committed = Column(Boolean)
    result_body = Column(JSONB)
    error_message = Column(Text)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    updated_at = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_indicator_import_jobs_type_status", "job_type", "status"),
        Index(
            "ix_indicator_import_jobs_submitted_created", "submitted_by", "created_at"
        ),
    )


class CanonicalMappingPackageJob(Base):
    """Persistent validation/import job for canonical mapping packages."""

    __tablename__ = "canonical_mapping_package_jobs"

    id = Column(String, primary_key=True)
    job_type = Column(String(20), nullable=False)
    package_id = Column(String(255), nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True)
    submitted_by = Column(String(100), nullable=False, index=True)
    validation_job_id = Column(String, index=True)
    request_metadata = Column(JSONB)
    total_rows = Column(Integer)
    accepted_rows = Column(Integer)
    rejected_rows = Column(Integer)
    committed = Column(Boolean)
    result_body = Column(JSONB)
    error_message = Column(Text)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    updated_at = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_canonical_mapping_package_jobs_type_status",
            "job_type",
            "status",
        ),
        Index(
            "ix_canonical_mapping_package_jobs_submitted_created",
            "submitted_by",
            "created_at",
        ),
        Index(
            "ix_canonical_mapping_package_jobs_package_created",
            "package_id",
            "created_at",
        ),
    )


class UserAccount(Base):
    """Database model for user accounts (auth persistence)."""

    __tablename__ = "user_accounts"

    id = Column(String, primary_key=True)
    username = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String(100))
    company_id = Column(String, index=True)
    role = Column(String(50), nullable=False)  # UserRole.value
    is_active = Column(Boolean, default=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    last_login = Column(DateTime)

    __table_args__ = (
        sa.UniqueConstraint("username"),
        Index("ix_user_accounts_username", "username"),
    )


class APIKeyRecord(Base):
    """Database model for persisted API keys (hashed secret, never store raw key)."""

    __tablename__ = "api_keys"

    id = Column(String, primary_key=True)  # public key id (embedded in raw key)
    user_id = Column(String, ForeignKey("user_accounts.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    secret_hash = Column(String, nullable=False)
    permissions = Column(JSONB)  # list[str] of Permission values
    created_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime)
    last_used = Column(DateTime)
    is_active = Column(Boolean, default=True, index=True)


class UnitCategory(Base):
    """Database model for unit categories."""

    __tablename__ = "unit_categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)  # mass, energy, volume, etc.
    base_unit = Column(String(120), nullable=False)  # kg, J, semantic units, etc.
    description = Column(Text)
    special_conversions = Column(Boolean, default=False)  # For temperature, etc.
    created_at = Column(DateTime, default=func.now())

    # Relationships
    units = relationship(
        "Unit", back_populates="category", cascade="all, delete-orphan"
    )


class Unit(Base):
    """Database model for units."""

    __tablename__ = "units"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("unit_categories.id"), nullable=False)
    symbol = Column(String(120), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    conversion_factor = Column(Numeric(40, 18), nullable=False)
    conversion_offset = Column(
        Numeric(40, 18), default=0
    )  # For temperature conversions
    aliases = Column(ARRAY(String))  # Array of aliases
    unit_metadata = Column(JSONB)
    dimension_vector = Column(JSONB)
    source_system = Column(String(100))
    source_version = Column(String(100))
    valid_from = Column(Date)
    valid_to = Column(Date)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    category = relationship("UnitCategory", back_populates="units")

    # Indexes
    __table_args__ = (
        Index("ix_units_symbol", "symbol"),
        Index("ix_units_category_id", "category_id"),
    )


class ConversionRule(Base):
    """Database model for custom conversion rules."""

    __tablename__ = "conversion_rules"

    id = Column(Integer, primary_key=True)
    from_unit = Column(String(120), nullable=False)
    to_unit = Column(String(120), nullable=False)
    formula = Column(Text, nullable=False)  # Formula for conversion
    reverse_formula = Column(Text)  # Reverse formula if different
    description = Column(Text)
    conditions = Column(JSONB)  # Conditions for rule application
    rule_metadata = Column(JSONB)
    rule_hash = Column(String(64), index=True)
    priority = Column(Integer, nullable=False, default=100, server_default="100")
    valid_from = Column(Date)
    valid_to = Column(Date)
    source_system = Column(String(100))
    source_version = Column(String(100))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())

    # Indexes
    __table_args__ = (Index("ix_conversion_rules_from_to", "from_unit", "to_unit"),)


class Currency(Base):
    __tablename__ = "currencies"

    code = Column(String(3), primary_key=True)
    numeric_code = Column(String(3), index=True)
    name = Column(String(200), nullable=False)
    minor_units = Column(Integer, nullable=False)
    valid_from = Column(Date)
    valid_to = Column(Date)
    redenomination_of = Column(String(3))
    redenomination_factor = Column(Numeric(30, 12))
    currency_metadata = Column(JSONB)
    is_active = Column(Boolean, nullable=False, default=True, server_default=sa.true())
    created_at = Column(DateTime, default=func.now(), nullable=False)


class FXRateBatch(Base):
    __tablename__ = "fx_rate_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(100), nullable=False, index=True)
    source_name = Column(String(200))
    source_url = Column(String(500))
    rate_type = Column(String(50), nullable=False, index=True)
    base_currency = Column(String(3), nullable=False, index=True)
    source_hash = Column(String(64), nullable=False, index=True)
    imported_at = Column(DateTime, default=func.now(), nullable=False)
    created_by = Column(String(100))
    batch_metadata = Column(JSONB)


class FXRateObservation(Base):
    __tablename__ = "fx_rate_observations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(
        Integer, ForeignKey("fx_rate_batches.id"), nullable=False, index=True
    )
    base_currency = Column(String(3), nullable=False, index=True)
    quote_currency = Column(String(3), nullable=False, index=True)
    rate_date = Column(Date, nullable=False, index=True)
    rate_value = Column(Numeric(30, 12), nullable=False)
    provider = Column(String(100), nullable=False, index=True)
    rate_type = Column(String(50), nullable=False, index=True)
    source_hash = Column(String(64), nullable=False, index=True)
    observed_at = Column(DateTime)
    rate_metadata = Column(JSONB)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint(
            "provider",
            "rate_type",
            "base_currency",
            "quote_currency",
            "rate_date",
            name="uq_fx_rate_observation_key",
        ),
        Index(
            "ix_fx_rate_observations_lookup",
            "provider",
            "rate_type",
            "base_currency",
            "quote_currency",
            "rate_date",
        ),
    )


class FXRatePeriod(Base):
    __tablename__ = "fx_rate_periods"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(100), nullable=False, index=True)
    rate_type = Column(String(50), nullable=False, index=True)
    base_currency = Column(String(3), nullable=False, index=True)
    quote_currency = Column(String(3), nullable=False, index=True)
    period_type = Column(String(20), nullable=False, index=True)
    period_start = Column(Date, nullable=False, index=True)
    period_end = Column(Date, nullable=False)
    rate_value = Column(Numeric(30, 12), nullable=False)
    source_observation_ids = Column(JSONB)
    source_hash = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint(
            "provider",
            "rate_type",
            "base_currency",
            "quote_currency",
            "period_type",
            "period_start",
            "period_end",
            name="uq_fx_rate_period_key",
        ),
        Index(
            "ix_fx_rate_periods_lookup",
            "provider",
            "rate_type",
            "base_currency",
            "quote_currency",
            "period_type",
            "period_start",
            "period_end",
        ),
    )


class FXPolicy(Base):
    __tablename__ = "fx_policies"

    id = Column(String(100), primary_key=True)
    name = Column(String(200), nullable=False)
    provider = Column(String(100), nullable=False)
    rate_type = Column(String(50), nullable=False)
    selection_mode = Column(String(50), nullable=False)
    business_day_rule = Column(String(50), nullable=False, default="previous_available")
    triangulation_allowed = Column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    fallback_behavior = Column(
        String(50), nullable=False, default="fail_closed", server_default="fail_closed"
    )
    rounding_scale = Column(Integer, nullable=False, default=6, server_default="6")
    rounding_mode = Column(
        String(50),
        nullable=False,
        default="ROUND_HALF_UP",
        server_default="ROUND_HALF_UP",
    )
    policy_metadata = Column(JSONB)
    is_active = Column(Boolean, nullable=False, default=True, server_default=sa.true())
    created_at = Column(DateTime, default=func.now(), nullable=False)


# Canonical semantic data now lives in PostgreSQL alongside the operational catalog.


class MetricType(Base):
    """Database model for metric types and aggregation rules."""

    __tablename__ = "metric_types"

    id = Column(Integer, primary_key=True)
    name = Column(
        String(50), unique=True, nullable=False
    )  # emissions, energy, water, etc.
    aggregation_method = Column(
        String(20), nullable=False
    )  # SUM, WEIGHTED_AVERAGE, etc.
    default_unit = Column(String(20))
    description = Column(Text)
    requires_weights = Column(Boolean, default=False)
    weight_variable = Column(String(100))  # Variable to use as weight
    metric_metadata = Column(JSONB)
    created_at = Column(DateTime, default=func.now())


# =============================================================================
# E5 Integration Models (E1/E2/E6 -> E5)
# =============================================================================


class Indicator(Base):
    """Database model for E1 ESRS/GRI indicators from dataset register."""

    __tablename__ = "indicators"

    id = Column(String, primary_key=True)
    identifier = Column(String, nullable=False)  # URN: urn:sds:reg:N
    title = Column(String, nullable=False)
    indicator_name = Column(String)  # "indicator" column from CSV
    description = Column(Text)
    dimension = Column(String(20), nullable=False, index=True)  # E, S, G, Transversal
    unit_name = Column(String(120))  # Some official unit labels exceed 50 chars
    unit_type = Column(String(120))  # Some official unit labels exceed 50 chars
    periodicity = Column(String(80))  # Official reporting-frequency labels can be long
    period_type = Column(String(80))  # Official period-type labels can be long
    source_ref = Column(Text)  # ESRS/GRI source references
    code_esrs = Column(
        String(150), index=True
    )  # Compiled ESRS child codes can be long.
    code_gri = Column(
        String(150), index=True
    )  # e.g., GRI 2-2.a (can be long compound refs)
    code_gri_expanded = Column(
        String(200)
    )  # Expanded GRI reference (can be long compound refs)
    evidence_path = Column(String)
    source_row = Column(Integer)
    owner = Column(String(100))
    access_rights = Column(String(50))  # Internal, Public, etc.
    validation_method = Column(String(100))
    double_materiality = Column(String(50))  # impact=X;financial=Y
    value_type = Column(String(20))  # Numeric, Text, Boolean
    concept_state = Column(
        CONCEPT_STATE_ENUM,
        nullable=False,
        default=ConceptState.CATALOGUED,
        server_default=ConceptState.CATALOGUED.value,
    )
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        sa.UniqueConstraint("identifier"),
        Index("ix_indicators_identifier", "identifier"),
        Index("ix_indicators_dimension_esrs", "dimension", "code_esrs"),
        Index("ix_indicators_dimension_gri", "dimension", "code_gri"),
    )


class StandardMapping(Base):
    """Database model for standard-agnostic crosswalk mappings between sustainability standards."""

    __tablename__ = "standard_mappings"

    id = Column(Integer, primary_key=True)

    # Source standard (generic)
    source_standard = Column(String(50), nullable=False, index=True)
    source_code = Column(String(150), nullable=False, index=True)
    source_label = Column(String(500))

    # Target standard (generic)
    target_standard = Column(String(50), nullable=False, index=True)
    target_code = Column(String(150), index=True)
    target_label = Column(String(500))

    # Mapping metadata
    esg_dimension = Column(String(20), index=True)  # E, S, G, Transversal
    relationship_type = Column(String(30), default="equivalent")
    confidence = Column(Numeric(3, 2), default=1.0)

    # Traceability
    dataset = Column(String(100))
    source_row = Column(Integer)
    mapping_metadata = Column(JSONB)

    # Audit fields
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(String(100))

    __table_args__ = (
        Index("ix_standard_mappings_source", "source_standard", "source_code"),
        Index("ix_standard_mappings_target", "target_standard", "target_code"),
        Index(
            "ix_standard_mappings_standards_pair", "source_standard", "target_standard"
        ),
        Index("ix_standard_mappings_dimension", "esg_dimension"),
    )


class DatasetSnapshot(Base):
    """Persistent version/audit snapshot for exported catalog datasets."""

    __tablename__ = "dataset_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset = Column(String(50), nullable=False)
    manifest_hash = Column(String(64), nullable=False)
    record_count = Column(Integer, nullable=False)
    contract_version = Column(String(20), nullable=False)
    item_index = Column(JSONB, nullable=False)
    source_ref = Column(String(500))
    source_hash = Column(String(64))
    created_by = Column(String(100))
    created_at = Column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_dataset_snapshots_dataset_created_at", "dataset", "created_at"),
        Index(
            "ix_dataset_snapshots_dataset_manifest_hash",
            "dataset",
            "manifest_hash",
            unique=True,
        ),
        Index("ix_dataset_snapshots_feed_order", "created_at", "dataset", "id"),
    )


class SustainabilityStandard(Base):
    """Registry of supported sustainability reporting standards."""

    __tablename__ = "sustainability_standards"

    id = Column(String(20), primary_key=True)
    name = Column(String(200), nullable=False)
    organization = Column(String(200))
    version = Column(String(50))
    url = Column(String(500))
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())


class Concept(Base):
    """Canonical semantic concept projected from the SDS catalog."""

    __tablename__ = "concepts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uri = Column(String(500), nullable=False)
    indicator_id = Column(String, ForeignKey("indicators.id"), index=True)
    label = Column(String(500), nullable=False)
    description = Column(Text)
    taxonomy = Column(String(50), nullable=False, index=True)
    concept_type = Column(String(50), nullable=False, index=True)
    unit = Column(String(50))
    temporal_granularity = Column(String(30))
    hierarchy_level = Column(Integer)
    concept_state = Column(
        CONCEPT_STATE_ENUM,
        nullable=False,
        default=ConceptState.CATALOGUED,
        server_default=ConceptState.CATALOGUED.value,
        index=True,
    )
    projection_source = Column(String(80))
    projection_hash = Column(String(64))
    projection_version = Column(String(80))
    projection_metadata = Column(JSONB)
    projected_at = Column(DateTime)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    indicator = relationship("Indicator", backref="concepts")
    formulas = relationship(
        "ConceptFormula", back_populates="concept", cascade="all, delete-orphan"
    )
    variables = relationship(
        "ConceptVariable", back_populates="concept", cascade="all, delete-orphan"
    )
    equivalences = relationship(
        "ConceptEquivalence",
        back_populates="source_concept",
        cascade="all, delete-orphan",
    )
    indicator_links = relationship(
        "ConceptIndicatorLink", back_populates="concept", cascade="all, delete-orphan"
    )

    __table_args__ = (
        sa.UniqueConstraint("uri"),
        Index("ix_concepts_uri", "uri"),
        Index("ix_concepts_taxonomy_type", "taxonomy", "concept_type"),
        Index("ix_concepts_indicator_state", "indicator_id", "concept_state"),
        Index("ix_concepts_projection_source", "projection_source"),
    )


class ConceptFormula(Base):
    """Canonical formula record for calculable concepts."""

    __tablename__ = "concept_formulas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    concept_id = Column(Integer, ForeignKey("concepts.id"), nullable=False, index=True)
    expression = Column(Text, nullable=False)
    expression_language = Column(String(30), nullable=False, default="sds")
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    concept = relationship("Concept", back_populates="formulas")

    __table_args__ = (
        Index("ix_concept_formulas_concept_active", "concept_id", "is_active"),
    )


class ConceptVariable(Base):
    """Dependency variable records for a calculable concept."""

    __tablename__ = "concept_variables"

    id = Column(Integer, primary_key=True, autoincrement=True)
    concept_id = Column(Integer, ForeignKey("concepts.id"), nullable=False, index=True)
    variable_uri = Column(String(500), nullable=False, index=True)
    variable_label = Column(String(500))
    ordering = Column(Integer, nullable=False, default=0)
    aggregation_method = Column(String(30), nullable=False, default="SUM")
    temporal_granularity = Column(String(30))
    created_at = Column(DateTime, default=func.now())

    concept = relationship("Concept", back_populates="variables")

    __table_args__ = (
        Index("ix_concept_variables_concept_ordering", "concept_id", "ordering"),
    )


class ConceptEquivalence(Base):
    """Explicit equivalence or relatedness links between canonical concepts."""

    __tablename__ = "concept_equivalences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_concept_id = Column(
        Integer, ForeignKey("concepts.id"), nullable=False, index=True
    )
    target_uri = Column(String(500), nullable=False, index=True)
    target_taxonomy = Column(String(50), index=True)
    relationship_type = Column(String(30), nullable=False, default="equivalent")
    confidence = Column(Numeric(3, 2), nullable=False, default=1.0)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    source_concept = relationship("Concept", back_populates="equivalences")

    __table_args__ = (
        Index(
            "ix_concept_equivalences_source_relationship",
            "source_concept_id",
            "relationship_type",
        ),
    )


class ConceptIndicatorLink(Base):
    """Deterministic linkage between semantic concepts and catalog indicators."""

    __tablename__ = "concept_indicator_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    concept_id = Column(Integer, ForeignKey("concepts.id"), nullable=False, index=True)
    indicator_id = Column(
        String, ForeignKey("indicators.id"), nullable=False, index=True
    )
    link_type = Column(String(50), nullable=False, index=True)
    confidence = Column(Numeric(3, 2), nullable=False, default=1.0)
    rationale = Column(Text)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    concept = relationship("Concept", back_populates="indicator_links")
    indicator = relationship("Indicator", backref="concept_links")

    __table_args__ = (
        Index(
            "ix_concept_indicator_links_unique",
            "concept_id",
            "indicator_id",
            "link_type",
            unique=True,
        ),
        Index("ix_concept_indicator_links_concept_type", "concept_id", "link_type"),
    )


# =============================================================================
# Canonical Concept Tables (immutable, append-only, revisioned)
# =============================================================================


class CanonicalConcept(Base):
    """Immutable canonical semantic concept — source of truth for projections."""

    __tablename__ = "canonical_concepts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_uri = Column(String(500), nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    effective_from = Column(DateTime, nullable=False, default=func.now())
    effective_to = Column(DateTime, nullable=True)
    superseded_by = Column(Integer, ForeignKey("canonical_concepts.id"), nullable=True)
    indicator_id = Column(String, ForeignKey("indicators.id"), index=True)
    label = Column(String(500), nullable=False)
    description = Column(Text)
    taxonomy = Column(String(50), nullable=False, index=True)
    concept_type = Column(String(50), nullable=False, index=True)
    unit = Column(String(50))
    temporal_granularity = Column(String(30))
    hierarchy_level = Column(Integer)
    concept_state = Column(
        CONCEPT_STATE_ENUM,
        nullable=False,
        default=ConceptState.CATALOGUED,
        server_default=ConceptState.CATALOGUED.value,
    )
    projection_source = Column(String(80))
    projection_hash = Column(String(64))
    projection_version = Column(String(80))
    projection_metadata = Column(JSONB)
    projected_at = Column(DateTime)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    created_by = Column(String(100))

    formulas = relationship(
        "CanonicalConceptFormula",
        back_populates="concept",
        cascade="all, delete-orphan",
    )
    variables = relationship(
        "CanonicalConceptVariable",
        back_populates="concept",
        cascade="all, delete-orphan",
    )
    equivalences = relationship(
        "CanonicalConceptEquivalence",
        back_populates="concept",
        cascade="all, delete-orphan",
    )
    indicator_links = relationship(
        "CanonicalConceptIndicatorLink",
        back_populates="concept",
        cascade="all, delete-orphan",
    )
    mapping_components = relationship(
        "MappingAssertionComponent",
        back_populates="canonical_concept",
    )

    __table_args__ = (
        Index("ix_canonical_concepts_uri", "canonical_uri"),
        Index(
            "ix_canonical_concepts_current",
            "canonical_uri",
            "effective_to",
            postgresql_where=text("effective_to IS NULL"),
        ),
        Index("ix_canonical_concepts_concept_state", "concept_state"),
        Index("ix_canonical_concepts_taxonomy_type", "taxonomy", "concept_type"),
        Index("ix_canonical_concepts_indicator_state", "indicator_id", "concept_state"),
        Index("ix_canonical_concepts_projection_source", "projection_source"),
        sa.UniqueConstraint(
            "canonical_uri", "revision", name="uq_canonical_concepts_uri_revision"
        ),
    )


class CanonicalConceptFormula(Base):
    """Canonical formula record for calculable concepts (immutable)."""

    __tablename__ = "canonical_concept_formulas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_concept_id = Column(
        Integer, ForeignKey("canonical_concepts.id"), nullable=False
    )
    expression = Column(Text, nullable=False)
    expression_language = Column(String(30), nullable=False, default="sds")
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    concept = relationship("CanonicalConcept", back_populates="formulas")

    __table_args__ = (
        Index(
            "ix_canonical_concept_formulas_concept_id",
            "canonical_concept_id",
        ),
        Index(
            "ix_canonical_concept_formulas_concept_active",
            "canonical_concept_id",
            "is_active",
        ),
    )


class CanonicalConceptVariable(Base):
    """Dependency variable records for a calculable concept (immutable)."""

    __tablename__ = "canonical_concept_variables"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_concept_id = Column(
        Integer, ForeignKey("canonical_concepts.id"), nullable=False
    )
    variable_uri = Column(String(500), nullable=False, index=True)
    variable_label = Column(String(500))
    ordering = Column(Integer, nullable=False, default=0)
    aggregation_method = Column(String(30), nullable=False, default="SUM")
    temporal_granularity = Column(String(30))
    created_at = Column(DateTime, default=func.now(), nullable=False)

    concept = relationship("CanonicalConcept", back_populates="variables")

    __table_args__ = (
        Index(
            "ix_canonical_concept_variables_concept_id",
            "canonical_concept_id",
        ),
        Index(
            "ix_canonical_concept_variables_concept_ordering",
            "canonical_concept_id",
            "ordering",
        ),
    )


class CanonicalConceptEquivalence(Base):
    """Explicit equivalence or relatedness links between canonical concepts (immutable)."""

    __tablename__ = "canonical_concept_equivalences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_concept_id = Column(
        Integer, ForeignKey("canonical_concepts.id"), nullable=False
    )
    target_uri = Column(String(500), nullable=False, index=True)
    target_taxonomy = Column(String(50), index=True)
    relationship_type = Column(String(30), nullable=False, default="equivalent")
    confidence = Column(Numeric(3, 2), nullable=False, default=1.0)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    concept = relationship("CanonicalConcept", back_populates="equivalences")

    __table_args__ = (
        Index(
            "ix_canonical_concept_equivalences_concept_id",
            "canonical_concept_id",
        ),
        Index(
            "ix_canonical_concept_equivalences_source_relationship",
            "canonical_concept_id",
            "relationship_type",
        ),
    )


class CanonicalConceptIndicatorLink(Base):
    """Deterministic linkage between canonical concepts and catalog indicators (immutable)."""

    __tablename__ = "canonical_concept_indicator_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_concept_id = Column(
        Integer, ForeignKey("canonical_concepts.id"), nullable=False
    )
    indicator_id = Column(
        String, ForeignKey("indicators.id"), nullable=False, index=True
    )
    link_type = Column(String(50), nullable=False)
    confidence = Column(Numeric(3, 2), nullable=False, default=1.0)
    rationale = Column(Text)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    concept = relationship("CanonicalConcept", back_populates="indicator_links")
    indicator = relationship("Indicator", backref="canonical_concept_links")

    __table_args__ = (
        sa.UniqueConstraint(
            "canonical_concept_id",
            "indicator_id",
            "link_type",
            name="uq_canonical_concept_indicator_links",
        ),
        Index(
            "ix_canonical_concept_indicator_links_concept_id",
            "canonical_concept_id",
        ),
        Index(
            "ix_canonical_concept_indicator_links_concept_type",
            "canonical_concept_id",
            "link_type",
        ),
    )


class StandardRelease(Base):
    """Versioned release of an external sustainability reporting standard."""

    __tablename__ = "standard_releases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    standard_id = Column(String(50), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    version = Column(String(100), nullable=False)
    release_date = Column(Date)
    source_url = Column(String(500))
    lifecycle_status = Column(
        String(30), nullable=False, default="active", server_default="active"
    )
    provenance = Column(JSONB)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    created_by = Column(String(100))

    datapoints = relationship(
        "StandardDatapoint",
        back_populates="standard_release",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "standard_id", "version", name="uq_standard_releases_standard_version"
        ),
        Index(
            "ix_standard_releases_standard_status", "standard_id", "lifecycle_status"
        ),
    )


class StandardDatapoint(Base):
    """Stable datapoint identity inside a specific external standard release."""

    __tablename__ = "standard_datapoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    standard_release_id = Column(
        Integer, ForeignKey("standard_releases.id"), nullable=False, index=True
    )
    code = Column(String(150), nullable=False, index=True)
    label = Column(String(500), nullable=False)
    disclosure_text = Column(Text)
    datapoint_type = Column(String(50))
    unit = Column(String(100))
    lifecycle_status = Column(
        String(30), nullable=False, default="active", server_default="active"
    )
    metadata_json = Column(JSONB)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    created_by = Column(String(100))

    standard_release = relationship("StandardRelease", back_populates="datapoints")
    mapping_assertion_groups = relationship(
        "MappingAssertionGroup",
        back_populates="source_datapoint",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "standard_release_id", "code", name="uq_standard_datapoints_release_code"
        ),
        Index(
            "ix_standard_datapoints_release_status",
            "standard_release_id",
            "lifecycle_status",
        ),
    )


class MappingAssertionGroup(Base):
    """Curated claim mapping one external datapoint to a Sygris canonical footprint."""

    __tablename__ = "mapping_assertion_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_datapoint_id = Column(
        Integer, ForeignKey("standard_datapoints.id"), nullable=False, index=True
    )
    mapping_profile = Column(
        String(100), nullable=False, default="default", server_default="default"
    )
    relationship_type = Column(
        String(30), nullable=False, default="equivalent", server_default="equivalent"
    )
    coverage_status = Column(
        String(30), nullable=False, default="complete", server_default="complete"
    )
    confidence = Column(
        Numeric(3, 2), nullable=False, default=1.0, server_default="1.0"
    )
    rationale = Column(Text)
    coverage_summary = Column(Text)
    difference_summary = Column(Text)
    valid_from = Column(DateTime, nullable=False, default=func.now())
    valid_to = Column(DateTime)
    approval_status = Column(
        String(30), nullable=False, default="draft", server_default="draft"
    )
    publication_status = Column(
        String(30), nullable=False, default="internal", server_default="internal"
    )
    assertion_hash = Column(String(64))
    package_snapshot_id = Column(Integer, ForeignKey("dataset_snapshots.id"))
    superseded_by = Column(Integer, ForeignKey("mapping_assertion_groups.id"))
    provenance = Column(JSONB)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    created_by = Column(String(100))

    source_datapoint = relationship(
        "StandardDatapoint", back_populates="mapping_assertion_groups"
    )
    components = relationship(
        "MappingAssertionComponent",
        back_populates="assertion_group",
        cascade="all, delete-orphan",
    )
    package_snapshot = relationship(
        "DatasetSnapshot", foreign_keys=[package_snapshot_id]
    )
    superseded_by_group = relationship("MappingAssertionGroup", remote_side=[id])

    __table_args__ = (
        Index(
            "ix_mapping_assertion_groups_source_profile_status",
            "source_datapoint_id",
            "mapping_profile",
            "approval_status",
        ),
        Index(
            "ix_mapping_assertion_groups_current_approved",
            "source_datapoint_id",
            "mapping_profile",
            unique=True,
            postgresql_where=text("valid_to IS NULL AND approval_status = 'approved'"),
        ),
        Index("ix_mapping_assertion_groups_assertion_hash", "assertion_hash"),
    )


class MappingAssertionComponent(Base):
    """One Sygris component inside a mapping assertion footprint."""

    __tablename__ = "mapping_assertion_components"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assertion_group_id = Column(
        Integer, ForeignKey("mapping_assertion_groups.id"), nullable=False, index=True
    )
    canonical_concept_id = Column(
        Integer, ForeignKey("canonical_concepts.id"), nullable=False, index=True
    )
    component_order = Column(Integer, nullable=False, default=0, server_default="0")
    component_role = Column(
        String(30), nullable=False, default="primary", server_default="primary"
    )
    coverage_fraction = Column(Numeric(5, 4))
    match_scope = Column(Text)
    mismatch_scope = Column(Text)
    transformation_rule = Column(Text)
    rationale = Column(Text)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    assertion_group = relationship("MappingAssertionGroup", back_populates="components")
    canonical_concept = relationship(
        "CanonicalConcept", back_populates="mapping_components"
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "assertion_group_id",
            "component_order",
            name="uq_mapping_assertion_components_group_order",
        ),
        Index(
            "ix_mapping_assertion_components_group_concept",
            "assertion_group_id",
            "canonical_concept_id",
        ),
    )


class MaterializedPairwiseMapping(Base):
    """Generated cross-standard mapping row derived from Sygris assertions."""

    __tablename__ = "materialized_pairwise_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_datapoint_id = Column(
        Integer, ForeignKey("standard_datapoints.id"), nullable=False, index=True
    )
    target_datapoint_id = Column(
        Integer, ForeignKey("standard_datapoints.id"), nullable=False, index=True
    )
    source_assertion_group_id = Column(
        Integer, ForeignKey("mapping_assertion_groups.id"), nullable=False, index=True
    )
    target_assertion_group_id = Column(
        Integer, ForeignKey("mapping_assertion_groups.id"), nullable=False, index=True
    )
    source_standard = Column(String(50), nullable=False, index=True)
    source_code = Column(String(150), nullable=False, index=True)
    target_standard = Column(String(50), nullable=False, index=True)
    target_code = Column(String(150), nullable=False, index=True)
    relationship_type = Column(
        String(30), nullable=False, default="derived", server_default="derived"
    )
    match_strength = Column(
        Numeric(3, 2), nullable=False, default=1.0, server_default="1.0"
    )
    derivation_method = Column(
        String(50),
        nullable=False,
        default="sygris_footprint_overlap",
        server_default="sygris_footprint_overlap",
    )
    coverage_summary = Column(Text)
    difference_summary = Column(Text)
    generated_snapshot_id = Column(Integer, ForeignKey("dataset_snapshots.id"))
    generated_from_hash = Column(String(64), nullable=False, index=True)
    generated_at = Column(DateTime, default=func.now(), nullable=False)
    is_current = Column(Boolean, nullable=False, default=True, server_default=sa.true())
    stale_reason = Column(Text)
    metadata_json = Column(JSONB)

    source_datapoint = relationship(
        "StandardDatapoint", foreign_keys=[source_datapoint_id]
    )
    target_datapoint = relationship(
        "StandardDatapoint", foreign_keys=[target_datapoint_id]
    )
    source_assertion_group = relationship(
        "MappingAssertionGroup", foreign_keys=[source_assertion_group_id]
    )
    target_assertion_group = relationship(
        "MappingAssertionGroup", foreign_keys=[target_assertion_group_id]
    )
    generated_snapshot = relationship(
        "DatasetSnapshot", foreign_keys=[generated_snapshot_id]
    )

    __table_args__ = (
        Index(
            "ix_materialized_pairwise_mappings_pair_current",
            "source_datapoint_id",
            "target_datapoint_id",
            unique=True,
            postgresql_where=text("is_current IS TRUE"),
        ),
        Index(
            "ix_materialized_pairwise_mappings_standards",
            "source_standard",
            "target_standard",
            "is_current",
        ),
        Index(
            "ix_materialized_pairwise_mappings_source_lookup",
            "source_standard",
            "source_code",
            "is_current",
        ),
        Index(
            "ix_materialized_pairwise_mappings_target_lookup",
            "target_standard",
            "target_code",
            "is_current",
        ),
    )


class AtomizerPackageImport(Base):
    """Audit record for an Atomizer package import into SDS."""

    __tablename__ = "atomizer_package_imports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    package_id = Column(String(200), index=True)
    package_version = Column(String(100))
    package_hash = Column(String(64), nullable=False)
    manifest_hash = Column(String(64), index=True)
    register_sha256 = Column(String(64))
    calculation_contract_sha256 = Column(String(64))
    values_sha256 = Column(String(64))
    import_mode = Column(String(30), nullable=False, default="apply")
    status = Column(String(30), nullable=False, default="running", index=True)
    register_row_count = Column(Integer, nullable=False, default=0)
    contract_node_count = Column(Integer, nullable=False, default=0)
    executable_contract_count = Column(Integer, nullable=False, default=0)
    semantic_only_count = Column(Integer, nullable=False, default=0)
    audit_only_count = Column(Integer, nullable=False, default=0)
    result_json = Column(JSONB)
    source_package = Column(JSONB)
    file_hashes = Column(JSONB)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at = Column(DateTime)
    created_by = Column(String(100))

    calculation_contracts = relationship(
        "CanonicalCalculationContract",
        back_populates="package_import",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        sa.UniqueConstraint("package_hash", name="uq_atomizer_package_imports_hash"),
        Index("ix_atomizer_package_imports_package_hash", "package_hash"),
        Index("ix_atomizer_package_imports_manifest_hash", "manifest_hash"),
        Index("ix_atomizer_package_imports_status_created", "status", "created_at"),
    )


class CanonicalCalculationContract(Base):
    """Versioned canonical calculation contract imported from Atomizer."""

    __tablename__ = "canonical_calculation_contracts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    package_import_id = Column(
        Integer, ForeignKey("atomizer_package_imports.id"), nullable=False, index=True
    )
    contract_version = Column(String(100), nullable=False)
    model_id = Column(String(200), nullable=False, index=True)
    node_id = Column(String(500), nullable=False, index=True)
    canonical_datapoint_id = Column(String(500), index=True)
    indicator_id = Column(String, ForeignKey("indicators.id"), index=True)
    indicator_identifier = Column(String(500), index=True)
    label = Column(String(500), nullable=False)
    exposure = Column(String(50), nullable=False, index=True)
    runtime_status = Column(String(50), nullable=False, index=True)
    role = Column(String(50))
    source_kind = Column(String(80))
    formula_kind = Column(String(50))
    semantic_expression = Column(Text)
    runtime_expression = Column(Text)
    value_kind = Column(String(50))
    unit_name = Column(String(120))
    unit_type = Column(String(120))
    result_currency = Column(String(3))
    conversion_policy = Column(
        String(50),
        nullable=False,
        default="fail_closed",
        server_default="fail_closed",
    )
    parent_node_id = Column(String(500), index=True)
    relation_to_parent = Column(Text)
    aggregation_policy = Column(JSONB)
    completeness_policy = Column(JSONB)
    gate_ids = Column(JSONB)
    support_rule_ids = Column(JSONB)
    evidence = Column(JSONB)
    provenance = Column(JSONB)
    source_payload = Column(JSONB)
    effective_from = Column(DateTime, default=func.now(), nullable=False)
    effective_to = Column(DateTime)
    is_active = Column(Boolean, nullable=False, default=True, server_default=sa.true())
    contract_hash = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    created_by = Column(String(100))

    package_import = relationship(
        "AtomizerPackageImport", back_populates="calculation_contracts"
    )
    indicator = relationship("Indicator", backref="calculation_contracts")
    components = relationship(
        "CanonicalCalculationComponent",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    dimensions = relationship(
        "CanonicalCalculationDimension",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    gates = relationship(
        "CanonicalCalculationGate",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    support_rules = relationship(
        "CanonicalCalculationSupportRule",
        back_populates="contract",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "ix_canonical_calc_contracts_active_indicator",
            "indicator_id",
            unique=True,
            postgresql_where=text("is_active IS TRUE AND indicator_id IS NOT NULL"),
        ),
        Index(
            "ix_canonical_calc_contracts_active_node",
            "node_id",
            unique=True,
            postgresql_where=text("is_active IS TRUE"),
        ),
        Index(
            "ix_canonical_calc_contracts_active_datapoint_lookup",
            "canonical_datapoint_id",
            postgresql_where=text(
                "is_active IS TRUE AND canonical_datapoint_id IS NOT NULL"
            ),
        ),
        Index(
            "ix_canonical_calc_contracts_active_model_datapoint",
            "model_id",
            "canonical_datapoint_id",
            unique=True,
            postgresql_where=text(
                "is_active IS TRUE AND canonical_datapoint_id IS NOT NULL"
            ),
        ),
        Index(
            "ix_canonical_calc_contracts_active_hash",
            "contract_hash",
            unique=True,
            postgresql_where=text("is_active IS TRUE"),
        ),
    )


class CanonicalCalculationComponent(Base):
    """Component reference for a canonical calculation contract."""

    __tablename__ = "canonical_calculation_components"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(
        Integer,
        ForeignKey("canonical_calculation_contracts.id"),
        nullable=False,
        index=True,
    )
    package_import_id = Column(
        Integer, ForeignKey("atomizer_package_imports.id"), nullable=False, index=True
    )
    component_order = Column(Integer, nullable=False, default=0, server_default="0")
    component_id = Column(String(500), nullable=False, index=True)
    component_node_id = Column(String(500), index=True)
    indicator_identifier = Column(String(500), index=True)
    component_scope = Column(String(80))
    variable_uri = Column(String(500))
    unit_name = Column(String(120))
    unit_type = Column(String(120))
    currency = Column(String(3))
    expected_currency = Column(String(3))
    conversion_policy = Column(
        String(50),
        nullable=False,
        default="fail_closed",
        server_default="fail_closed",
    )
    fx_policy_id = Column(String(100), ForeignKey("fx_policies.id"))
    aggregation_method = Column(String(50))
    role = Column(String(50))
    required = Column(Boolean, nullable=False, default=True, server_default=sa.true())
    numerator_denominator_role = Column(String(50))
    weight_hint = Column(String(200))
    source_payload = Column(JSONB)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    contract = relationship("CanonicalCalculationContract", back_populates="components")
    package_import = relationship("AtomizerPackageImport")

    __table_args__ = (
        sa.UniqueConstraint(
            "contract_id",
            "component_order",
            name="uq_canonical_calc_components_contract_order",
        ),
        Index(
            "ix_canonical_calc_components_component",
            "component_id",
            "component_node_id",
        ),
    )


class CanonicalCalculationDimension(Base):
    """Dimension constraint attached to a calculation contract."""

    __tablename__ = "canonical_calculation_dimensions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(
        Integer,
        ForeignKey("canonical_calculation_contracts.id"),
        nullable=False,
        index=True,
    )
    package_import_id = Column(
        Integer, ForeignKey("atomizer_package_imports.id"), nullable=False, index=True
    )
    dimension_id = Column(String(200), nullable=False, index=True)
    mode = Column(String(50), nullable=False)
    fixed_value = Column(String(500))
    member_values = Column(JSONB)
    required = Column(Boolean, nullable=False, default=False, server_default=sa.false())
    rationale = Column(Text)
    source_payload = Column(JSONB)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    contract = relationship("CanonicalCalculationContract", back_populates="dimensions")
    package_import = relationship("AtomizerPackageImport")

    __table_args__ = (
        Index(
            "ix_canonical_calc_dimensions_contract_dimension",
            "contract_id",
            "dimension_id",
        ),
    )


class CanonicalCalculationGate(Base):
    """Structured gate evidence imported with calculation contracts."""

    __tablename__ = "canonical_calculation_gates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    package_import_id = Column(
        Integer, ForeignKey("atomizer_package_imports.id"), nullable=False, index=True
    )
    contract_id = Column(
        Integer, ForeignKey("canonical_calculation_contracts.id"), index=True
    )
    gate_id = Column(String(200), nullable=False, index=True)
    title = Column(String(500))
    payload = Column(JSONB)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    contract = relationship("CanonicalCalculationContract", back_populates="gates")
    package_import = relationship("AtomizerPackageImport")


class CanonicalCalculationSupportRule(Base):
    """Structured support rule imported with calculation contracts."""

    __tablename__ = "canonical_calculation_support_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    package_import_id = Column(
        Integer, ForeignKey("atomizer_package_imports.id"), nullable=False, index=True
    )
    contract_id = Column(
        Integer, ForeignKey("canonical_calculation_contracts.id"), index=True
    )
    support_rule_id = Column(String(200), nullable=False, index=True)
    title = Column(String(500))
    payload = Column(JSONB)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    contract = relationship(
        "CanonicalCalculationContract", back_populates="support_rules"
    )
    package_import = relationship("AtomizerPackageImport")


class ValueContext(Base):
    """Stable identity for a tenant value observation across revisions."""

    __tablename__ = "value_contexts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(100), nullable=False, index=True)
    context_hash_recipe_version = Column(String(50), nullable=False)
    context_hash = Column(String(64), nullable=False, index=True)
    entity_id = Column(String(200), nullable=False, index=True)
    reporting_period_id = Column(String(100), nullable=False, index=True)
    period_start = Column(Date)
    period_end = Column(Date)
    period_close_date = Column(Date)
    period_type = Column(String(50), nullable=False)
    reporting_boundary_id = Column(String(200), nullable=False)
    canonical_concept_id = Column(
        Integer, ForeignKey("canonical_concepts.id"), index=True
    )
    canonical_uri = Column(String(500), index=True)
    source_observation_type = Column(
        String(50),
        nullable=False,
        default="standard_direct_legacy",
        server_default="standard_direct_legacy",
    )
    sds_indicator_id = Column(String, ForeignKey("indicators.id"), index=True)
    indicator_identifier = Column(String(500), nullable=False, index=True)
    standard_release_id = Column(String(100), nullable=False, index=True)
    standard_datapoint_id = Column(String(200), nullable=False, index=True)
    dimensions_json = Column(JSONB)
    scenario_basis = Column(
        String(50), nullable=False, default="actual", server_default="actual"
    )
    value_kind = Column(String(50), nullable=False)
    expected_unit = Column(String(120))
    expected_currency = Column(String(3))
    identity_payload = Column(JSONB)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    created_by = Column(String(100))

    indicator = relationship("Indicator")
    canonical_concept = relationship("CanonicalConcept")
    revisions = relationship(
        "ValueRevision",
        back_populates="context",
        cascade="all, delete-orphan",
    )
    current_pointer = relationship(
        "CurrentValuePointer",
        back_populates="context",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        sa.CheckConstraint(
            "source_observation_type IN ("
            "'standard_direct_legacy', "
            "'canonical_operational', "
            "'standard_bound_evidence'"
            ")",
            name="ck_value_contexts_source_observation_type",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "context_hash",
            name="uq_value_contexts_tenant_hash",
        ),
        Index(
            "ix_value_contexts_indicator_period",
            "indicator_identifier",
            "reporting_period_id",
        ),
        Index(
            "ix_value_contexts_tenant_canonical_period",
            "tenant_id",
            "canonical_uri",
            "reporting_period_id",
        ),
        Index(
            "ix_value_contexts_canonical_concept_period",
            "canonical_concept_id",
            "reporting_period_id",
        ),
    )


class ValueRevision(Base):
    """Immutable append-only value revision for one value context."""

    __tablename__ = "value_revisions"

    id = Column(String(100), primary_key=True)
    context_id = Column(Integer, ForeignKey("value_contexts.id"), nullable=False)
    tenant_id = Column(String(100), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    state = Column(String(30), nullable=False, index=True)
    value_kind = Column(String(50), nullable=False)
    canonical_value = Column(Text)
    numeric_value = Column(Numeric(38, 12))
    text_value = Column(Text)
    boolean_value = Column(Boolean)
    unit = Column(String(120))
    currency = Column(String(3))
    original_value = Column(Text)
    original_unit = Column(String(120))
    original_currency = Column(String(3))
    source_system = Column(String(100), index=True)
    source_record_id = Column(String(100), index=True)
    external_key = Column(String(255), index=True)
    evidence_hash = Column(String(64), index=True)
    materiality_metadata = Column(JSONB)
    calculation_contract_id = Column(
        Integer, ForeignKey("canonical_calculation_contracts.id"), index=True
    )
    formula_contract_hash = Column(String(64), index=True)
    input_revision_ids = Column(JSONB)
    conversion_trace = Column(JSONB)
    trace_hash = Column(String(64), index=True)
    parent_revision_id = Column(String(100), ForeignKey("value_revisions.id"))
    revision_provenance = Column(String(80), index=True)
    source_payload_hash = Column(String(64), index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    created_by = Column(String(100))

    context = relationship("ValueContext", back_populates="revisions")
    calculation_contract = relationship("CanonicalCalculationContract")
    parent_revision = relationship("ValueRevision", remote_side=[id])

    __table_args__ = (
        sa.UniqueConstraint(
            "context_id",
            "revision_number",
            name="uq_value_revisions_context_revision",
        ),
        Index(
            "ix_value_revisions_source_observation",
            "tenant_id",
            "source_system",
            "external_key",
            "source_record_id",
        ),
    )


class ValueRevisionEvent(Base):
    """Monotonic append-only event emitted for revision and pointer changes."""

    __tablename__ = "value_revision_events"

    id = Column(String(100), primary_key=True)
    tenant_id = Column(String(100), nullable=False, index=True)
    event_seq = Column(BigInteger, nullable=False)
    context_event_seq = Column(Integer, nullable=False)
    context_id = Column(Integer, ForeignKey("value_contexts.id"), nullable=False)
    revision_id = Column(String(100), ForeignKey("value_revisions.id"), index=True)
    event_type = Column(String(50), nullable=False, index=True)
    from_state = Column(String(30))
    to_state = Column(String(30))
    pointer_moved = Column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    event_payload = Column(JSONB)
    idempotency_key = Column(String(255))
    occurred_at = Column(DateTime, default=func.now(), nullable=False)
    occurred_by = Column(String(100))

    context = relationship("ValueContext")
    revision = relationship("ValueRevision")

    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "event_seq",
            name="uq_value_revision_events_tenant_seq",
        ),
        sa.UniqueConstraint(
            "context_id",
            "context_event_seq",
            name="uq_value_revision_events_context_seq",
        ),
        Index("ix_value_revision_events_feed", "tenant_id", "event_seq", "id"),
    )


class CurrentValuePointer(Base):
    """Operational pointer to the current usable value revision."""

    __tablename__ = "current_value_pointers"

    context_id = Column(Integer, ForeignKey("value_contexts.id"), primary_key=True)
    tenant_id = Column(String(100), nullable=False, index=True)
    revision_id = Column(
        String(100),
        ForeignKey("value_revisions.id"),
        nullable=False,
        index=True,
    )
    pointer_basis = Column(
        String(50), nullable=False, default="current", server_default="current"
    )
    updated_at = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by = Column(String(100))

    context = relationship("ValueContext", back_populates="current_pointer")
    revision = relationship("ValueRevision")


class ReportedValuePointer(Base):
    """Audit pointer to the revision reported in a locked report snapshot."""

    __tablename__ = "reported_value_pointers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(100), nullable=False, index=True)
    entity_id = Column(String(200), nullable=False, index=True)
    reporting_period_id = Column(String(100), nullable=False, index=True)
    standard_release_id = Column(String(100), nullable=False, index=True)
    report_snapshot_id = Column(String(200), nullable=False, index=True)
    context_id = Column(Integer, ForeignKey("value_contexts.id"), nullable=False)
    revision_id = Column(String(100), ForeignKey("value_revisions.id"), nullable=False)
    pointer_basis = Column(
        String(50), nullable=False, default="reported", server_default="reported"
    )
    reported_at = Column(DateTime, default=func.now(), nullable=False)
    reported_by = Column(String(100))

    context = relationship("ValueContext")
    revision = relationship("ValueRevision")

    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "entity_id",
            "reporting_period_id",
            "standard_release_id",
            "report_snapshot_id",
            "context_id",
            name="uq_reported_value_pointers_snapshot_context",
        ),
        Index(
            "ix_reported_value_pointers_snapshot",
            "tenant_id",
            "report_snapshot_id",
        ),
    )


class RevokedToken(Base):
    """Revoked JWT tokens for persistent blacklist."""

    __tablename__ = "revoked_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String(64), nullable=False)
    revoked_at = Column(DateTime, default=func.now(), nullable=False)
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("token_hash"),
        Index("ix_revoked_tokens_token_hash", "token_hash"),
    )


class LocalizedText(Base):
    """Append-only/versioned localized display text keyed by canonical subject identity.

    The cross-cutting SDS localization store (LOC-1). Canonical source fields stay
    authoritative in V1; this holds APPROVED target-language display text + the
    ``source_hash`` it was approved against (SHA-256 over NFC-normalized source text),
    so a later source-text change marks the row stale. Public runtime serves only
    ``approved`` rows whose source_hash still matches and whose effective interval is
    active; at most one active approved row per
    (subject_uri, subject_kind, field, language, scope_kind, tenant_id).
    """

    __tablename__ = "localized_text"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    subject_kind = Column(String(40), nullable=False)
    subject_uri = Column(String(500), nullable=False)
    field = Column(String(50), nullable=False)
    language = Column(String(35), nullable=False)
    display_language = Column(String(35), nullable=False)
    text = Column(Text, nullable=False)
    status = Column(String(20), nullable=False)
    source_language = Column(String(35), nullable=False)
    source_hash = Column(sa.CHAR(64), nullable=False)
    translation_hash = Column(sa.CHAR(64), nullable=False)
    source_type = Column(String(30))
    source_ref = Column(String(500))
    reviewer_ref = Column(String(500))
    revision = Column(Integer, nullable=False, server_default="1", default=1)
    effective_from = Column(
        DateTime, nullable=False, server_default=func.now(), default=func.now()
    )
    effective_to = Column(DateTime)
    scope_kind = Column(String(30))
    tenant_id = Column(String)
    created_at = Column(
        DateTime, nullable=False, server_default=func.now(), default=func.now()
    )
    created_by = Column(String(100))

    __table_args__ = (
        Index(
            "ix_localized_text_lookup",
            "subject_kind",
            "subject_uri",
            "field",
            "language",
        ),
        Index("ix_localized_text_status", "status"),
        Index(
            "ux_localized_text_active_approved_global",
            "subject_kind",
            "subject_uri",
            "field",
            "language",
            unique=True,
            postgresql_where=sa.text(
                "status = 'approved' AND effective_to IS NULL AND tenant_id IS NULL"
            ),
        ),
        Index(
            "ux_localized_text_active_approved_tenant",
            "subject_kind",
            "subject_uri",
            "field",
            "language",
            "tenant_id",
            unique=True,
            postgresql_where=sa.text(
                "status = 'approved' AND effective_to IS NULL AND tenant_id IS NOT NULL"
            ),
        ),
    )


# Register the VARCH-1a semantic-atomization decision-time backbone tables on
# Base.metadata. semantic_models depends only on .base, so importing it here introduces
# no cycle; doing so guarantees any `src.database` import sees these tables and keeps the
# Alembic metadata drift guard honest.
from . import semantic_models as _semantic_models  # noqa: E402,F401
