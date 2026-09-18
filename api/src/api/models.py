"""
Pydantic models for API request/response validation.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from src.concept_runtime_aliases import CSRD_E3_5_DISCLOSURE_URI

ValueScalar = Union[bool, float, int, Decimal, str]


def _decimal_to_float(value: float | int | Decimal) -> float | int:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _serialize_value_scalar(value: ValueScalar) -> bool | float | int | str:
    if isinstance(value, Decimal):
        return float(value)
    return value


class TemporalGranularity(str, Enum):
    """Temporal granularity options."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMESTRAL = "semestral"
    ANNUAL = "annual"


class AggregationMethod(str, Enum):
    """Aggregation method options."""

    SUM = "SUM"
    AVERAGE = "AVERAGE"
    WEIGHTED_AVERAGE = "WEIGHTED_AVERAGE"
    MIN = "MIN"
    MAX = "MAX"
    COUNT = "COUNT"
    FIRST = "FIRST"
    LAST = "LAST"


# === VALUE MODELS ===


class ValueCreate(BaseModel):
    """Model for creating a new value."""

    concept: str = Field(
        ..., description="Concept URI (e.g., 'urn:sds:reg:esrs:e3_5_01')"
    )
    entity: str = Field(..., description="Entity ID (e.g., 'nh_group')")
    period: date = Field(..., description="Period date")
    external_key: Optional[str] = Field(
        None,
        description="Optional client-controlled observation key for deterministic upsert",
    )
    value: ValueScalar = Field(
        ...,
        description="Observation value. Numeric values can be unit-converted; boolean/text values are stored as-is.",
    )
    value_type: Optional[str] = Field(
        None,
        description=(
            "Optional SDS/Atomizer value type: numeric, boolean, narrative, or semi-narrative. "
            "Aliases number/text are accepted by import helpers."
        ),
    )
    unit: str = Field(..., description="Unit of measurement (e.g., 'L', 'kg', 'kWh')")
    currency: Optional[str] = Field(None, description="Row-level source currency code")
    expected_unit: Optional[str] = Field(
        None, description="Expected normalized unit for ingest-time conversion"
    )
    expected_currency: Optional[str] = Field(
        None, description="Expected normalized currency code for ingest-time conversion"
    )
    fx_policy_id: Optional[str] = Field(
        None,
        description="FX policy identifier to use when currency conversion is requested",
    )
    value_date: Optional[date] = Field(
        None, description="Transaction/value date for row-level observation metadata"
    )
    period_start: Optional[date] = Field(
        None, description="Start date of the source observation period"
    )
    period_end: Optional[date] = Field(
        None, description="End date of the source observation period"
    )
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "concept": "urn:sds:reg:esrs:e1_5_12",
                "entity": "nh_es_valencia_plant",
                "period": "2024-01-31",
                "external_key": "erp:nordhaven-valencia-energy-2024-01",
                "value": 19.995,
                "value_type": "numeric",
                "unit": "MWh",
                "metadata": {"source": "nordhaven_db", "quality": "validated"},
            }
        }
    )

    @field_serializer("value")
    def _serialize_value(self, v: ValueScalar):
        return _serialize_value_scalar(v)

    @field_validator("currency", "expected_currency")
    @classmethod
    def _normalize_currency_code(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().upper()
        return normalized or None


class ValueResponse(BaseModel):
    """Model for value response."""

    id: str = Field(..., description="Value ID")
    concept: str = Field(..., description="Concept URI")
    entity: str = Field(..., description="Entity ID")
    period: date = Field(..., description="Period date")
    external_key: Optional[str] = Field(
        None, description="Client-controlled observation key"
    )
    value: ValueScalar = Field(..., description="Stored observation value")
    original_value: Optional[ValueScalar] = Field(
        None, description="Original value before conversion"
    )
    value_type: str = Field(
        "numeric",
        description="Stored SDS/Atomizer value type: numeric, boolean, narrative, or semi-narrative",
    )
    unit: str = Field(..., description="Unit of measurement")
    original_unit: Optional[str] = Field(
        None, description="Original unit before conversion"
    )
    conversion_applied: Optional[bool] = Field(
        False, description="Whether unit conversion was applied"
    )
    currency: Optional[str] = Field(None, description="Stored row-level currency code")
    original_currency: Optional[str] = Field(
        None, description="Original source currency before conversion"
    )
    currency_conversion_applied: Optional[bool] = Field(
        False, description="Whether currency conversion was applied"
    )
    conversion_trace: Optional[List[Dict[str, Any]]] = Field(
        None, description="Ordered unit/FX conversion provenance trace"
    )
    value_date: Optional[date] = Field(
        None, description="Transaction/value date for row-level observation metadata"
    )
    period_start: Optional[date] = Field(
        None, description="Start date of the source observation period"
    )
    period_end: Optional[date] = Field(
        None, description="End date of the source observation period"
    )
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    @field_serializer("value", "original_value")
    def _serialize_value(self, v: ValueScalar):
        if v is None:
            return None
        return _serialize_value_scalar(v)


class ValueContextResponse(BaseModel):
    """Stable value-context identity used by revision-backed SDS values."""

    id: int
    tenant_id: str
    context_hash_recipe_version: str
    context_hash: str
    entity_id: str
    reporting_period_id: str
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    period_close_date: Optional[date] = None
    period_type: str
    reporting_boundary_id: str
    canonical_concept_id: Optional[int] = None
    canonical_uri: Optional[str] = None
    source_observation_type: str = "standard_direct_legacy"
    sds_indicator_id: Optional[str] = None
    indicator_identifier: str
    standard_release_id: str
    standard_datapoint_id: str
    dimensions: Dict[str, Any] = Field(default_factory=dict)
    scenario_basis: str
    value_kind: str
    expected_unit: Optional[str] = None
    expected_currency: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None


class ValueRevisionResponse(BaseModel):
    """Immutable append-only SDS value revision."""

    id: str
    context_id: int
    tenant_id: str
    revision_number: int
    state: str
    value_kind: str
    canonical_value: Optional[str] = None
    numeric_value: Optional[str] = None
    text_value: Optional[str] = None
    boolean_value: Optional[bool] = None
    unit: Optional[str] = None
    currency: Optional[str] = None
    source_system: Optional[str] = None
    source_record_id: Optional[str] = None
    external_key: Optional[str] = None
    evidence_hash: Optional[str] = None
    materiality_metadata: Optional[Dict[str, Any]] = None
    calculation_contract_id: Optional[int] = None
    formula_contract_hash: Optional[str] = None
    input_revision_ids: List[str] = Field(default_factory=list)
    conversion_trace: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None
    trace_hash: Optional[str] = None
    parent_revision_id: Optional[str] = None
    revision_provenance: Optional[str] = None
    source_payload_hash: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None


class ValueRevisionEventResponse(BaseModel):
    """Monotonic event emitted by a value revision or pointer change."""

    id: str
    tenant_id: str
    event_seq: int
    context_event_seq: int
    context_id: int
    revision_id: Optional[str] = None
    event_type: str
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    pointer_moved: bool
    event_payload: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    occurred_at: Optional[datetime] = None
    occurred_by: Optional[str] = None


class ValueRevisionLineageResponse(BaseModel):
    """Lineage for one value revision."""

    context: ValueContextResponse
    revision: ValueRevisionResponse
    parent_revision_id: Optional[str] = None
    input_revision_ids: List[str] = Field(default_factory=list)


class ValueContextLineageResponse(BaseModel):
    """All revisions and events for one value context."""

    context: ValueContextResponse
    revisions: List[ValueRevisionResponse] = Field(default_factory=list)
    events: List[ValueRevisionEventResponse] = Field(default_factory=list)
    current_revision_id: Optional[str] = None


class ValueRevisionChangeFeedResponse(BaseModel):
    """Event-sequence paginated feed for revision-backed SDS values."""

    tenant_id: str
    limit: int = Field(..., ge=1)
    items: List[ValueRevisionEventResponse]
    next_after_event_seq: Optional[int] = None
    has_more: bool


class ValueImportStatus(str, Enum):
    """Bulk import row status."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ValueImportJobStatus(str, Enum):
    """Async values import job status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class IndicatorImportJobStatus(str, Enum):
    """Async indicator import job status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class IndicatorImportJobType(str, Enum):
    """Indicator import job kind."""

    VALIDATION = "validation"
    IMPORT = "import"


class CanonicalMappingPackageJobStatus(str, Enum):
    """Async canonical mapping package job status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CanonicalMappingPackageJobType(str, Enum):
    """Canonical mapping package job kind."""

    VALIDATION = "validation"
    IMPORT = "import"


class ValueExportFormat(str, Enum):
    """Supported values export formats."""

    CSV = "csv"
    JSON = "json"
    PARQUET = "parquet"


class DatasetExportFormat(str, Enum):
    """Supported dataset export formats."""

    CSV = "csv"
    JSON = "json"
    PARQUET = "parquet"


class ChangeFeedDataset(str, Enum):
    """Datasets exposed through the incremental changed feed."""

    INDICATORS = "indicators"
    MAPPINGS = "mappings"
    VALUES = "values"


class ChangeFeedEventType(str, Enum):
    """Supported changed-feed event types."""

    SNAPSHOT_PERSISTED = "snapshot_persisted"
    IMPORT_COMMITTED = "import_committed"
    ROW_CHANGED = "row_changed"


class DatasetManifestResponse(BaseModel):
    """Version/audit manifest for a dataset export surface."""

    dataset: str = Field(..., description="Logical dataset name")
    record_count: int = Field(
        ..., ge=0, description="Number of records in the exported slice"
    )
    manifest_hash: str = Field(
        ..., description="Deterministic SHA-256 hash of the exported slice"
    )
    etag: str = Field(..., description="HTTP ETag derived from the manifest hash")
    contract_version: str = Field(..., description="Dataset export contract version")
    generated_at: datetime = Field(..., description="Manifest generation timestamp")
    last_modified: Optional[datetime] = Field(
        None, description="Latest known record update timestamp for the exported slice"
    )
    signature_algorithm: str = Field(
        ..., description="Detached signature algorithm for the manifest payload"
    )
    signature_key_id: str = Field(
        ...,
        description="Identifier of the key/secret used to sign the manifest payload",
    )
    signature: str = Field(
        ..., description="Base64 detached signature for the canonical manifest payload"
    )
    signed_at: datetime = Field(..., description="Manifest signature timestamp")
    signed_payload_hash: str = Field(
        ..., description="SHA-256 hash of the canonical payload that was signed"
    )


class DatasetSnapshotResponse(BaseModel):
    """Persistent snapshot metadata for a catalog dataset."""

    id: int = Field(..., description="Snapshot identifier")
    dataset: str = Field(..., description="Logical dataset name")
    manifest_hash: str = Field(
        ..., description="Deterministic SHA-256 hash of the exported slice"
    )
    record_count: int = Field(
        ..., ge=0, description="Number of records in the snapshot"
    )
    contract_version: str = Field(..., description="Dataset export contract version")
    source_ref: Optional[str] = Field(
        None,
        description="Input artifact or source reference that produced the snapshot",
    )
    source_hash: Optional[str] = Field(
        None, description="SHA-256 hash of the imported source artifact when available"
    )
    created_by: Optional[str] = Field(
        None, description="Tool or actor that generated the snapshot"
    )
    created_at: datetime = Field(..., description="Snapshot creation timestamp")


class DatasetSnapshotHistoryResponse(BaseModel):
    """Paginated history of persistent dataset snapshots."""

    dataset: str = Field(..., description="Logical dataset name")
    total: int = Field(..., ge=0, description="Total snapshot count available")
    limit: int = Field(..., ge=1, description="Requested page size")
    items: List[DatasetSnapshotResponse] = Field(
        ..., description="Most recent snapshots first"
    )


class DatasetDiffResponse(BaseModel):
    """Structural diff between two dataset snapshots or snapshot vs current state."""

    dataset: str = Field(..., description="Logical dataset name")
    from_snapshot_id: Optional[int] = Field(
        None, description="Baseline snapshot identifier"
    )
    to_snapshot_id: Optional[int] = Field(
        None,
        description="Target snapshot identifier when comparing two stored versions",
    )
    from_manifest_hash: str = Field(..., description="Baseline manifest hash")
    to_manifest_hash: str = Field(..., description="Target manifest hash")
    added_count: int = Field(..., ge=0, description="Rows added in the target slice")
    removed_count: int = Field(
        ..., ge=0, description="Rows removed in the target slice"
    )
    changed_count: int = Field(
        ..., ge=0, description="Rows whose stable payload changed"
    )
    unchanged_count: int = Field(
        ..., ge=0, description="Rows unchanged between baseline and target"
    )
    added_keys: List[str] = Field(
        ..., description="Added logical keys, truncated when necessary"
    )
    removed_keys: List[str] = Field(
        ..., description="Removed logical keys, truncated when necessary"
    )
    changed_keys: List[str] = Field(
        ..., description="Changed logical keys, truncated when necessary"
    )
    keys_truncated: bool = Field(..., description="Whether any key list was truncated")


class ChangeFeedEventResponse(BaseModel):
    """Single event emitted by the incremental changed feed."""

    cursor: str = Field(..., description="Opaque cursor checkpoint for this event")
    dataset: ChangeFeedDataset = Field(..., description="Logical dataset that changed")
    event_type: ChangeFeedEventType = Field(
        ..., description="Type of persisted change event"
    )
    occurred_at: datetime = Field(
        ..., description="Event timestamp used for total ordering"
    )
    event_id: str = Field(
        ..., description="Stable event identifier inside the dataset feed"
    )
    manifest_hash: Optional[str] = Field(
        None,
        description="Dataset manifest hash when the event comes from a catalog snapshot",
    )
    contract_version: Optional[str] = Field(
        None, description="Export contract version associated with the event"
    )
    record_count: Optional[int] = Field(
        None, ge=0, description="Record count materialized by the event when available"
    )
    source_ref: Optional[str] = Field(
        None, description="Source artifact or import reference when available"
    )
    source_hash: Optional[str] = Field(
        None, description="SHA-256 hash of the imported source artifact when available"
    )
    snapshot_id: Optional[int] = Field(
        None, description="Snapshot identifier for catalog events"
    )
    previous_snapshot_id: Optional[int] = Field(
        None,
        description="Previous snapshot identifier when the event is a catalog snapshot",
    )
    previous_manifest_hash: Optional[str] = Field(
        None, description="Previous manifest hash when the event is a catalog snapshot"
    )
    diff: Optional[Dict[str, Any]] = Field(
        None, description="Diff summary when the event is a catalog snapshot"
    )
    job_id: Optional[str] = Field(
        None, description="Async import job identifier for value events"
    )
    submitted_by: Optional[str] = Field(
        None, description="Submitting principal for value import events"
    )
    source_format: Optional[str] = Field(
        None, description="Source payload format for value import events"
    )
    source_filename: Optional[str] = Field(
        None, description="Original source filename when the event came from CSV import"
    )
    total_rows: Optional[int] = Field(
        None, ge=0, description="Total rows processed by a value import event"
    )
    accepted_rows: Optional[int] = Field(
        None, ge=0, description="Rows accepted by a value import event"
    )
    rejected_rows: Optional[int] = Field(
        None, ge=0, description="Rows rejected by a value import event"
    )
    committed: Optional[bool] = Field(
        None, description="Whether a value import event committed data"
    )
    operation: Optional[str] = Field(
        None, description="Logical operation for row-level value changes"
    )
    record: Optional[Dict[str, Any]] = Field(
        None,
        description="Current record payload when the event is a row-level value change",
    )


class ChangeFeedResponse(BaseModel):
    """Cursor-paginated response for persisted catalog/value changes."""

    datasets: List[ChangeFeedDataset] = Field(
        ..., description="Datasets covered by this feed page"
    )
    limit: int = Field(..., ge=1, description="Requested page size")
    items: List[ChangeFeedEventResponse] = Field(
        ..., description="Events ordered from oldest to newest"
    )
    next_cursor: Optional[str] = Field(
        None, description="Opaque cursor for the next page checkpoint"
    )
    has_more: bool = Field(
        ..., description="Whether more events remain after this page"
    )


class ValueBulkImportRequest(BaseModel):
    """Model for bulk values import."""

    items: List[ValueCreate] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Values to validate, normalize, and persist as one batch",
    )


