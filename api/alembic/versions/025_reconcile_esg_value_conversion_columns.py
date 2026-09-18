"""Reconcile legacy ESG value conversion columns.

Revision ID: 025_reconcile_esg_value_conversion_columns
Revises: 024_relax_calculation_datapoint_uniqueness
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "025_reconcile_esg_value_conversion_columns"
down_revision = "024_relax_calculation_datapoint_uniqueness"
branch_labels = None
depends_on = None


TABLE_NAME = "esg_values"


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(TABLE_NAME)}


def upgrade() -> None:
    columns = _columns()
    if "original_value" not in columns:
        op.add_column(
            TABLE_NAME,
            sa.Column("original_value", sa.Numeric(precision=30, scale=10), nullable=True),
        )
    if "currency" not in columns:
        op.add_column(TABLE_NAME, sa.Column("currency", sa.String(length=3), nullable=True))
    if "original_currency" not in columns:
        op.add_column(
            TABLE_NAME, sa.Column("original_currency", sa.String(length=3), nullable=True)
        )
    if "currency_conversion_applied" not in columns:
        op.add_column(
            TABLE_NAME,
            sa.Column(
                "currency_conversion_applied",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
        )
    if "conversion_trace" not in columns:
        op.add_column(
            TABLE_NAME,
            sa.Column("conversion_trace", postgresql.JSONB(), nullable=True),
        )
    if "value_date" not in columns:
        op.add_column(TABLE_NAME, sa.Column("value_date", sa.Date(), nullable=True))
    if "period_start" not in columns:
        op.add_column(TABLE_NAME, sa.Column("period_start", sa.Date(), nullable=True))
    if "period_end" not in columns:
        op.add_column(TABLE_NAME, sa.Column("period_end", sa.Date(), nullable=True))


def downgrade() -> None:
    # These columns are normally owned by revision 020. This reconciliation
    # migration fixes local databases stamped past 020 without the columns.
    pass
