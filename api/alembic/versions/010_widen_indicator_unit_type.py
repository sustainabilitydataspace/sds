"""widen indicator unit_type for official unit labels

Revision ID: 010_widen_indicator_unit_type
Revises: 009_change_feed_indexes
Create Date: 2026-05-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "010_widen_indicator_unit_type"
down_revision = "009_change_feed_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "indicators",
        "unit_type",
        existing_type=sa.String(length=50),
        type_=sa.String(length=120),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "indicators",
        "unit_type",
        existing_type=sa.String(length=120),
        type_=sa.String(length=50),
        existing_nullable=True,
    )