class ValueBulkImportRowResult(BaseModel):
    """Per-row result for a bulk import request."""

    row_number: int = Field(
        ..., ge=1, description="1-based row number within the request payload"
    )
    status: ValueImportStatus = Field(..., description="Row processing status")
    concept: Optional[str] = Field(None, description="Concept URI")
    entity: Optional[str] = Field(None, description="Entity ID")
    period: Optional[date] = Field(None, description="Period date")
    external_key: Optional[str] = Field(
        None, description="Client-controlled observation key"
    )
    unit: Optional[str] = Field(None, description="Stored unit")
    value_id: Optional[str] = Field(
        None, description="Persisted value ID when the row is accepted"
    )
    message: Optional[str] = Field(None, description="Validation or processing message")


class ValueBulkImportResponse(BaseModel):
    """Response model for bulk values import."""

    total_rows: int = Field(..., ge=0, description="Total rows received")
    accepted_rows: int = Field(..., ge=0, description="Rows that passed validation")
    rejected_rows: int = Field(
        ..., ge=0, description="Rows rejected before persistence"
    )
    committed: bool = Field(..., description="Whether the batch was persisted")
    items: List[ValueBulkImportRowResult] = Field(
        ..., description="Per-row result report"
    )


class ValueImportJobResponse(BaseModel):
    """Response model for an async value import job."""

    id: str = Field(..., description="Job identifier")
    source_format: str = Field(..., description="Submitted payload format")
    status: ValueImportJobStatus = Field(..., description="Current job status")
    submitted_by: str = Field(..., description="Submitting principal")
    source_filename: Optional[str] = Field(
        None, description="Original filename when submitted as CSV"
    )
    request_metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Submission metadata"
    )
    total_rows: Optional[int] = Field(
        None, ge=0, description="Total rows processed by the job"
    )
    accepted_rows: Optional[int] = Field(
        None, ge=0, description="Rows accepted by validation"
    )
    rejected_rows: Optional[int] = Field(
        None, ge=0, description="Rows rejected by validation"
    )
    committed: Optional[bool] = Field(
        None, description="Whether the batch was persisted"
    )
    result_body: Optional[Dict[str, Any]] = Field(
        None, description="Final batch response when available"
    )
    error_message: Optional[str] = Field(
        None, description="Failure message when the job fails"
    )
    created_at: datetime = Field(..., description="Job creation timestamp")
    started_at: Optional[datetime] = Field(
        None, description="Job execution start timestamp"
    )
    completed_at: Optional[datetime] = Field(
        None, description="Job completion timestamp"
    )
    updated_at: datetime = Field(..., description="Last job status update timestamp")


