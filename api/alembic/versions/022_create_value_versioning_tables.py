"""Create value versioning tables.

Revision ID: 022_create_value_versioning_tables
Revises: 021_add_canonical_mapping_package_jobs
Create Date: 2026-05-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "022_create_value_versioning_tables"
down_revision = "021_canonical_mapping_package_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "value_contexts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("context_hash_recipe_version", sa.String(length=50), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=200), nullable=False),
        sa.Column("reporting_period_id", sa.String(length=100), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("period_close_date", sa.Date(), nullable=True),
        sa.Column("period_type", sa.String(length=50), nullable=False),
        sa.Column("reporting_boundary_id", sa.String(length=200), nullable=False),
        sa.Column("sds_indicator_id", sa.String(), nullable=True),
        sa.Column("indicator_identifier", sa.String(length=500), nullable=False),
        sa.Column("standard_release_id", sa.String(length=100), nullable=False),
        sa.Column("standard_datapoint_id", sa.String(length=200), nullable=False),
        sa.Column("dimensions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "scenario_basis",
            sa.String(length=50),
            server_default="actual",
            nullable=False,
        ),
        sa.Column("value_kind", sa.String(length=50), nullable=False),
        sa.Column("expected_unit", sa.String(length=120), nullable=True),
        sa.Column("expected_currency", sa.String(length=3), nullable=True),
        sa.Column("identity_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["sds_indicator_id"], ["indicators.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "context_hash",
            name="uq_value_contexts_tenant_hash",
        ),
    )
    op.create_index("ix_value_contexts_tenant_id", "value_contexts", ["tenant_id"])
    op.create_index(
        "ix_value_contexts_context_hash",
        "value_contexts",
        ["context_hash"],
    )
    op.create_index("ix_value_contexts_entity_id", "value_contexts", ["entity_id"])
    op.create_index(
        "ix_value_contexts_reporting_period_id",
        "value_contexts",
        ["reporting_period_id"],
    )
    op.create_index(
        "ix_value_contexts_indicator_identifier",
        "value_contexts",
        ["indicator_identifier"],
    )
    op.create_index(
        "ix_value_contexts_standard_release_id",
        "value_contexts",
        ["standard_release_id"],
    )
    op.create_index(
        "ix_value_contexts_standard_datapoint_id",
        "value_contexts",
        ["standard_datapoint_id"],
    )
    op.create_index(
        "ix_value_contexts_sds_indicator_id",
        "value_contexts",
        ["sds_indicator_id"],
    )
    op.create_index(
        "ix_value_contexts_indicator_period",
        "value_contexts",
        ["indicator_identifier", "reporting_period_id"],
    )

    op.create_table(
        "value_revisions",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("context_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("value_kind", sa.String(length=50), nullable=False),
        sa.Column("canonical_value", sa.Text(), nullable=True),
        sa.Column("numeric_value", sa.Numeric(38, 12), nullable=True),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("boolean_value", sa.Boolean(), nullable=True),
        sa.Column("unit", sa.String(length=120), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("original_value", sa.Text(), nullable=True),
        sa.Column("original_unit", sa.String(length=120), nullable=True),
        sa.Column("original_currency", sa.String(length=3), nullable=True),
        sa.Column("source_system", sa.String(length=100), nullable=True),
        sa.Column("source_record_id", sa.String(length=100), nullable=True),
        sa.Column("external_key", sa.String(length=255), nullable=True),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "materiality_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("calculation_contract_id", sa.Integer(), nullable=True),
        sa.Column("formula_contract_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "input_revision_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("conversion_trace", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("trace_hash", sa.String(length=64), nullable=True),
        sa.Column("parent_revision_id", sa.String(length=100), nullable=True),
        sa.Column("revision_provenance", sa.String(length=80), nullable=True),
        sa.Column("source_payload_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["context_id"], ["value_contexts.id"]),
        sa.ForeignKeyConstraint(
            ["calculation_contract_id"],
            ["canonical_calculation_contracts.id"],
        ),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["value_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "context_id",
            "revision_number",
            name="uq_value_revisions_context_revision",
        ),
    )
    op.create_index("ix_value_revisions_tenant_id", "value_revisions", ["tenant_id"])
    op.create_index("ix_value_revisions_state", "value_revisions", ["state"])
    op.create_index(
        "ix_value_revisions_source_system", "value_revisions", ["source_system"]
    )
    op.create_index(
        "ix_value_revisions_source_record_id",
        "value_revisions",
        ["source_record_id"],
    )
    op.create_index("ix_value_revisions_external_key", "value_revisions", ["external_key"])
    op.create_index("ix_value_revisions_evidence_hash", "value_revisions", ["evidence_hash"])
    op.create_index(
        "ix_value_revisions_calculation_contract_id",
        "value_revisions",
        ["calculation_contract_id"],
    )
    op.create_index(
        "ix_value_revisions_formula_contract_hash",
        "value_revisions",
        ["formula_contract_hash"],
    )
    op.create_index("ix_value_revisions_trace_hash", "value_revisions", ["trace_hash"])
    op.create_index(
        "ix_value_revisions_revision_provenance",
        "value_revisions",
        ["revision_provenance"],
    )
    op.create_index(
        "ix_value_revisions_source_payload_hash",
        "value_revisions",
        ["source_payload_hash"],
    )
    op.create_index(
        "ix_value_revisions_source_observation",
        "value_revisions",
        ["tenant_id", "source_system", "external_key", "source_record_id"],
    )

    op.create_table(
        "value_revision_events",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("event_seq", sa.BigInteger(), nullable=False),
        sa.Column("context_event_seq", sa.Integer(), nullable=False),
        sa.Column("context_id", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.String(length=100), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("from_state", sa.String(length=30), nullable=True),
        sa.Column("to_state", sa.String(length=30), nullable=True),
        sa.Column(
            "pointer_moved",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("event_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column(
            "occurred_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("occurred_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["context_id"], ["value_contexts.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["value_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
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
    )
    op.create_index(
        "ix_value_revision_events_tenant_id",
        "value_revision_events",
        ["tenant_id"],
    )
    op.create_index(
        "ix_value_revision_events_revision_id",
        "value_revision_events",
        ["revision_id"],
    )
    op.create_index(
        "ix_value_revision_events_event_type",
        "value_revision_events",
        ["event_type"],
    )
    op.create_index(
        "ix_value_revision_events_feed",
        "value_revision_events",
        ["tenant_id", "event_seq", "id"],
    )

    op.create_table(
        "current_value_pointers",
        sa.Column("context_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("revision_id", sa.String(length=100), nullable=False),
        sa.Column(
            "pointer_basis",
            sa.String(length=50),
            server_default="current",
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["context_id"], ["value_contexts.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["value_revisions.id"]),
        sa.PrimaryKeyConstraint("context_id"),
    )
    op.create_index(
        "ix_current_value_pointers_tenant_id",
        "current_value_pointers",
        ["tenant_id"],
    )
    op.create_index(
        "ix_current_value_pointers_revision_id",
        "current_value_pointers",
        ["revision_id"],
    )

    op.create_table(
        "reported_value_pointers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.String(length=200), nullable=False),
        sa.Column("reporting_period_id", sa.String(length=100), nullable=False),
        sa.Column("standard_release_id", sa.String(length=100), nullable=False),
        sa.Column("report_snapshot_id", sa.String(length=200), nullable=False),
        sa.Column("context_id", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.String(length=100), nullable=False),
        sa.Column(
            "pointer_basis",
            sa.String(length=50),
            server_default="reported",
            nullable=False,
        ),
        sa.Column(
            "reported_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("reported_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["context_id"], ["value_contexts.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["value_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "entity_id",
            "reporting_period_id",
            "standard_release_id",
            "report_snapshot_id",
            "context_id",
            name="uq_reported_value_pointers_snapshot_context",
        ),
    )
    op.create_index(
        "ix_reported_value_pointers_tenant_id",
        "reported_value_pointers",
        ["tenant_id"],
    )
    op.create_index(
        "ix_reported_value_pointers_entity_id",
        "reported_value_pointers",
        ["entity_id"],
    )
    op.create_index(
        "ix_reported_value_pointers_reporting_period_id",
        "reported_value_pointers",
        ["reporting_period_id"],
    )
    op.create_index(
        "ix_reported_value_pointers_standard_release_id",
        "reported_value_pointers",
        ["standard_release_id"],
    )
    op.create_index(
        "ix_reported_value_pointers_report_snapshot_id",
        "reported_value_pointers",
        ["report_snapshot_id"],
    )
    op.create_index(
        "ix_reported_value_pointers_snapshot",
        "reported_value_pointers",
        ["tenant_id", "report_snapshot_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reported_value_pointers_snapshot",
        table_name="reported_value_pointers",
    )
    op.drop_index(
        "ix_reported_value_pointers_report_snapshot_id",
        table_name="reported_value_pointers",
    )
    op.drop_index(
        "ix_reported_value_pointers_standard_release_id",
        table_name="reported_value_pointers",
    )
    op.drop_index(
        "ix_reported_value_pointers_reporting_period_id",
        table_name="reported_value_pointers",
    )
    op.drop_index(
        "ix_reported_value_pointers_entity_id",
        table_name="reported_value_pointers",
    )
    op.drop_index(
        "ix_reported_value_pointers_tenant_id",
        table_name="reported_value_pointers",
    )
    op.drop_table("reported_value_pointers")

    op.drop_index(
        "ix_current_value_pointers_revision_id",
        table_name="current_value_pointers",
    )
    op.drop_index(
        "ix_current_value_pointers_tenant_id",
        table_name="current_value_pointers",
    )
    op.drop_table("current_value_pointers")

    op.drop_index(
        "ix_value_revision_events_feed",
        table_name="value_revision_events",
    )
    op.drop_index(
        "ix_value_revision_events_event_type",
        table_name="value_revision_events",
    )
    op.drop_index(
        "ix_value_revision_events_revision_id",
        table_name="value_revision_events",
    )
    op.drop_index(
        "ix_value_revision_events_tenant_id",
        table_name="value_revision_events",
    )
    op.drop_table("value_revision_events")

    op.drop_index(
        "ix_value_revisions_source_observation",
        table_name="value_revisions",
    )
    op.drop_index(
        "ix_value_revisions_source_payload_hash",
        table_name="value_revisions",
    )
    op.drop_index(
        "ix_value_revisions_revision_provenance",
        table_name="value_revisions",
    )
    op.drop_index("ix_value_revisions_trace_hash", table_name="value_revisions")
    op.drop_index(
        "ix_value_revisions_formula_contract_hash",
        table_name="value_revisions",
    )
    op.drop_index(
        "ix_value_revisions_calculation_contract_id",
        table_name="value_revisions",
    )
    op.drop_index("ix_value_revisions_evidence_hash", table_name="value_revisions")
    op.drop_index("ix_value_revisions_external_key", table_name="value_revisions")
    op.drop_index("ix_value_revisions_source_record_id", table_name="value_revisions")
    op.drop_index("ix_value_revisions_source_system", table_name="value_revisions")
    op.drop_index("ix_value_revisions_state", table_name="value_revisions")
    op.drop_index("ix_value_revisions_tenant_id", table_name="value_revisions")
    op.drop_table("value_revisions")

    op.drop_index(
        "ix_value_contexts_indicator_period",
        table_name="value_contexts",
    )
    op.drop_index("ix_value_contexts_sds_indicator_id", table_name="value_contexts")
    op.drop_index(
        "ix_value_contexts_standard_datapoint_id",
        table_name="value_contexts",
    )
    op.drop_index(
        "ix_value_contexts_standard_release_id",
        table_name="value_contexts",
    )
    op.drop_index(
        "ix_value_contexts_indicator_identifier",
        table_name="value_contexts",
    )
    op.drop_index(
        "ix_value_contexts_reporting_period_id",
        table_name="value_contexts",
    )
    op.drop_index("ix_value_contexts_entity_id", table_name="value_contexts")
    op.drop_index("ix_value_contexts_context_hash", table_name="value_contexts")
    op.drop_index("ix_value_contexts_tenant_id", table_name="value_contexts")
    op.drop_table("value_contexts")
