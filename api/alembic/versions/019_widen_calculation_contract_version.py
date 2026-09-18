"""Widen calculation contract version field.

Revision ID: 019_widen_calculation_contract_version
Revises: 018_create_calculation_contract_tables
Create Date: 2026-05-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "019_widen_calculation_contract_version"
down_revision = "018_create_calculation_contract_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "canonical_calculation_contracts",
        "contract_version",
        existing_type=sa.String(length=30),
        type_=sa.String(length=100),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "canonical_calculation_contracts",
        "contract_version",
        existing_type=sa.String(length=100),
        type_=sa.String(length=30),
        existing_nullable=False,
    )
