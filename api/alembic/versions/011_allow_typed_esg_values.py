"""allow typed ESG values

Revision ID: 011_allow_typed_esg_values
Revises: 010_widen_indicator_unit_type
Create Date: 2026-05-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "011_allow_typed_esg_values"
down_revision = "010_widen_indicator_unit_type"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _column_names(table_name):
        op.add_column(table_name, column)


def _drop_column_if_present(table_name: str, column_name: str) -> None:
    if column_name in _column_names(table_name):
        op.drop_column(table_name, column_name)


def upgrade() -> None:
    _add_column_if_missing(
        "esg_values",
        sa.Column(
            "value_type",
            sa.String(length=20),
            nullable=False,
            server_default="numeric",
        ),
    )
    _add_column_if_missing(
        "esg_values", sa.Column("text_value", sa.Text(), nullable=True)
    )
    _add_column_if_missing(
        "esg_values", sa.Column("boolean_value", sa.Boolean(), nullable=True)
    )
    op.alter_column(
        "esg_values",
        "value",
        existing_type=sa.Numeric(30, 10),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("DELETE FROM esg_values WHERE value IS NULL")
    op.alter_column(
        "esg_values",
        "value",
        existing_type=sa.Numeric(30, 10),
        nullable=False,
    )
    _drop_column_if_present("esg_values", "boolean_value")
    _drop_column_if_present("esg_values", "text_value")
    _drop_column_if_present("esg_values", "value_type")
