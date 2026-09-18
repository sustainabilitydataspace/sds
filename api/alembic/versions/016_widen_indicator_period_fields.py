"""widen indicator period fields for official register labels

Revision ID: 016_widen_indicator_period_fields
Revises: 015_create_canonical_mapping_tables
Create Date: 2026-05-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "016_widen_indicator_period_fields"
down_revision = "015_create_canonical_mapping_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "indicators",
        "periodicity",
        existing_type=sa.String(length=20),
        type_=sa.String(length=80),
        existing_nullable=True,
    )
    op.alter_column(
        "indicators",
        "period_type",
        existing_type=sa.String(length=20),
        type_=sa.String(length=80),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "indicators",
        "period_type",
        existing_type=sa.String(length=80),
        type_=sa.String(length=20),
        existing_nullable=True,
    )
    op.alter_column(
        "indicators",
        "periodicity",
        existing_type=sa.String(length=80),
        type_=sa.String(length=20),
        existing_nullable=True,
    )
