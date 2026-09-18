"""Create calculation contract import tables.

Revision ID: 018_create_calculation_contract_tables
Revises: 017_widen_indicator_code_esrs
Create Date: 2026-05-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "018_create_calculation_contract_tables"
down_revision = "017_widen_indicator_code_esrs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "atomizer_package_imports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("package_id", sa.String(length=200), nullable=True),
        sa.Column("package_version", sa.String(length=100), nullable=True),
        sa.Column("package_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("register_sha256", sa.String(length=64), nullable=True),
        sa.Column("calculation_contract_sha256", sa.String(length=64), nullable=True),
        sa.Column("values_sha256", sa.String(length=64), nullable=True),
        sa.Column("import_mode", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("register_row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("contract_node_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "executable_contract_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("semantic_only_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("audit_only_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_package", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("file_hashes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_hash", name="uq_atomizer_package_imports_hash"),
    )
    op.create_index(
        "ix_atomizer_package_imports_package_id",
        "atomizer_package_imports",
        ["package_id"],
        unique=False,
    )
    op.create_index(
        "ix_atomizer_package_imports_package_hash",
        "atomizer_package_imports",
        ["package_hash"],
        unique=False,
    )
    op.create_index(
        "ix_atomizer_package_imports_manifest_hash",
        "atomizer_package_imports",
        ["manifest_hash"],
        unique=False,
    )
    op.create_index(
        "ix_atomizer_package_imports_status",
        "atomizer_package_imports",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_atomizer_package_imports_status_created",
        "atomizer_package_imports",
        ["status", "created_at"],
        unique=False,
    )

    op.create_table(
        "canonical_calculation_contracts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("package_import_id", sa.Integer(), nullable=False),
        sa.Column("contract_version", sa.String(length=100), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("node_id", sa.String(length=500), nullable=False),
        sa.Column("canonical_datapoint_id", sa.String(length=500), nullable=True),
        sa.Column("indicator_id", sa.String(), nullable=True),
        sa.Column("indicator_identifier", sa.String(length=500), nullable=True),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("exposure", sa.String(length=50), nullable=False),
        sa.Column("runtime_status", sa.String(length=50), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=True),
        sa.Column("source_kind", sa.String(length=80), nullable=True),
        sa.Column("formula_kind", sa.String(length=50), nullable=True),
        sa.Column("semantic_expression", sa.Text(), nullable=True),
        sa.Column("runtime_expression", sa.Text(), nullable=True),
        sa.Column("value_kind", sa.String(length=50), nullable=True),
        sa.Column("unit_name", sa.String(length=120), nullable=True),
        sa.Column("unit_type", sa.String(length=120), nullable=True),
        sa.Column("parent_node_id", sa.String(length=500), nullable=True),
        sa.Column("relation_to_parent", sa.String(length=100), nullable=True),
        sa.Column(
            "aggregation_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "completeness_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("gate_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "support_rule_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("effective_from", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("effective_to", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("contract_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["indicator_id"], ["indicators.id"]),
        sa.ForeignKeyConstraint(["package_import_id"], ["atomizer_package_imports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_canonical_calculation_contracts_package_import_id",
        "canonical_calculation_contracts",
        ["package_import_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_contracts_model_id",
        "canonical_calculation_contracts",
        ["model_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_contracts_node_id",
        "canonical_calculation_contracts",
        ["node_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_contracts_canonical_datapoint_id",
        "canonical_calculation_contracts",
        ["canonical_datapoint_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_contracts_indicator_id",
        "canonical_calculation_contracts",
        ["indicator_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_contracts_indicator_identifier",
        "canonical_calculation_contracts",
        ["indicator_identifier"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_contracts_exposure",
        "canonical_calculation_contracts",
        ["exposure"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_contracts_runtime_status",
        "canonical_calculation_contracts",
        ["runtime_status"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_contracts_parent_node_id",
        "canonical_calculation_contracts",
        ["parent_node_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_contracts_contract_hash",
        "canonical_calculation_contracts",
        ["contract_hash"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calc_contracts_active_indicator",
        "canonical_calculation_contracts",
        ["indicator_id"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE AND indicator_id IS NOT NULL"),
    )
    op.create_index(
        "ix_canonical_calc_contracts_active_node",
        "canonical_calculation_contracts",
        ["node_id"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
    )
    op.create_index(
        "ix_canonical_calc_contracts_active_datapoint",
        "canonical_calculation_contracts",
        ["canonical_datapoint_id"],
        unique=True,
        postgresql_where=sa.text(
            "is_active IS TRUE AND canonical_datapoint_id IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_canonical_calc_contracts_active_hash",
        "canonical_calculation_contracts",
        ["contract_hash"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
    )

    op.create_table(
        "canonical_calculation_components",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("package_import_id", sa.Integer(), nullable=False),
        sa.Column("component_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("component_id", sa.String(length=500), nullable=False),
        sa.Column("component_node_id", sa.String(length=500), nullable=True),
        sa.Column("indicator_identifier", sa.String(length=500), nullable=True),
        sa.Column("component_scope", sa.String(length=80), nullable=True),
        sa.Column("variable_uri", sa.String(length=500), nullable=True),
        sa.Column("unit_name", sa.String(length=120), nullable=True),
        sa.Column("unit_type", sa.String(length=120), nullable=True),
        sa.Column("aggregation_method", sa.String(length=50), nullable=True),
        sa.Column("role", sa.String(length=50), nullable=True),
        sa.Column("required", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("numerator_denominator_role", sa.String(length=50), nullable=True),
        sa.Column("weight_hint", sa.String(length=200), nullable=True),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["contract_id"], ["canonical_calculation_contracts.id"]),
        sa.ForeignKeyConstraint(["package_import_id"], ["atomizer_package_imports.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "contract_id",
            "component_order",
            name="uq_canonical_calc_components_contract_order",
        ),
    )
    op.create_index(
        "ix_canonical_calculation_components_contract_id",
        "canonical_calculation_components",
        ["contract_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_components_package_import_id",
        "canonical_calculation_components",
        ["package_import_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_components_component_id",
        "canonical_calculation_components",
        ["component_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_components_component_node_id",
        "canonical_calculation_components",
        ["component_node_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_components_indicator_identifier",
        "canonical_calculation_components",
        ["indicator_identifier"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calc_components_component",
        "canonical_calculation_components",
        ["component_id", "component_node_id"],
        unique=False,
    )

    op.create_table(
        "canonical_calculation_dimensions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("package_import_id", sa.Integer(), nullable=False),
        sa.Column("dimension_id", sa.String(length=200), nullable=False),
        sa.Column("mode", sa.String(length=50), nullable=False),
        sa.Column("fixed_value", sa.String(length=500), nullable=True),
        sa.Column("member_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["contract_id"], ["canonical_calculation_contracts.id"]),
        sa.ForeignKeyConstraint(["package_import_id"], ["atomizer_package_imports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_canonical_calculation_dimensions_contract_id",
        "canonical_calculation_dimensions",
        ["contract_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_dimensions_package_import_id",
        "canonical_calculation_dimensions",
        ["package_import_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_dimensions_dimension_id",
        "canonical_calculation_dimensions",
        ["dimension_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calc_dimensions_contract_dimension",
        "canonical_calculation_dimensions",
        ["contract_id", "dimension_id"],
        unique=False,
    )

    op.create_table(
        "canonical_calculation_gates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("package_import_id", sa.Integer(), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=True),
        sa.Column("gate_id", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["contract_id"], ["canonical_calculation_contracts.id"]),
        sa.ForeignKeyConstraint(["package_import_id"], ["atomizer_package_imports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_canonical_calculation_gates_package_import_id",
        "canonical_calculation_gates",
        ["package_import_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_gates_contract_id",
        "canonical_calculation_gates",
        ["contract_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_gates_gate_id",
        "canonical_calculation_gates",
        ["gate_id"],
        unique=False,
    )

    op.create_table(
        "canonical_calculation_support_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("package_import_id", sa.Integer(), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=True),
        sa.Column("support_rule_id", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["contract_id"], ["canonical_calculation_contracts.id"]),
        sa.ForeignKeyConstraint(["package_import_id"], ["atomizer_package_imports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_canonical_calculation_support_rules_package_import_id",
        "canonical_calculation_support_rules",
        ["package_import_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_support_rules_contract_id",
        "canonical_calculation_support_rules",
        ["contract_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_calculation_support_rules_support_rule_id",
        "canonical_calculation_support_rules",
        ["support_rule_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canonical_calculation_support_rules_support_rule_id",
        table_name="canonical_calculation_support_rules",
    )
    op.drop_index(
        "ix_canonical_calculation_support_rules_contract_id",
        table_name="canonical_calculation_support_rules",
    )
    op.drop_index(
        "ix_canonical_calculation_support_rules_package_import_id",
        table_name="canonical_calculation_support_rules",
    )
    op.drop_table("canonical_calculation_support_rules")

    op.drop_index(
        "ix_canonical_calculation_gates_gate_id",
        table_name="canonical_calculation_gates",
    )
    op.drop_index(
        "ix_canonical_calculation_gates_contract_id",
        table_name="canonical_calculation_gates",
    )
    op.drop_index(
        "ix_canonical_calculation_gates_package_import_id",
        table_name="canonical_calculation_gates",
    )
    op.drop_table("canonical_calculation_gates")

    op.drop_index(
        "ix_canonical_calc_dimensions_contract_dimension",
        table_name="canonical_calculation_dimensions",
    )
    op.drop_index(
        "ix_canonical_calculation_dimensions_dimension_id",
        table_name="canonical_calculation_dimensions",
    )
    op.drop_index(
        "ix_canonical_calculation_dimensions_package_import_id",
        table_name="canonical_calculation_dimensions",
    )
    op.drop_index(
        "ix_canonical_calculation_dimensions_contract_id",
        table_name="canonical_calculation_dimensions",
    )
    op.drop_table("canonical_calculation_dimensions")

    op.drop_index(
        "ix_canonical_calc_components_component",
        table_name="canonical_calculation_components",
    )
    op.drop_index(
        "ix_canonical_calculation_components_indicator_identifier",
        table_name="canonical_calculation_components",
    )
    op.drop_index(
        "ix_canonical_calculation_components_component_node_id",
        table_name="canonical_calculation_components",
    )
    op.drop_index(
        "ix_canonical_calculation_components_component_id",
        table_name="canonical_calculation_components",
    )
    op.drop_index(
        "ix_canonical_calculation_components_package_import_id",
        table_name="canonical_calculation_components",
    )
    op.drop_index(
        "ix_canonical_calculation_components_contract_id",
        table_name="canonical_calculation_components",
    )
    op.drop_table("canonical_calculation_components")

    op.drop_index(
        "ix_canonical_calc_contracts_active_hash",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calc_contracts_active_datapoint",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calc_contracts_active_node",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calc_contracts_active_indicator",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calculation_contracts_contract_hash",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calculation_contracts_parent_node_id",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calculation_contracts_runtime_status",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calculation_contracts_exposure",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calculation_contracts_indicator_identifier",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calculation_contracts_indicator_id",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calculation_contracts_canonical_datapoint_id",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calculation_contracts_node_id",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calculation_contracts_model_id",
        table_name="canonical_calculation_contracts",
    )
    op.drop_index(
        "ix_canonical_calculation_contracts_package_import_id",
        table_name="canonical_calculation_contracts",
    )
    op.drop_table("canonical_calculation_contracts")

    op.drop_index(
        "ix_atomizer_package_imports_status_created",
        table_name="atomizer_package_imports",
    )
    op.drop_index("ix_atomizer_package_imports_status", table_name="atomizer_package_imports")
    op.drop_index(
        "ix_atomizer_package_imports_manifest_hash",
        table_name="atomizer_package_imports",
    )
    op.drop_index(
        "ix_atomizer_package_imports_package_hash",
        table_name="atomizer_package_imports",
    )
    op.drop_index(
        "ix_atomizer_package_imports_package_id",
        table_name="atomizer_package_imports",
    )
    op.drop_table("atomizer_package_imports")
