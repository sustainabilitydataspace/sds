"""Create canonical concept tables with revision/interval support.

Revision ID: 014_create_canonical_concept_tables
Revises: 013_indicator_import_jobs
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "014_create_canonical_concept_tables"
down_revision = "013_indicator_import_jobs"
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
    # ------------------------------------------------------------------
    # Canonical concept tables (immutable, append-only, revisioned)
    # ------------------------------------------------------------------
    op.create_table(
        "canonical_concepts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_uri", sa.String(length=500), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("effective_from", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("effective_to", sa.DateTime(), nullable=True),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column("indicator_id", sa.String(), nullable=True),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("taxonomy", sa.String(length=50), nullable=False),
        sa.Column("concept_type", sa.String(length=50), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("temporal_granularity", sa.String(length=30), nullable=True),
        sa.Column("hierarchy_level", sa.Integer(), nullable=True),
        sa.Column(
            "concept_state",
            CONCEPT_STATE,
            nullable=False,
            server_default="catalogued",
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["indicator_id"], ["indicators.id"]),
        sa.ForeignKeyConstraint(["superseded_by"], ["canonical_concepts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_uri", "revision", name="uq_canonical_concepts_uri_revision"),
    )
    op.create_index("ix_canonical_concepts_uri", "canonical_concepts", ["canonical_uri"], unique=False)
    op.create_index(
        "ix_canonical_concepts_current",
        "canonical_concepts",
        ["canonical_uri", "effective_to"],
        unique=False,
        postgresql_where=sa.text("effective_to IS NULL"),
    )
    op.create_index(
        "ix_canonical_concepts_indicator_id", "canonical_concepts", ["indicator_id"], unique=False
    )
    op.create_index(
        "ix_canonical_concepts_taxonomy", "canonical_concepts", ["taxonomy"], unique=False
    )
    op.create_index(
        "ix_canonical_concepts_concept_type", "canonical_concepts", ["concept_type"], unique=False
    )
    op.create_index(
        "ix_canonical_concepts_concept_state", "canonical_concepts", ["concept_state"], unique=False
    )
    op.create_index(
        "ix_canonical_concepts_taxonomy_type",
        "canonical_concepts",
        ["taxonomy", "concept_type"],
        unique=False,
    )

    # Canonical formulas
    op.create_table(
        "canonical_concept_formulas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_concept_id", sa.Integer(), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("expression_language", sa.String(length=30), nullable=False, server_default="sds"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["canonical_concept_id"], ["canonical_concepts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_canonical_concept_formulas_concept_id",
        "canonical_concept_formulas",
        ["canonical_concept_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_concept_formulas_concept_active",
        "canonical_concept_formulas",
        ["canonical_concept_id", "is_active"],
        unique=False,
    )

    # Canonical variables
    op.create_table(
        "canonical_concept_variables",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_concept_id", sa.Integer(), nullable=False),
        sa.Column("variable_uri", sa.String(length=500), nullable=False),
        sa.Column("variable_label", sa.String(length=500), nullable=True),
        sa.Column("ordering", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("aggregation_method", sa.String(length=30), nullable=False, server_default="SUM"),
        sa.Column("temporal_granularity", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["canonical_concept_id"], ["canonical_concepts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_canonical_concept_variables_concept_id",
        "canonical_concept_variables",
        ["canonical_concept_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_concept_variables_variable_uri",
        "canonical_concept_variables",
        ["variable_uri"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_concept_variables_concept_ordering",
        "canonical_concept_variables",
        ["canonical_concept_id", "ordering"],
        unique=False,
    )

    # Canonical equivalences
    op.create_table(
        "canonical_concept_equivalences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_concept_id", sa.Integer(), nullable=False),
        sa.Column("target_uri", sa.String(length=500), nullable=False),
        sa.Column("target_taxonomy", sa.String(length=50), nullable=True),
        sa.Column(
            "relationship_type", sa.String(length=30), nullable=False, server_default="equivalent"
        ),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["canonical_concept_id"], ["canonical_concepts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_canonical_concept_equivalences_concept_id",
        "canonical_concept_equivalences",
        ["canonical_concept_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_concept_equivalences_target_uri",
        "canonical_concept_equivalences",
        ["target_uri"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_concept_equivalences_target_taxonomy",
        "canonical_concept_equivalences",
        ["target_taxonomy"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_concept_equivalences_source_relationship",
        "canonical_concept_equivalences",
        ["canonical_concept_id", "relationship_type"],
        unique=False,
    )

    # Canonical indicator links
    op.create_table(
        "canonical_concept_indicator_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_concept_id", sa.Integer(), nullable=False),
        sa.Column("indicator_id", sa.String(), nullable=False),
        sa.Column("link_type", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default="1.0"),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["canonical_concept_id"], ["canonical_concepts.id"]),
        sa.ForeignKeyConstraint(["indicator_id"], ["indicators.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "canonical_concept_id",
            "indicator_id",
            "link_type",
            name="uq_canonical_concept_indicator_links",
        ),
    )
    op.create_index(
        "ix_canonical_concept_indicator_links_concept_id",
        "canonical_concept_indicator_links",
        ["canonical_concept_id"],
        unique=False,
    )
    op.create_index(
        "ix_canonical_concept_indicator_links_indicator_id",
        "canonical_concept_indicator_links",
        ["indicator_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # Backfill from existing mutable concepts tables
    # ------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO canonical_concepts (
            canonical_uri, revision, effective_from, effective_to,
            indicator_id, label, description, taxonomy, concept_type,
            unit, temporal_granularity, hierarchy_level, concept_state,
            created_at
        )
        SELECT
            uri, 1, COALESCE(created_at, NOW()), NULL,
            indicator_id, label, description, taxonomy, concept_type,
            unit, temporal_granularity, hierarchy_level, concept_state,
            COALESCE(created_at, NOW())
        FROM concepts
        """
    )

    op.execute(
        """
        INSERT INTO canonical_concept_formulas (
            canonical_concept_id, expression, expression_language,
            version, is_active, created_at
        )
        SELECT
            cc.id, cf.expression, cf.expression_language,
            cf.version, cf.is_active, COALESCE(cf.created_at, NOW())
        FROM concept_formulas cf
        JOIN concepts c ON c.id = cf.concept_id
        JOIN canonical_concepts cc ON cc.canonical_uri = c.uri AND cc.effective_to IS NULL
        """
    )

    op.execute(
        """
        INSERT INTO canonical_concept_variables (
            canonical_concept_id, variable_uri, variable_label,
            ordering, aggregation_method, temporal_granularity, created_at
        )
        SELECT
            cc.id, cv.variable_uri, cv.variable_label,
            cv.ordering, cv.aggregation_method, cv.temporal_granularity, COALESCE(cv.created_at, NOW())
        FROM concept_variables cv
        JOIN concepts c ON c.id = cv.concept_id
        JOIN canonical_concepts cc ON cc.canonical_uri = c.uri AND cc.effective_to IS NULL
        """
    )

    op.execute(
        """
        INSERT INTO canonical_concept_equivalences (
            canonical_concept_id, target_uri, target_taxonomy,
            relationship_type, confidence, created_at
        )
        SELECT
            cc.id, ce.target_uri, ce.target_taxonomy,
            ce.relationship_type, ce.confidence, COALESCE(ce.created_at, NOW())
        FROM concept_equivalences ce
        JOIN concepts c ON c.id = ce.source_concept_id
        JOIN canonical_concepts cc ON cc.canonical_uri = c.uri AND cc.effective_to IS NULL
        """
    )

    op.execute(
        """
        INSERT INTO canonical_concept_indicator_links (
            canonical_concept_id, indicator_id, link_type,
            confidence, rationale, created_at
        )
        SELECT
            cc.id, cil.indicator_id, cil.link_type,
            cil.confidence, cil.rationale, COALESCE(cil.created_at, NOW())
        FROM concept_indicator_links cil
        JOIN concepts c ON c.id = cil.concept_id
        JOIN canonical_concepts cc ON cc.canonical_uri = c.uri AND cc.effective_to IS NULL
        """
    )

    # ------------------------------------------------------------------
    # Projection sync function + trigger
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_concept_projection()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Upsert the current canonical row into the mutable projection
            IF NEW.effective_to IS NULL THEN
                INSERT INTO concepts (
                    uri, indicator_id, label, description, taxonomy,
                    concept_type, unit, temporal_granularity, hierarchy_level,
                    concept_state, created_at, updated_at
                )
                VALUES (
                    NEW.canonical_uri, NEW.indicator_id, NEW.label, NEW.description,
                    NEW.taxonomy, NEW.concept_type, NEW.unit, NEW.temporal_granularity,
                    NEW.hierarchy_level, NEW.concept_state, NEW.created_at, NOW()
                )
                ON CONFLICT (uri) DO UPDATE SET
                    indicator_id = EXCLUDED.indicator_id,
                    label = EXCLUDED.label,
                    description = EXCLUDED.description,
                    taxonomy = EXCLUDED.taxonomy,
                    concept_type = EXCLUDED.concept_type,
                    unit = EXCLUDED.unit,
                    temporal_granularity = EXCLUDED.temporal_granularity,
                    hierarchy_level = EXCLUDED.hierarchy_level,
                    concept_state = EXCLUDED.concept_state,
                    updated_at = NOW();
            ELSE
                -- If superseded, optionally mark stale or leave as-is
                -- (We do not auto-delete to allow audit reads)
                NULL;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_sync_concept_projection
        AFTER INSERT OR UPDATE ON canonical_concepts
        FOR EACH ROW
        EXECUTE FUNCTION sync_concept_projection();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_sync_concept_projection ON canonical_concepts;")
    op.execute("DROP FUNCTION IF EXISTS sync_concept_projection();")

    op.drop_index(
        "ix_canonical_concept_indicator_links_indicator_id",
        table_name="canonical_concept_indicator_links",
    )
    op.drop_index(
        "ix_canonical_concept_indicator_links_concept_id",
        table_name="canonical_concept_indicator_links",
    )
    op.drop_table("canonical_concept_indicator_links")

    op.drop_index(
        "ix_canonical_concept_equivalences_source_relationship",
        table_name="canonical_concept_equivalences",
    )
    op.drop_index(
        "ix_canonical_concept_equivalences_target_taxonomy",
        table_name="canonical_concept_equivalences",
    )
    op.drop_index(
        "ix_canonical_concept_equivalences_target_uri",
        table_name="canonical_concept_equivalences",
    )
    op.drop_index(
        "ix_canonical_concept_equivalences_concept_id",
        table_name="canonical_concept_equivalences",
    )
    op.drop_table("canonical_concept_equivalences")

    op.drop_index(
        "ix_canonical_concept_variables_concept_ordering",
        table_name="canonical_concept_variables",
    )
    op.drop_index(
        "ix_canonical_concept_variables_variable_uri",
        table_name="canonical_concept_variables",
    )
    op.drop_index(
        "ix_canonical_concept_variables_concept_id",
        table_name="canonical_concept_variables",
    )
    op.drop_table("canonical_concept_variables")

    op.drop_index(
        "ix_canonical_concept_formulas_concept_active",
        table_name="canonical_concept_formulas",
    )
    op.drop_index(
        "ix_canonical_concept_formulas_concept_id",
        table_name="canonical_concept_formulas",
    )
    op.drop_table("canonical_concept_formulas")

    op.drop_index(
        "ix_canonical_concepts_taxonomy_type", table_name="canonical_concepts"
    )
    op.drop_index(
        "ix_canonical_concepts_concept_state", table_name="canonical_concepts"
    )
    op.drop_index(
        "ix_canonical_concepts_concept_type", table_name="canonical_concepts"
    )
    op.drop_index(
        "ix_canonical_concepts_taxonomy", table_name="canonical_concepts"
    )
    op.drop_index(
        "ix_canonical_concepts_indicator_id", table_name="canonical_concepts"
    )
    op.drop_index(
        "ix_canonical_concepts_current", table_name="canonical_concepts"
    )
    op.drop_index(
        "ix_canonical_concepts_uri", table_name="canonical_concepts"
    )
    op.drop_table("canonical_concepts")