class IndicatorImportJobResponse(BaseModel):
    """Response model for an indicator import validation or apply job."""

    id: str = Field(..., description="Job identifier")
    job_type: IndicatorImportJobType = Field(..., description="validation or import")
    source_format: str = Field(..., description="Submitted payload format")
    status: IndicatorImportJobStatus = Field(..., description="Current job status")
    submitted_by: str = Field(..., description="Submitting principal")
    source_filename: Optional[str] = Field(None, description="Original CSV filename")
    source_sha256: Optional[str] = Field(
        None, description="SHA-256 of the uploaded CSV text"
    )
    source_size_bytes: Optional[int] = Field(
        None, ge=0, description="Uploaded CSV size in bytes"
    )
    validation_job_id: Optional[str] = Field(
        None, description="Validation job used to submit an import job"
    )
    request_metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Submission metadata"
    )
    total_rows: Optional[int] = Field(
        None, ge=0, description="Total data rows processed"
    )
    accepted_rows: Optional[int] = Field(
        None, ge=0, description="Rows accepted by validation"
    )
    rejected_rows: Optional[int] = Field(
        None, ge=0, description="Rows rejected by validation"
    )
    committed: Optional[bool] = Field(
        None, description="Whether catalog changes were committed"
    )
    result_body: Optional[Dict[str, Any]] = Field(
        None, description="Validation/import summary when available"
    )
    error_message: Optional[str] = Field(
        None, description="Failure message when the job fails"
    )
    created_at: datetime = Field(..., description="Job creation timestamp")
    started_at: Optional[datetime] = Field(
        None, description="Job execution start timestamp"
    )
    completed_at: Optional[datetime] = Field(
        None, description="Job completion timestamp"
    )
    updated_at: datetime = Field(..., description="Last job status update timestamp")


