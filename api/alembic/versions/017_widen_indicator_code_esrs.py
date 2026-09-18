"""widen indicator ESRS code for compiled child datapoints

Revision ID: 017_widen_indicator_code_esrs
Revises: 016_widen_indicator_period_fields
Create Date: 2026-05-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "017_widen_indicator_code_esrs"
down_revision = "016_widen_indicator_period_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "indicators",
        "code_esrs",
        existing_type=sa.String(length=50),
        type_=sa.String(length=150),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "indicators",
        "code_esrs",
        existing_type=sa.String(length=150),
        type_=sa.String(length=50),
        existing_nullable=True,
    )
