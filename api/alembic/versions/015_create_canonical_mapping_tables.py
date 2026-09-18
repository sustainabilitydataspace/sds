"""Create canonical mapping knowledge-base tables.

Revision ID: 015_create_canonical_mapping_tables
Revises: 014_create_canonical_concept_tables
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "015_create_canonical_mapping_tables"
down_revision = "014_create_canonical_concept_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "standard_releases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("standard_id", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column(
            "lifecycle_status",
            sa.String(length=30),
            server_default="active",
            nullable=False,
        ),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "standard_id", "version", name="uq_standard_releases_standard_version"
        ),
    )
    op.create_index(
        "ix_standard_releases_standard_id",
        "standard_releases",
        ["standard_id"],
        unique=False,
    )
    op.create_index(
        "ix_standard_releases_standard_status",
        "standard_releases",
        ["standard_id", "lifecycle_status"],
        unique=False,
    )

    op.create_table(
        "standard_datapoints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("standard_release_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=150), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("disclosure_text", sa.Text(), nullable=True),
        sa.Column("datapoint_type", sa.String(length=50), nullable=True),
        sa.Column("unit", sa.String(length=100), nullable=True),
        sa.Column(
            "lifecycle_status",
            sa.String(length=30),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["standard_release_id"], ["standard_releases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "standard_release_id", "code", name="uq_standard_datapoints_release_code"
        ),
    )
    op.create_index(
        "ix_standard_datapoints_standard_release_id",
        "standard_datapoints",
        ["standard_release_id"],
        unique=False,
    )
    op.create_index(
        "ix_standard_datapoints_code", "standard_datapoints", ["code"], unique=False
    )
    op.create_index(
        "ix_standard_datapoints_release_status",
        "standard_datapoints",
        ["standard_release_id", "lifecycle_status"],
        unique=False,
    )

    op.create_table(
        "mapping_assertion_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_datapoint_id", sa.Integer(), nullable=False),
        sa.Column(
            "mapping_profile",
            sa.String(length=100),
            server_default="default",
            nullable=False,
        ),
        sa.Column(
            "relationship_type",
            sa.String(length=30),
            server_default="equivalent",
            nullable=False,
        ),
        sa.Column(
            "coverage_status",
            sa.String(length=30),
            server_default="complete",
            nullable=False,
        ),
        sa.Column("confidence", sa.Numeric(3, 2), server_default="1.0", nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("coverage_summary", sa.Text(), nullable=True),
        sa.Column("difference_summary", sa.Text(), nullable=True),
        sa.Column(
            "valid_from", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("valid_to", sa.DateTime(), nullable=True),
        sa.Column(
            "approval_status",
            sa.String(length=30),
            server_default="draft",
            nullable=False,
        ),
        sa.Column(
            "publication_status",
            sa.String(length=30),
            server_default="internal",
            nullable=False,
        ),
        sa.Column("assertion_hash", sa.String(length=64), nullable=True),
        sa.Column("package_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["package_snapshot_id"], ["dataset_snapshots.id"]),
        sa.ForeignKeyConstraint(["source_datapoint_id"], ["standard_datapoints.id"]),
        sa.ForeignKeyConstraint(["superseded_by"], ["mapping_assertion_groups.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mapping_assertion_groups_source_datapoint_id",
        "mapping_assertion_groups",
        ["source_datapoint_id"],
        unique=False,
    )
    op.create_index(
        "ix_mapping_assertion_groups_source_profile_status",
        "mapping_assertion_groups",
        ["source_datapoint_id", "mapping_profile", "approval_status"],
        unique=False,
    )
    op.create_index(
        "ix_mapping_assertion_groups_current_approved",
        "mapping_assertion_groups",
        ["source_datapoint_id", "mapping_profile"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL AND approval_status = 'approved'"),
    )
    op.create_index(
        "ix_mapping_assertion_groups_assertion_hash",
        "mapping_assertion_groups",
        ["assertion_hash"],
        unique=False,
    )

    op.create_table(
        "mapping_assertion_components",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("assertion_group_id", sa.Integer(), nullable=False),
        sa.Column("canonical_concept_id", sa.Integer(), nullable=False),
        sa.Column("component_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "component_role",
            sa.String(length=30),
            server_default="primary",
            nullable=False,
        ),
        sa.Column("coverage_fraction", sa.Numeric(5, 4), nullable=True),
        sa.Column("match_scope", sa.Text(), nullable=True),
        sa.Column("mismatch_scope", sa.Text(), nullable=True),
        sa.Column("transformation_rule", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["assertion_group_id"], ["mapping_assertion_groups.id"]
        ),
        sa.ForeignKeyConstraint(["canonical_concept_id"], ["canonical_concepts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assertion_group_id",
            "component_order",
            name="uq_mapping_assertion_components_group_order",
        ),
    )
    op.create_index(
        "ix_mapping_assertion_components_assertion_group_id",
        "mapping_assertion_components",
        ["assertion_group_id"],
        unique=False,
    )
    op.create_index(
        "ix_mapping_assertion_components_canonical_concept_id",
        "mapping_assertion_components",
        ["canonical_concept_id"],
        unique=False,
    )
    op.create_index(
        "ix_mapping_assertion_components_group_concept",
        "mapping_assertion_components",
        ["assertion_group_id", "canonical_concept_id"],
        unique=False,
    )

    op.create_table(
        "materialized_pairwise_mappings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_datapoint_id", sa.Integer(), nullable=False),
        sa.Column("target_datapoint_id", sa.Integer(), nullable=False),
        sa.Column("source_assertion_group_id", sa.Integer(), nullable=False),
        sa.Column("target_assertion_group_id", sa.Integer(), nullable=False),
        sa.Column("source_standard", sa.String(length=50), nullable=False),
        sa.Column("source_code", sa.String(length=150), nullable=False),
        sa.Column("target_standard", sa.String(length=50), nullable=False),
        sa.Column("target_code", sa.String(length=150), nullable=False),
        sa.Column(
            "relationship_type",
            sa.String(length=30),
            server_default="derived",
            nullable=False,
        ),
        sa.Column(
            "match_strength", sa.Numeric(3, 2), server_default="1.0", nullable=False
        ),
        sa.Column(
            "derivation_method",
            sa.String(length=50),
            server_default="sygris_footprint_overlap",
            nullable=False,
        ),
        sa.Column("coverage_summary", sa.Text(), nullable=True),
        sa.Column("difference_summary", sa.Text(), nullable=True),
        sa.Column("generated_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("generated_from_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("stale_reason", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.ForeignKeyConstraint(["generated_snapshot_id"], ["dataset_snapshots.id"]),
        sa.ForeignKeyConstraint(
            ["source_assertion_group_id"], ["mapping_assertion_groups.id"]
        ),
        sa.ForeignKeyConstraint(["source_datapoint_id"], ["standard_datapoints.id"]),
        sa.ForeignKeyConstraint(
            ["target_assertion_group_id"], ["mapping_assertion_groups.id"]
        ),
        sa.ForeignKeyConstraint(["target_datapoint_id"], ["standard_datapoints.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_source_datapoint_id",
        "materialized_pairwise_mappings",
        ["source_datapoint_id"],
        unique=False,
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_target_datapoint_id",
        "materialized_pairwise_mappings",
        ["target_datapoint_id"],
        unique=False,
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_source_assertion_group_id",
        "materialized_pairwise_mappings",
        ["source_assertion_group_id"],
        unique=False,
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_target_assertion_group_id",
        "materialized_pairwise_mappings",
        ["target_assertion_group_id"],
        unique=False,
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_source_standard",
        "materialized_pairwise_mappings",
        ["source_standard"],
        unique=False,
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_target_standard",
        "materialized_pairwise_mappings",
        ["target_standard"],
        unique=False,
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_source_code",
        "materialized_pairwise_mappings",
        ["source_code"],
        unique=False,
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_target_code",
        "materialized_pairwise_mappings",
        ["target_code"],
        unique=False,
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_generated_from_hash",
        "materialized_pairwise_mappings",
        ["generated_from_hash"],
        unique=False,
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_pair_current",
        "materialized_pairwise_mappings",
        ["source_datapoint_id", "target_datapoint_id"],
        unique=True,
        postgresql_where=sa.text("is_current IS TRUE"),
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_standards",
        "materialized_pairwise_mappings",
        ["source_standard", "target_standard", "is_current"],
        unique=False,
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_source_lookup",
        "materialized_pairwise_mappings",
        ["source_standard", "source_code", "is_current"],
        unique=False,
    )
    op.create_index(
        "ix_materialized_pairwise_mappings_target_lookup",
        "materialized_pairwise_mappings",
        ["target_standard", "target_code", "is_current"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_materialized_pairwise_mappings_target_lookup",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_index(
        "ix_materialized_pairwise_mappings_source_lookup",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_index(
        "ix_materialized_pairwise_mappings_standards",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_index(
        "ix_materialized_pairwise_mappings_pair_current",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_index(
        "ix_materialized_pairwise_mappings_generated_from_hash",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_index(
        "ix_materialized_pairwise_mappings_target_code",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_index(
        "ix_materialized_pairwise_mappings_source_code",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_index(
        "ix_materialized_pairwise_mappings_target_standard",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_index(
        "ix_materialized_pairwise_mappings_source_standard",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_index(
        "ix_materialized_pairwise_mappings_target_assertion_group_id",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_index(
        "ix_materialized_pairwise_mappings_source_assertion_group_id",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_index(
        "ix_materialized_pairwise_mappings_target_datapoint_id",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_index(
        "ix_materialized_pairwise_mappings_source_datapoint_id",
        table_name="materialized_pairwise_mappings",
    )
    op.drop_table("materialized_pairwise_mappings")

    op.drop_index(
        "ix_mapping_assertion_components_group_concept",
        table_name="mapping_assertion_components",
    )
    op.drop_index(
        "ix_mapping_assertion_components_canonical_concept_id",
        table_name="mapping_assertion_components",
    )
    op.drop_index(
        "ix_mapping_assertion_components_assertion_group_id",
        table_name="mapping_assertion_components",
    )
    op.drop_table("mapping_assertion_components")

    op.drop_index(
        "ix_mapping_assertion_groups_assertion_hash",
        table_name="mapping_assertion_groups",
    )
    op.drop_index(
        "ix_mapping_assertion_groups_current_approved",
        table_name="mapping_assertion_groups",
    )
    op.drop_index(
        "ix_mapping_assertion_groups_source_profile_status",
        table_name="mapping_assertion_groups",
    )
    op.drop_index(
        "ix_mapping_assertion_groups_source_datapoint_id",
        table_name="mapping_assertion_groups",
    )
    op.drop_table("mapping_assertion_groups")

    op.drop_index(
        "ix_standard_datapoints_release_status", table_name="standard_datapoints"
    )
    op.drop_index("ix_standard_datapoints_code", table_name="standard_datapoints")
    op.drop_index(
        "ix_standard_datapoints_standard_release_id",
        table_name="standard_datapoints",
    )
    op.drop_table("standard_datapoints")

    op.drop_index(
        "ix_standard_releases_standard_status", table_name="standard_releases"
    )
    op.drop_index("ix_standard_releases_standard_id", table_name="standard_releases")
    op.drop_table("standard_releases")
