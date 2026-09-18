"""Widen unit category base unit labels.

Revision ID: 026_widen_unit_category_base_unit
Revises: 025_reconcile_esg_value_conversion_columns
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "026_widen_unit_category_base_unit"
down_revision = "025_reconcile_esg_value_conversion_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "unit_categories",
        "base_unit",
        existing_type=sa.String(length=20),
        type_=sa.String(length=120),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "unit_categories",
        "base_unit",
        existing_type=sa.String(length=120),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