class CanonicalMappingPackageJobResponse(BaseModel):
    """Response model for internal canonical mapping package jobs."""

    id: str = Field(..., description="Job identifier")
    job_type: CanonicalMappingPackageJobType = Field(
        ..., description="validation or import"
    )
    package_id: str = Field(..., description="Canonical mapping package id")
    status: CanonicalMappingPackageJobStatus = Field(
        ..., description="Current job status"
    )
    submitted_by: str = Field(..., description="Submitting principal")
    validation_job_id: Optional[str] = Field(
        None, description="Validation job used to submit an import job"
    )
    request_metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Submission metadata"
    )
    total_rows: Optional[int] = Field(
        None, ge=0, description="Total assertion groups evaluated"
    )
    accepted_rows: Optional[int] = Field(
        None, ge=0, description="Assertion groups accepted for activation"
    )
    rejected_rows: Optional[int] = Field(
        None, ge=0, description="Assertion groups rejected or left pending"
    )
    committed: Optional[bool] = Field(
        None, description="Whether canonical mapping changes were committed"
    )
    result_body: Optional[Dict[str, Any]] = Field(
        None, description="Validation/import summary when available"
    )
    error_message: Optional[str] = Field(
        None, description="Failure or blocker message when available"
    )
    created_at: datetime = Field(..., description="Job creation timestamp")
    started_at: Optional[datetime] = Field(
        None, description="Job execution start timestamp"
    )
    completed_at: Optional[datetime] = Field(
        None, description="Job completion timestamp"
    )
    updated_at: datetime = Field(..., description="Last job status update timestamp")


class IndicatorImportErrorsResponse(BaseModel):
    """Paginated row-level errors for an indicator import job."""

    job_id: str = Field(..., description="Job identifier")
    total_errors: int = Field(..., ge=0, description="Total retained row errors")
    offset: int = Field(..., ge=0, description="Current page offset")
    limit: int = Field(..., ge=1, description="Current page size")
    items: List[Dict[str, Any]] = Field(
        default_factory=list, description="Row error items"
    )


class ValueFilter(BaseModel):
    """Model for filtering values."""

    concept: Optional[str] = Field(None, description="Filter by concept URI")
    entity: Optional[str] = Field(None, description="Filter by entity ID")
    period_start: Optional[date] = Field(
        None, description="Start date for period filter"
    )
    period_end: Optional[date] = Field(None, description="End date for period filter")
    unit: Optional[str] = Field(None, description="Filter by unit")
    limit: Optional[int] = Field(
        100, ge=1, le=1000, description="Maximum number of results"
    )
    offset: Optional[int] = Field(0, ge=0, description="Number of results to skip")


# === CALCULATION MODELS ===


class CalculationRequest(BaseModel):
    """Model for calculation request."""

    concept: str = Field(
        ...,
        description=("Indicator concept URI (e.g., " f"'{CSRD_E3_5_DISCLOSURE_URI}')"),
    )
    entity: str = Field(..., description="Entity ID for calculation")
    period: str = Field(..., description="Period identifier (e.g., '2024', '2024-Q1')")
    granularity: Optional[TemporalGranularity] = Field(
        TemporalGranularity.ANNUAL, description="Temporal granularity"
    )
    include_trace: Optional[bool] = Field(True, description="Include calculation trace")


