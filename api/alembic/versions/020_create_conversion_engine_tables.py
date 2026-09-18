"""Create conversion engine tables.

Revision ID: 020_create_conversion_engine_tables
Revises: 019_widen_calculation_contract_version
Create Date: 2026-05-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "020_create_conversion_engine_tables"
down_revision = "019_widen_calculation_contract_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns to existing unit tables
    op.add_column("units", sa.Column("dimension_vector", postgresql.JSONB(), nullable=True))
    op.add_column("units", sa.Column("source_system", sa.String(length=100), nullable=True))
    op.add_column("units", sa.Column("source_version", sa.String(length=100), nullable=True))
    op.add_column("units", sa.Column("valid_from", sa.Date(), nullable=True))
    op.add_column("units", sa.Column("valid_to", sa.Date(), nullable=True))

    op.add_column("conversion_rules", sa.Column("rule_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_conversion_rules_rule_hash", "conversion_rules", ["rule_hash"])
    op.add_column("conversion_rules", sa.Column("priority", sa.Integer(), nullable=False, server_default="100"))
    op.add_column("conversion_rules", sa.Column("valid_from", sa.Date(), nullable=True))
    op.add_column("conversion_rules", sa.Column("valid_to", sa.Date(), nullable=True))
    op.add_column("conversion_rules", sa.Column("source_system", sa.String(length=100), nullable=True))
    op.add_column("conversion_rules", sa.Column("source_version", sa.String(length=100), nullable=True))

    # Add row-level value conversion provenance and observation-date metadata.
    op.add_column("esg_values", sa.Column("original_value", sa.Numeric(precision=30, scale=10), nullable=True))
    op.add_column("esg_values", sa.Column("currency", sa.String(length=3), nullable=True))
    op.add_column("esg_values", sa.Column("original_currency", sa.String(length=3), nullable=True))
    op.add_column(
        "esg_values",
        sa.Column(
            "currency_conversion_applied",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column("esg_values", sa.Column("conversion_trace", postgresql.JSONB(), nullable=True))
    op.add_column("esg_values", sa.Column("value_date", sa.Date(), nullable=True))
    op.add_column("esg_values", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("esg_values", sa.Column("period_end", sa.Date(), nullable=True))

    # Create FX tables
    op.create_table(
        "currencies",
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("numeric_code", sa.String(length=3), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("redenomination_of", sa.String(length=3), nullable=True),
        sa.Column("redenomination_factor", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("currency_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_index("ix_currencies_numeric_code", "currencies", ["numeric_code"])

    op.create_table(
        "fx_rate_batches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("rate_type", sa.String(length=50), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("imported_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("batch_metadata", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fx_rate_batches_provider", "fx_rate_batches", ["provider"])
    op.create_index("ix_fx_rate_batches_rate_type", "fx_rate_batches", ["rate_type"])
    op.create_index("ix_fx_rate_batches_base_currency", "fx_rate_batches", ["base_currency"])
    op.create_index("ix_fx_rate_batches_source_hash", "fx_rate_batches", ["source_hash"])

    op.create_table(
        "fx_rate_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("rate_value", sa.Numeric(precision=30, scale=12), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("rate_type", sa.String(length=50), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("rate_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["fx_rate_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "rate_type", "base_currency", "quote_currency", "rate_date",
            name="uq_fx_rate_observation_key",
        ),
    )
    op.create_index("ix_fx_rate_observations_batch_id", "fx_rate_observations", ["batch_id"])
    op.create_index("ix_fx_rate_observations_base_currency", "fx_rate_observations", ["base_currency"])
    op.create_index("ix_fx_rate_observations_quote_currency", "fx_rate_observations", ["quote_currency"])
    op.create_index("ix_fx_rate_observations_rate_date", "fx_rate_observations", ["rate_date"])
    op.create_index("ix_fx_rate_observations_provider", "fx_rate_observations", ["provider"])
    op.create_index("ix_fx_rate_observations_rate_type", "fx_rate_observations", ["rate_type"])
    op.create_index(
        "ix_fx_rate_observations_lookup",
        "fx_rate_observations",
        ["provider", "rate_type", "base_currency", "quote_currency", "rate_date"],
    )

    op.create_table(
        "fx_rate_periods",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("rate_type", sa.String(length=50), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("period_type", sa.String(length=20), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("rate_value", sa.Numeric(precision=30, scale=12), nullable=False),
        sa.Column("source_observation_ids", postgresql.JSONB(), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "rate_type", "base_currency", "quote_currency",
            "period_type", "period_start", "period_end",
            name="uq_fx_rate_period_key",
        ),
    )
    op.create_index("ix_fx_rate_periods_provider", "fx_rate_periods", ["provider"])
    op.create_index("ix_fx_rate_periods_rate_type", "fx_rate_periods", ["rate_type"])
    op.create_index("ix_fx_rate_periods_base_currency", "fx_rate_periods", ["base_currency"])
    op.create_index("ix_fx_rate_periods_period_type", "fx_rate_periods", ["period_type"])
    op.create_index("ix_fx_rate_periods_period_start", "fx_rate_periods", ["period_start"])
    op.create_index(
        "ix_fx_rate_periods_lookup",
        "fx_rate_periods",
        [
            "provider",
            "rate_type",
            "base_currency",
            "quote_currency",
            "period_type",
            "period_start",
            "period_end",
        ],
    )

    op.create_table(
        "fx_policies",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("rate_type", sa.String(length=50), nullable=False),
        sa.Column("selection_mode", sa.String(length=50), nullable=False),
        sa.Column("business_day_rule", sa.String(length=50), server_default="previous_available", nullable=False),
        sa.Column("triangulation_allowed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("fallback_behavior", sa.String(length=50), server_default="fail_closed", nullable=False),
        sa.Column("rounding_scale", sa.Integer(), server_default="6", nullable=False),
        sa.Column("rounding_mode", sa.String(length=50), server_default="ROUND_HALF_UP", nullable=False),
        sa.Column("policy_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add calculation-contract currency policy metadata after fx_policies exists.
    op.add_column(
        "canonical_calculation_contracts",
        sa.Column("result_currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "canonical_calculation_contracts",
        sa.Column(
            "conversion_policy",
            sa.String(length=50),
            server_default="fail_closed",
            nullable=False,
        ),
    )
    op.add_column(
        "canonical_calculation_components",
        sa.Column("currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "canonical_calculation_components",
        sa.Column("expected_currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "canonical_calculation_components",
        sa.Column(
            "conversion_policy",
            sa.String(length=50),
            server_default="fail_closed",
            nullable=False,
        ),
    )
    op.add_column(
        "canonical_calculation_components",
        sa.Column("fx_policy_id", sa.String(length=100), nullable=True),
    )
    op.create_foreign_key(
        "fk_canonical_calc_components_fx_policy_id",
        "canonical_calculation_components",
        "fx_policies",
        ["fx_policy_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_canonical_calc_components_fx_policy_id",
        "canonical_calculation_components",
        type_="foreignkey",
    )
    op.drop_column("canonical_calculation_components", "fx_policy_id")
    op.drop_column("canonical_calculation_components", "conversion_policy")
    op.drop_column("canonical_calculation_components", "expected_currency")
    op.drop_column("canonical_calculation_components", "currency")
    op.drop_column("canonical_calculation_contracts", "conversion_policy")
    op.drop_column("canonical_calculation_contracts", "result_currency")

    op.drop_table("fx_policies")
    op.drop_table("fx_rate_periods")
    op.drop_table("fx_rate_observations")
    op.drop_table("fx_rate_batches")
    op.drop_table("currencies")

    op.drop_column("esg_values", "period_end")
    op.drop_column("esg_values", "period_start")
    op.drop_column("esg_values", "value_date")
    op.drop_column("esg_values", "conversion_trace")
    op.drop_column("esg_values", "currency_conversion_applied")
    op.drop_column("esg_values", "original_currency")
    op.drop_column("esg_values", "currency")
    op.drop_column("esg_values", "original_value")

    op.drop_column("conversion_rules", "source_version")
    op.drop_column("conversion_rules", "source_system")
    op.drop_column("conversion_rules", "valid_to")
    op.drop_column("conversion_rules", "valid_from")
    op.drop_column("conversion_rules", "priority")
    op.drop_index("ix_conversion_rules_rule_hash", table_name="conversion_rules")
    op.drop_column("conversion_rules", "rule_hash")

    op.drop_column("units", "valid_to")
    op.drop_column("units", "valid_from")
    op.drop_column("units", "source_version")
    op.drop_column("units", "source_system")
    op.drop_column("units", "dimension_vector")
