"""normalize ESG value type names

Revision ID: 012_value_type_names
Revises: 011_allow_typed_esg_values
Create Date: 2026-05-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "012_value_type_names"
down_revision = "011_allow_typed_esg_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE esg_values SET value_type = 'numeric' WHERE value_type IN ('number', 'decimal')")
    op.execute("UPDATE esg_values SET value_type = 'narrative' WHERE value_type IN ('text', 'string')")
    op.alter_column(
        "esg_values",
        "value_type",
        existing_type=sa.String(length=20),
        server_default="numeric",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute("UPDATE esg_values SET value_type = 'number' WHERE value_type = 'numeric'")
    op.execute("UPDATE esg_values SET value_type = 'text' WHERE value_type IN ('narrative', 'semi-narrative')")
    op.alter_column(
        "esg_values",
        "value_type",
        existing_type=sa.String(length=20),
        server_default="number",
        existing_nullable=False,
    )