class CalculationTrace(BaseModel):
    """Model for calculation trace information."""

    calculation_id: str = Field(..., description="Unique calculation ID")
    steps: List[str] = Field(..., description="Calculation steps performed")
    variables_used: List[str] = Field(..., description="Variables used in calculation")
    variable_values: Dict[str, Union[float, int, str, bool, None]] = Field(
        default_factory=dict,
        description="Variable values used when evaluating the formula",
    )
    aggregations_applied: List[str] = Field(..., description="Aggregations applied")
    conversions_applied: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Unit and currency conversion steps applied during calculation",
    )
    dependencies_resolved: List[str] = Field(..., description="Dependencies resolved")
    warnings: List[str] = Field(
        default_factory=list,
        description="Non-fatal calculation warnings",
    )
    execution_time_ms: Optional[float] = Field(
        None, description="Execution time in milliseconds"
    )


class CalculationResponse(BaseModel):
    """Model for calculation response."""

    concept: str = Field(..., description="Calculated concept URI")
    entity: str = Field(..., description="Entity ID")
    period: str = Field(..., description="Period identifier")
    value: Union[float, int, Decimal] = Field(..., description="Calculated value")
    unit: Optional[str] = Field(None, description="Result unit")
    formula_used: Optional[str] = Field(None, description="Formula applied")
    confidence: float = Field(1.0, ge=0, le=1, description="Calculation confidence")
    trace: Optional[CalculationTrace] = Field(None, description="Calculation trace")
    calculated_at: datetime = Field(..., description="Calculation timestamp")
    requested_concept: Optional[str] = Field(
        None,
        description="Framework concept requested by the caller; equals concept for native calculations.",
    )
    returned_framework_object: Optional[Dict[str, Any]] = Field(
        None,
        description="Framework-facing object returned to the caller.",
    )
    executed_concept: Optional[str] = Field(
        None,
        description="Canonical concept actually executed when it differs from the request.",
    )
    execution_authority: Optional[str] = Field(
        None,
        description=(
            "Execution authority: native, native_contract, equivalent_mapping, "
            "or certified_bridge."
        ),
    )
    mapping_relationship_type: Optional[str] = Field(
        None,
        description="Mapping relationship type used for cross-standard execution, if any.",
    )
    coverage_status: Optional[str] = Field(
        None,
        description="Coverage status for the executed route.",
    )
    bridge_id: Optional[str] = Field(
        None,
        description="Certified bridge contract id when non-equivalent transfer is explicitly approved.",
    )
    source_value_ids: List[str] = Field(
        default_factory=list,
        description="Source observation or revision ids used by the executed calculation.",
    )
    unit_policy: Optional[Dict[str, Any]] = Field(
        None,
        description="Unit policy applied to the returned framework value.",
    )
    unit_conversions: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Unit conversions applied to return the requested framework value.",
    )
    aggregation_policy: Optional[Dict[str, Any]] = Field(
        None,
        description="Entity, period, perimeter, or hierarchy aggregation policy used.",
    )
    framework_metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Framework standard, version, code, label, and requirement metadata when known.",
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Non-fatal calculation or cross-standard route warnings.",
    )

    @field_serializer("value")
    def _serialize_value(self, v: float | int | Decimal):
        return _decimal_to_float(v)


class BatchCalculationStatus(str, Enum):
    """Per-concept batch calculation outcome."""

    SUCCESS = "success"
    FAILED = "failed"


class BatchCalculationItem(BaseModel):
    """Single concept outcome for batch calculation requests."""

    concept: str = Field(..., description="Requested concept URI")
    status: BatchCalculationStatus = Field(..., description="Calculation outcome")
    result: Optional[CalculationResponse] = Field(
        None, description="Successful calculation result"
    )
    error: Optional[str] = Field(None, description="Failure detail")


# === VALUE RESOLUTION MODELS ===


class ValueResolutionStatus(str, Enum):
    """Resolver outcome for a requested concept/entity/period value."""

    RESOLVED = "resolved"
    NOT_TRANSFORMABLE = "not_transformable"
    NOT_ENOUGH_EVIDENCE = "not_enough_evidence"
    INCOMPLETE_COVERAGE = "incomplete_coverage"
    MULTIPLE_TARGETS = "multiple_targets"
    AMBIGUOUS_MAPPING = "ambiguous_mapping"
    BRIDGE_REQUIRED = "bridge_required"
    NON_EXECUTABLE_CONTRACT = "non_executable_contract"
    FRAMEWORK_METADATA_INCOMPLETE = "framework_metadata_incomplete"
    AGGREGATION_POLICY_UNPROVEN = "aggregation_policy_unproven"
    ENTITY_SCOPE_UNPROVEN = "entity_scope_unproven"
    PERIOD_SEMANTICS_UNPROVEN = "period_semantics_unproven"
    UNSUPPORTED_CONVERSION = "unsupported_conversion"
    NOT_FOUND = "not_found"
    INVALID_REQUEST = "invalid_request"


class ValueResolutionMethod(str, Enum):
    """Transformation method used or refused by the value resolver."""

    DIRECT_STORED = "direct_stored"
    CALCULATION = "calculation"
    EQUIVALENT_MAPPING = "equivalent_mapping"
    CONVERTED = "converted"
    CALCULATION_THEN_MAPPING = "calculation_then_mapping"
    MAPPING_THEN_CONVERSION = "mapping_then_conversion"
    COMPONENT_AGGREGATION = "component_aggregation"
    REFUSED_NON_EQUIVALENT_MAPPING = "refused_non_equivalent_mapping"


