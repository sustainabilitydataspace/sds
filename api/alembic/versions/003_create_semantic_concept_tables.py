"""Create canonical semantic concept tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "003_semantic_concepts"
down_revision = "002_add_indicator_concept_state"
branch_labels = None
depends_on = None

CONCEPT_STATE = sa.Enum(
    "catalogued",
    "semantically_modelled",
    "calculable",
    name="concept_state",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "concepts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uri", sa.String(length=500), nullable=False),
        sa.Column("indicator_id", sa.String(), nullable=True),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("taxonomy", sa.String(length=50), nullable=False),
        sa.Column("concept_type", sa.String(length=50), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("temporal_granularity", sa.String(length=30), nullable=True),
        sa.Column("hierarchy_level", sa.Integer(), nullable=True),
        sa.Column("concept_state", CONCEPT_STATE, nullable=False, server_default="catalogued"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["indicator_id"], ["indicators.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uri"),
    )
    op.create_index("ix_concepts_uri", "concepts", ["uri"])
    op.create_index("ix_concepts_indicator_id", "concepts", ["indicator_id"])
    op.create_index("ix_concepts_taxonomy", "concepts", ["taxonomy"])
    op.create_index("ix_concepts_concept_type", "concepts", ["concept_type"])
    op.create_index("ix_concepts_concept_state", "concepts", ["concept_state"])
    op.create_index("ix_concepts_taxonomy_type", "concepts", ["taxonomy", "concept_type"])
    op.create_index("ix_concepts_indicator_state", "concepts", ["indicator_id", "concept_state"])

    op.create_table(
        "concept_formulas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("concept_id", sa.Integer(), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("expression_language", sa.String(length=30), nullable=False, server_default="sds"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_concept_formulas_concept_id", "concept_formulas", ["concept_id"])
    op.create_index("ix_concept_formulas_concept_active", "concept_formulas", ["concept_id", "is_active"])

    op.create_table(
        "concept_variables",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("concept_id", sa.Integer(), nullable=False),
        sa.Column("variable_uri", sa.String(length=500), nullable=False),
        sa.Column("variable_label", sa.String(length=500), nullable=True),
        sa.Column("ordering", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("aggregation_method", sa.String(length=30), nullable=False, server_default="SUM"),
        sa.Column("temporal_granularity", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_concept_variables_concept_id", "concept_variables", ["concept_id"])
    op.create_index("ix_concept_variables_variable_uri", "concept_variables", ["variable_uri"])
    op.create_index(
        "ix_concept_variables_concept_ordering",
        "concept_variables",
        ["concept_id", "ordering"],
    )

    op.create_table(
        "concept_equivalences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_concept_id", sa.Integer(), nullable=False),
        sa.Column("target_uri", sa.String(length=500), nullable=False),
        sa.Column("target_taxonomy", sa.String(length=50), nullable=True),
        sa.Column("relationship_type", sa.String(length=30), nullable=False, server_default="equivalent"),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["source_concept_id"], ["concepts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_concept_equivalences_source_concept_id", "concept_equivalences", ["source_concept_id"])
    op.create_index("ix_concept_equivalences_target_uri", "concept_equivalences", ["target_uri"])
    op.create_index("ix_concept_equivalences_target_taxonomy", "concept_equivalences", ["target_taxonomy"])
    op.create_index(
        "ix_concept_equivalences_source_relationship",
        "concept_equivalences",
        ["source_concept_id", "relationship_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_concept_equivalences_source_relationship", table_name="concept_equivalences")
    op.drop_index("ix_concept_equivalences_target_taxonomy", table_name="concept_equivalences")
    op.drop_index("ix_concept_equivalences_target_uri", table_name="concept_equivalences")
    op.drop_index("ix_concept_equivalences_source_concept_id", table_name="concept_equivalences")
    op.drop_table("concept_equivalences")

    op.drop_index("ix_concept_variables_concept_ordering", table_name="concept_variables")
    op.drop_index("ix_concept_variables_variable_uri", table_name="concept_variables")
    op.drop_index("ix_concept_variables_concept_id", table_name="concept_variables")
    op.drop_table("concept_variables")

    op.drop_index("ix_concept_formulas_concept_active", table_name="concept_formulas")
    op.drop_index("ix_concept_formulas_concept_id", table_name="concept_formulas")
    op.drop_table("concept_formulas")

    op.drop_index("ix_concepts_indicator_state", table_name="concepts")
    op.drop_index("ix_concepts_taxonomy_type", table_name="concepts")
    op.drop_index("ix_concepts_concept_state", table_name="concepts")
    op.drop_index("ix_concepts_concept_type", table_name="concepts")
    op.drop_index("ix_concepts_taxonomy", table_name="concepts")
    op.drop_index("ix_concepts_indicator_id", table_name="concepts")
    op.drop_index("ix_concepts_uri", table_name="concepts")
    op.drop_table("concepts")
