"""Widen unit symbol columns for canonical sustainability units.

Revision ID: 027_widen_unit_symbol_columns
Revises: 026_widen_unit_category_base_unit
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "027_widen_unit_symbol_columns"
down_revision = "026_widen_unit_category_base_unit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "units",
        "symbol",
        existing_type=sa.String(length=20),
        type_=sa.String(length=120),
        existing_nullable=False,
    )
    op.alter_column(
        "units",
        "name",
        existing_type=sa.String(length=100),
        type_=sa.String(length=200),
        existing_nullable=False,
    )
    op.alter_column(
        "conversion_rules",
        "from_unit",
        existing_type=sa.String(length=20),
        type_=sa.String(length=120),
        existing_nullable=False,
    )
    op.alter_column(
        "conversion_rules",
        "to_unit",
        existing_type=sa.String(length=20),
        type_=sa.String(length=120),
        existing_nullable=False,
    )


def downgrade() -> None:
    _assert_max_length("conversion_rules", "to_unit", 20)
    _assert_max_length("conversion_rules", "from_unit", 20)
    _assert_max_length("units", "name", 100)
    _assert_max_length("units", "symbol", 20)
    op.alter_column(
        "conversion_rules",
        "to_unit",
        existing_type=sa.String(length=120),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
    op.alter_column(
        "conversion_rules",
        "from_unit",
        existing_type=sa.String(length=120),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
    op.alter_column(
        "units",
        "name",
        existing_type=sa.String(length=200),
        type_=sa.String(length=100),
        existing_nullable=False,
    )
    op.alter_column(
        "units",
        "symbol",
        existing_type=sa.String(length=120),
        type_=sa.String(length=20),
        existing_nullable=False,
    )


def _assert_max_length(table_name: str, column_name: str, max_length: int) -> None:
    overflow = op.get_bind().execute(
        sa.text(
            f"""
            SELECT {column_name}
            FROM {table_name}
            WHERE {column_name} IS NOT NULL
              AND length({column_name}) > :max_length
            LIMIT 5
            """
        ),
        {"max_length": max_length},
    )
    examples = [str(row[0]) for row in overflow.fetchall()]
    if examples:
        raise RuntimeError(
            f"Cannot downgrade 027_widen_unit_symbol_columns: "
            f"{table_name}.{column_name} contains values longer than "
            f"{max_length} characters, examples={examples!r}."
        )