class ValueResolveRequest(BaseModel):
    """Resolve one ESG value through direct storage, calculation, mapping, or conversion."""

    target_concept: str = Field(
        ...,
        description=(
            "Concept requested by the consumer, for example "
            f"{CSRD_E3_5_DISCLOSURE_URI} or "
            "urn:sds:disclosure:gri:303-3."
        ),
    )
    entity: str = Field(..., description="Entity ID to resolve for.")
    period: str = Field(
        ...,
        description="Reporting period identifier, for example 2024 or 2024-01-15.",
    )
    granularity: TemporalGranularity = Field(
        TemporalGranularity.ANNUAL,
        description="Temporal granularity used for period matching and calculations.",
    )
    source_concept: Optional[str] = Field(
        None,
        description="Optional explicit source concept to map from before resolving the target.",
    )
    target_unit: Optional[str] = Field(
        None,
        description="Optional unit requested by the consumer. Unsupported cross-dimension conversions fail closed.",
    )
    include_trace: bool = Field(
        True,
        description="Return route, mapping, contract, and conversion evidence when available.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "target_concept": "urn:sds:reg:esrs:e1_5_12",
                "entity": "nh_group",
                "period": "2024",
                "granularity": "annual",
                "target_unit": "MWh",
                "include_trace": True,
            }
        }
    )


class ValueResolveResponse(BaseModel):
    """Resolved value or structured refusal with route evidence."""

    status: ValueResolutionStatus = Field(..., description="Resolution outcome.")
    method: Optional[ValueResolutionMethod] = Field(
        None, description="Method used, or the refused transformation class."
    )
    target_concept: str = Field(..., description="Requested target concept.")
    source_concept: Optional[str] = Field(
        None, description="Source concept used when resolution crossed a mapping."
    )
    entity: str = Field(..., description="Resolved entity.")
    period: str = Field(..., description="Requested period.")
    granularity: TemporalGranularity = Field(..., description="Requested granularity.")
    value: Optional[Union[float, int, Decimal, str, bool]] = Field(
        None, description="Resolved value when status is resolved."
    )
    unit: Optional[str] = Field(None, description="Resolved or requested unit.")
    value_id: Optional[str] = Field(
        None, description="Stored source value ID when direct storage was used."
    )
    relationship_type: Optional[str] = Field(
        None, description="Mapping relationship type used or refused."
    )
    mapping_row_ids: List[str] = Field(
        default_factory=list, description="Mapping row IDs involved in the route."
    )
    component_concepts: List[str] = Field(
        default_factory=list,
        description="Source component concepts used when the route aggregates narrower mappings.",
    )
    contract_id: Optional[str] = Field(
        None, description="Calculation contract identifier when a calculation ran."
    )
    contract_version: Optional[str] = Field(
        None, description="Calculation contract version when a calculation ran."
    )
    execution_authority: Optional[str] = Field(
        None,
        description=(
            "Calculation authority used when a calculation route ran: native, "
            "native_contract, equivalent_mapping, or certified_bridge."
        ),
    )
    bridge_id: Optional[str] = Field(
        None,
        description=(
            "Certified bridge contract id when the resolver used an explicitly "
            "authorized non-equivalent calculation route."
        ),
    )
    missing_inputs: List[str] = Field(
        default_factory=list, description="Inputs missing for a calculation route."
    )
    candidates: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Candidate mappings or routes inspected before resolving/refusing.",
    )
    conversion_steps: List[Dict[str, Any]] = Field(
        default_factory=list, description="Unit or FX conversion trace steps."
    )
    alternative_route: Optional[Dict[str, Any]] = Field(
        None,
        description="Lower-priority route found but not selected, for example calculation behind a direct stored value.",
    )
    trace: Optional[Dict[str, Any]] = Field(
        None, description="Detailed route evidence when include_trace is true."
    )

    @field_serializer("value")
    def _serialize_value(self, v: Optional[Union[float, int, Decimal, str, bool]]):
        if v is None:
            return None
        return _serialize_value_scalar(v)


class RuntimeReadinessCheck(BaseModel):
    """One runtime prerequisite check for the SDS interoperability flow."""

    name: str = Field(..., description="Stable check identifier.")
    ready: bool = Field(..., description="Whether this prerequisite is available.")
    detail: str = Field(..., description="Human-readable check result.")
    evidence: Dict[str, Any] = Field(
        default_factory=dict, description="Machine-readable evidence for the check."
    )


class RuntimeReadinessResponse(BaseModel):
    """Runtime readiness for the SDS interoperability flow."""

    status: str = Field(..., description="ready when all required checks pass.")
    required_checks: int = Field(..., ge=0, description="Number of required checks.")
    passed_checks: int = Field(..., ge=0, description="Number of checks that passed.")
    checks: List[RuntimeReadinessCheck] = Field(
        default_factory=list, description="Individual prerequisite checks."
    )


# === CONFIGURATION MODELS ===


