"""Baseline schema before semantic-state remediation."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "001_baseline_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hierarchy_configurations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("hierarchy_type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("configuration", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hierarchy_configurations_company_id",
        "hierarchy_configurations",
        ["company_id"],
    )

    op.create_table(
        "esg_values",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("concept", sa.String(), nullable=False),
        sa.Column("entity", sa.String(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(30, 10), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("original_unit", sa.String(), nullable=True),
        sa.Column("conversion_applied", sa.Boolean(), nullable=True),
        sa.Column(
            "value_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_esg_values_concept", "esg_values", ["concept"])
    op.create_index("ix_esg_values_entity", "esg_values", ["entity"])
    op.create_index("ix_esg_values_period", "esg_values", ["period"])
    op.create_index(
        "ix_esg_values_concept_entity_period",
        "esg_values",
        ["concept", "entity", "period"],
    )

    op.create_table(
        "user_accounts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(length=100), nullable=True),
        sa.Column("company_id", sa.String(), nullable=True),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("last_login", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_user_accounts_company_id", "user_accounts", ["company_id"])
    op.create_index("ix_user_accounts_username", "user_accounts", ["username"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("secret_hash", sa.String(), nullable=False),
        sa.Column(
            "permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_used", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_is_active", "api_keys", ["is_active"])

    op.create_table(
        "sync_config",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("auto_sync_on_startup", sa.Boolean(), nullable=True),
        sa.Column("auto_sync_interval_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "sync_state",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("last_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_completed_at", sa.DateTime(), nullable=True),
        sa.Column("last_success", sa.Boolean(), nullable=True),
        sa.Column(
            "last_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "unit_categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("base_unit", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("special_conversions", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "units",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("conversion_factor", sa.Numeric(40, 18), nullable=False),
        sa.Column("conversion_offset", sa.Numeric(40, 18), nullable=True),
        sa.Column("aliases", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column(
            "unit_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["category_id"], ["unit_categories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol"),
    )
    op.create_index("ix_units_category_id", "units", ["category_id"])
    op.create_index("ix_units_symbol", "units", ["symbol"])

    op.create_table(
        "conversion_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("from_unit", sa.String(length=20), nullable=False),
        sa.Column("to_unit", sa.String(length=20), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("reverse_formula", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "rule_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversion_rules_from_to", "conversion_rules", ["from_unit", "to_unit"]
    )

    op.create_table(
        "metric_types",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("aggregation_method", sa.String(length=20), nullable=False),
        sa.Column("default_unit", sa.String(length=20), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("requires_weights", sa.Boolean(), nullable=True),
        sa.Column("weight_variable", sa.String(length=100), nullable=True),
        sa.Column(
            "metric_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "indicators",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("identifier", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("indicator_name", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("dimension", sa.String(length=20), nullable=False),
        sa.Column("unit_name", sa.String(length=120), nullable=True),
        sa.Column("unit_type", sa.String(length=120), nullable=True),
        sa.Column("periodicity", sa.String(length=20), nullable=True),
        sa.Column("period_type", sa.String(length=20), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("code_esrs", sa.String(length=50), nullable=True),
        sa.Column("code_gri", sa.String(length=150), nullable=True),
        sa.Column("code_gri_expanded", sa.String(length=200), nullable=True),
        sa.Column("evidence_path", sa.String(), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("owner", sa.String(length=100), nullable=True),
        sa.Column("access_rights", sa.String(length=50), nullable=True),
        sa.Column("validation_method", sa.String(length=100), nullable=True),
        sa.Column("double_materiality", sa.String(length=50), nullable=True),
        sa.Column("value_type", sa.String(length=20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identifier"),
    )
    op.create_index("ix_indicators_identifier", "indicators", ["identifier"])
    op.create_index("ix_indicators_dimension", "indicators", ["dimension"])
    op.create_index("ix_indicators_code_esrs", "indicators", ["code_esrs"])
    op.create_index("ix_indicators_code_gri", "indicators", ["code_gri"])
    op.create_index(
        "ix_indicators_dimension_esrs", "indicators", ["dimension", "code_esrs"]
    )
    op.create_index(
        "ix_indicators_dimension_gri", "indicators", ["dimension", "code_gri"]
    )

    op.create_table(
        "standard_mappings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_standard", sa.String(length=50), nullable=False),
        sa.Column("source_code", sa.String(length=150), nullable=False),
        sa.Column("source_label", sa.String(length=500), nullable=True),
        sa.Column("target_standard", sa.String(length=50), nullable=False),
        sa.Column("target_code", sa.String(length=150), nullable=True),
        sa.Column("target_label", sa.String(length=500), nullable=True),
        sa.Column("esg_dimension", sa.String(length=20), nullable=True),
        sa.Column("relationship_type", sa.String(length=30), nullable=True),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("dataset", sa.String(length=100), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column(
            "mapping_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_standard_mappings_source_standard", "standard_mappings", ["source_standard"]
    )
    op.create_index(
        "ix_standard_mappings_source_code", "standard_mappings", ["source_code"]
    )
    op.create_index(
        "ix_standard_mappings_target_standard", "standard_mappings", ["target_standard"]
    )
    op.create_index(
        "ix_standard_mappings_target_code", "standard_mappings", ["target_code"]
    )
    op.create_index(
        "ix_standard_mappings_esg_dimension", "standard_mappings", ["esg_dimension"]
    )
    op.create_index(
        "ix_standard_mappings_source",
        "standard_mappings",
        ["source_standard", "source_code"],
    )
    op.create_index(
        "ix_standard_mappings_target",
        "standard_mappings",
        ["target_standard", "target_code"],
    )
    op.create_index(
        "ix_standard_mappings_standards_pair",
        "standard_mappings",
        ["source_standard", "target_standard"],
    )
    op.create_index(
        "ix_standard_mappings_dimension", "standard_mappings", ["esg_dimension"]
    )

    op.create_table(
        "sustainability_standards",
        sa.Column("id", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("organization", sa.String(length=200), nullable=True),
        sa.Column("version", sa.String(length=50), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "revoked_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_revoked_tokens_token_hash", "revoked_tokens", ["token_hash"])


def downgrade() -> None:
    op.drop_index("ix_revoked_tokens_token_hash", table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
    op.drop_table("sustainability_standards")
    op.drop_index("ix_standard_mappings_dimension", table_name="standard_mappings")
    op.drop_index("ix_standard_mappings_standards_pair", table_name="standard_mappings")
    op.drop_index("ix_standard_mappings_target", table_name="standard_mappings")
    op.drop_index("ix_standard_mappings_source", table_name="standard_mappings")
    op.drop_index("ix_standard_mappings_esg_dimension", table_name="standard_mappings")
    op.drop_index("ix_standard_mappings_target_code", table_name="standard_mappings")
    op.drop_index(
        "ix_standard_mappings_target_standard", table_name="standard_mappings"
    )
    op.drop_index("ix_standard_mappings_source_code", table_name="standard_mappings")
    op.drop_index(
        "ix_standard_mappings_source_standard", table_name="standard_mappings"
    )
    op.drop_table("standard_mappings")
    op.drop_index("ix_indicators_dimension_gri", table_name="indicators")
    op.drop_index("ix_indicators_dimension_esrs", table_name="indicators")
    op.drop_index("ix_indicators_code_gri", table_name="indicators")
    op.drop_index("ix_indicators_code_esrs", table_name="indicators")
    op.drop_index("ix_indicators_dimension", table_name="indicators")
    op.drop_index("ix_indicators_identifier", table_name="indicators")
    op.drop_table("indicators")
    op.drop_table("metric_types")
    op.drop_index("ix_conversion_rules_from_to", table_name="conversion_rules")
    op.drop_table("conversion_rules")
    op.drop_index("ix_units_symbol", table_name="units")
    op.drop_index("ix_units_category_id", table_name="units")
    op.drop_table("units")
    op.drop_table("unit_categories")
    op.drop_table("sync_state")
    op.drop_table("sync_config")
    op.drop_index("ix_api_keys_is_active", table_name="api_keys")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_user_accounts_username", table_name="user_accounts")
    op.drop_index("ix_user_accounts_company_id", table_name="user_accounts")
    op.drop_table("user_accounts")
    op.drop_index("ix_esg_values_concept_entity_period", table_name="esg_values")
    op.drop_index("ix_esg_values_period", table_name="esg_values")
    op.drop_index("ix_esg_values_entity", table_name="esg_values")
    op.drop_index("ix_esg_values_concept", table_name="esg_values")
    op.drop_table("esg_values")
    op.drop_index(
        "ix_hierarchy_configurations_company_id", table_name="hierarchy_configurations"
    )
    op.drop_table("hierarchy_configurations")
