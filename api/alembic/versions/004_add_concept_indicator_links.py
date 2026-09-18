"""add concept indicator linkage table

Revision ID: 004_concept_links
Revises: 003_semantic_concepts
Create Date: 2026-04-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "004_concept_links"
down_revision = "003_semantic_concepts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "concept_indicator_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("concept_id", sa.Integer(), nullable=False),
        sa.Column("indicator_id", sa.String(), nullable=False),
        sa.Column("link_type", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default="1.0"),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"]),
        sa.ForeignKeyConstraint(["indicator_id"], ["indicators.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_concept_indicator_links_unique",
        "concept_indicator_links",
        ["concept_id", "indicator_id", "link_type"],
        unique=True,
    )
    op.create_index(
        "ix_concept_indicator_links_concept_type",
        "concept_indicator_links",
        ["concept_id", "link_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_concept_indicator_links_concept_id"),
        "concept_indicator_links",
        ["concept_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_concept_indicator_links_indicator_id"),
        "concept_indicator_links",
        ["indicator_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_concept_indicator_links_link_type"),
        "concept_indicator_links",
        ["link_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_concept_indicator_links_link_type"), table_name="concept_indicator_links")
    op.drop_index(op.f("ix_concept_indicator_links_indicator_id"), table_name="concept_indicator_links")
    op.drop_index(op.f("ix_concept_indicator_links_concept_id"), table_name="concept_indicator_links")
    op.drop_index("ix_concept_indicator_links_concept_type", table_name="concept_indicator_links")
    op.drop_index("ix_concept_indicator_links_unique", table_name="concept_indicator_links")
    op.drop_table("concept_indicator_links")