class HierarchyLevel(BaseModel):
    """Model for hierarchy level."""

    id: str = Field(..., description="Level ID")
    name: str = Field(..., description="Level name")
    parent: Optional[str] = Field(None, description="Parent level ID")
    level: int = Field(..., ge=0, description="Hierarchy level (0 = root)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class HierarchyConfiguration(BaseModel):
    """Model for hierarchy configuration."""

    id: Optional[str] = Field(None, description="Hierarchy configuration ID")
    company_id: str = Field(..., description="Company identifier")
    hierarchy_type: str = Field(
        ..., description="Type of hierarchy (organizational, temporal)"
    )
    name: str = Field(..., description="Configuration name")
    description: Optional[str] = Field(None, description="Configuration description")
    levels: List[HierarchyLevel] = Field(..., description="Hierarchy levels")
    active: bool = Field(True, description="Whether configuration is active")

    @field_validator("levels")
    @classmethod
    def validate_levels(cls, v: List[HierarchyLevel]):
        """Validate hierarchy levels."""
        if not v:
            raise ValueError("At least one level is required")

        # Check for duplicate IDs
        ids = [level.id for level in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate level IDs found")

        return v


# === ONTOLOGY MODELS ===


class ConceptInfo(BaseModel):
    """Model for concept information."""

    uri: str = Field(..., description="Concept URI")
    label: str = Field(..., description="Human-readable label")
    description: Optional[str] = Field(None, description="Concept description")
    taxonomy: str = Field(
        ...,
        description=(
            "Source taxonomy for the returned concept. The query filter "
            "`taxonomy=CSRD`, `taxonomy=GRI`, or `taxonomy=GHG` selects a source "
            "standard; `taxonomy=Sygris` selects the unified SDS/Sygris public "
            "catalog view."
        ),
    )
    concept_type: str = Field(
        ..., description="Type of concept (Variable, Indicator, Disclosure)"
    )
    unit: Optional[str] = Field(None, description="Default unit")
    temporal_granularity: Optional[str] = Field(
        None, description="Temporal granularity"
    )
    hierarchy_level: Optional[int] = Field(None, description="Hierarchy level")
    similarity_score: Optional[float] = Field(
        None,
        description=(
            "Similarity score for text-search results; None for ordinary list/detail "
            "reads and for searches that do not compute a score."
        ),
    )
    related_variables: Optional[List[str]] = Field(
        None,
        description=(
            "Related variable URIs from the public calculation contract when one "
            "backs this concept. None or an empty list means no public derivation "
            "is available on this response, not that private/runtime-only data was searched."
        ),
    )
    formula: Optional[str] = Field(
        None,
        description=(
            "Public calculation formula when the concept has an active public "
            "calculation contract or projected public formula; None for "
            "source-standard disclosure/catalog nodes and concepts without a public derivation."
        ),
    )
    formula_kind: Optional[str] = Field(
        None,
        description=(
            "Kind of calculation contract backing this concept (e.g. ratio, sum, "
            "direct). Populated only when the concept's indicator has an active, "
            "publicly-registered calculation contract; else None."
        ),
    )
    dimensions: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=(
            "Canonical calculation dimensions that disaggregate this concept "
            "(e.g. waste_hazard_status, waste_treatment_type), each "
            "`{dimension_id, mode, fixed_value, member_values, required}`, ordered by "
            "dimension_id. Populated only from a publicly-registered active contract."
        ),
    )
    aggregation: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Aggregation policy of the backing calculation contract, passed through "
            "verbatim from the imported contract; None when no public contract applies."
        ),
    )
    calculation_contract_id: Optional[str] = Field(
        None,
        description=(
            "Stable content-addressed identity (contract hash) of the publicly-registered "
            "calculation contract backing this concept; None when none applies."
        ),
    )
    calculation_contract_version: Optional[str] = Field(
        None,
        description="Version of the backing public calculation contract; else None.",
    )
    pins: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Public temporal-read reproducibility pins; populated only when a bitemporal "
            "selector is supplied (else None, preserving backward-compatible behavior)."
        ),
    )
    display_label: Optional[str] = Field(
        None,
        description=(
            "Localized display label when ?lang is requested and an approved/current "
            "translation exists for the requested language or its base-language fallback; "
            "else the canonical label (source fallback). Canonical `label` is never "
            "replaced (V1 additive contract)."
        ),
    )
    display_description: Optional[str] = Field(
        None, description="Localized display description (same rules as display_label)."
    )
    localization: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Localization metadata (requested/resolved/source language, status, "
            "fallback_chain, missing_fields, stale_fields); status is approved_current "
            "for exact requested-language translations, language_fallback for approved "
            "base-language broadening, or source_fallback for canonical-source fallback. "
            "Populated only when ?lang is requested."
        ),
    )


class EquivalenceInfo(BaseModel):
    """Model for concept equivalence information."""

    source_concept: str = Field(..., description="Source concept URI")
    target_concept: str = Field(..., description="Target concept URI")
    equivalence_type: str = Field(
        ..., description="Type of equivalence (exact, partial, contributesTo, related)"
    )
    confidence: float = Field(..., ge=0, le=1, description="Equivalence confidence")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class SPARQLQuery(BaseModel):
    """Model for SPARQL query."""

    query: str = Field(..., description="SPARQL query string")
    format: Optional[str] = Field(
        "json", description="Result format (json, xml, turtle)"
    )


# === UNIT CONVERSION MODELS ===


class UnitConversionRequest(BaseModel):
    """Model for unit conversion request."""

    value: Union[float, int, Decimal] = Field(..., description="Value to convert")
    from_unit: str = Field(..., description="Source unit")
    to_unit: str = Field(..., description="Target unit")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"value": 1000, "from_unit": "kg", "to_unit": "t"}
        }
    )

    @field_serializer("value")
    def _serialize_value(self, v: float | int | Decimal):
        return _decimal_to_float(v)


class UnitConversionResponse(BaseModel):
    """Model for unit conversion response."""

    original_value: Union[float, int, Decimal] = Field(
        ..., description="Original value"
    )
    original_unit: str = Field(..., description="Original unit")
    converted_value: Union[float, int, Decimal] = Field(
        ..., description="Converted value"
    )
    converted_unit: str = Field(..., description="Converted unit")
    conversion_factor: Optional[Union[float, int, Decimal]] = Field(
        None, description="Conversion factor applied; null for affine conversions"
    )
    formula_used: str = Field(..., description="Formula used for conversion")

    @field_serializer("original_value", "converted_value", "conversion_factor")
    def _serialize_decimal_fields(self, v: float | int | Decimal | None):
        if v is None:
            return None
        return _decimal_to_float(v)


class UnitInfo(BaseModel):
    """Model for unit information."""

    symbol: str = Field(..., description="Unit symbol")
    name: str = Field(..., description="Unit name")
    category: str = Field(..., description="Unit category")
    base_unit: str = Field(..., description="Base unit for category")
    aliases: List[str] = Field(..., description="Unit aliases")


# === FX CONVERSION MODELS ===


class FXCurrencyResponse(BaseModel):
    """Pinned currency catalog row."""

    code: str = Field(..., min_length=3, max_length=3, description="ISO 4217 code")
    numeric_code: Optional[str] = Field(None, description="ISO 4217 numeric code")
    name: str = Field(..., description="Currency name")
    minor_units: int = Field(..., ge=0, description="Minor units/decimal places")
    valid_from: Optional[date] = Field(None, description="First valid date")
    valid_to: Optional[date] = Field(None, description="Last valid date")


class FXPolicyResponse(BaseModel):
    """Active FX policy exposed by the API."""

    id: str = Field(..., description="FX policy identifier")
    name: str = Field(..., description="Human-readable policy name")
    provider: str = Field(..., description="Rate provider")
    rate_type: str = Field(..., description="Rate type, for example reference")
    selection_mode: str = Field(..., description="Date/period rate selection mode")
    business_day_rule: str = Field(..., description="Business-day handling rule")
    triangulation_allowed: bool = Field(
        ..., description="Whether triangulation is allowed"
    )
    fallback_behavior: str = Field(..., description="Fallback behavior")
    rounding_scale: int = Field(..., ge=0, description="Decimal rounding scale")
    rounding_mode: str = Field(..., description="Decimal rounding mode")


class FXRateCoverageResponse(BaseModel):
    """FX rate observation returned by coverage checks."""

    id: Union[int, str] = Field(..., description="Rate observation identifier")
    base_currency: str = Field(..., min_length=3, max_length=3)
    quote_currency: str = Field(..., min_length=3, max_length=3)
    rate_date: date
    rate_value: Union[float, int, Decimal]
    provider: str
    rate_type: str
    source_hash: Optional[str] = None

    @field_serializer("rate_value")
    def _serialize_rate_value(self, v: float | int | Decimal):
        return _decimal_to_float(v)


class FXConversionRequest(BaseModel):
    """Request model for FX conversion preview."""

    value: Decimal = Field(..., description="Value to convert")
    from_currency: str = Field(..., min_length=3, max_length=3)
    to_currency: str = Field(..., min_length=3, max_length=3)
    value_date: date = Field(..., description="Transaction/value date")
    period_start: Optional[date] = Field(None, description="Reporting period start")
    period_end: Optional[date] = Field(None, description="Reporting period end")
    fx_policy_id: str = Field(..., description="FX policy identifier")

    @field_serializer("value")
    def _serialize_value(self, v: Decimal):
        return _decimal_to_float(v)

    @field_validator("from_currency", "to_currency", mode="before")
    @classmethod
    def _normalize_currency_code(cls, value: str) -> str:
        return _normalize_fx_currency_code(value)


class FXConversionResponse(BaseModel):
    """Response model for FX conversion preview."""

    original_value: Decimal
    converted_value: Decimal
    from_currency: str
    to_currency: str
    trace: List[Dict[str, Any]]

    @field_serializer("original_value", "converted_value")
    def _serialize_decimal_fields(self, v: Decimal):
        return _decimal_to_float(v)


class FXRateImportRow(BaseModel):
    """Single controlled FX rate row."""

    quote_currency: str = Field(..., min_length=3, max_length=3)
    rate_date: date
    rate_value: Decimal = Field(..., gt=0)
    observed_at: Optional[datetime] = None
    rate_metadata: Optional[Dict[str, Any]] = None

    @field_validator("quote_currency", mode="before")
    @classmethod
    def _normalize_quote_currency(cls, value: str) -> str:
        return _normalize_fx_currency_code(value)


class FXRateImportRequest(BaseModel):
    """Controlled CSV-like FX rate import request."""

    provider: str = Field(..., min_length=1)
    rate_type: str = Field(..., min_length=1)
    base_currency: str = Field(..., min_length=3, max_length=3)
    source_hash: str = Field(..., min_length=64, max_length=64)
    rows: List[FXRateImportRow] = Field(..., min_length=1, max_length=10_000)

    @field_validator("base_currency", mode="before")
    @classmethod
    def _normalize_base_currency(cls, value: str) -> str:
        return _normalize_fx_currency_code(value)

    @field_validator("source_hash")
    @classmethod
    def _validate_source_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdefABCDEF" for character in value):
            raise ValueError("source_hash must be a 64-character SHA-256 hex digest")
        return value.lower()


class FXRateImportResponse(BaseModel):
    """Summary returned after a controlled FX rate import."""

    batch_id: Union[int, str, None] = Field(None, description="Created rate batch id")
    imported_rows: int = Field(..., ge=0, description="Number of submitted rate rows")
    created_rows: int = Field(..., ge=0, description="Rows created")
    updated_rows: int = Field(..., ge=0, description="Rows updated")
    unchanged_rows: int = Field(..., ge=0, description="Rows already current")
    provider: str
    rate_type: str
    base_currency: str


def _normalize_fx_currency_code(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if len(normalized) != 3 or not normalized.isalpha() or not normalized.isascii():
        raise ValueError("currency code must be exactly 3 uppercase ASCII letters")
    return normalized


# === COMMON RESPONSE MODELS ===


class ErrorResponse(BaseModel):
    """Model for error responses."""

    error: Dict[str, Any] = Field(..., description="Error information")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {
                    "code": 400,
                    "message": "Invalid input data",
                    "request_id": "123e4567-e89b-12d3-a456-426614174000",
                }
            }
        }
    )


class SuccessResponse(BaseModel):
    """Model for success responses."""

    success: bool = Field(True, description="Operation success status")
    message: str = Field(..., description="Success message")
    data: Optional[Any] = Field(None, description="Response data")


class PaginatedResponse(BaseModel):
    """Model for paginated responses."""

    items: List[Any] = Field(..., description="List of items")
    total: int = Field(..., description="Total number of items")
    page: int = Field(..., description="Current page number")
    size: int = Field(..., description="Page size")
    pages: int = Field(..., description="Total number of pages")
    next_cursor: Optional[str] = Field(
        None,
        description="Opaque cursor for the next page when cursor pagination is used",
    )
    has_more: Optional[bool] = Field(
        None, description="Whether more records are available after this page"
    )
    pins: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Public temporal-read reproducibility pins (resolved_decision_commit_id, "
            "valid_time_slice, effective_version_set_hash, replay_manifest_id/version/hash, "
            "catalog_projection_version); populated only when a bitemporal selector is supplied."
        ),
    )
