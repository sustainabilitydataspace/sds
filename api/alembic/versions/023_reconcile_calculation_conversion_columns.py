"""Reconcile calculation conversion columns.

Revision ID: 023_reconcile_calculation_conversion_columns
Revises: 022_create_value_versioning_tables
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "023_reconcile_calculation_conversion_columns"
down_revision = "022_create_value_versioning_tables"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _foreign_key_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {fk["name"] for fk in inspector.get_foreign_keys(table_name)}


def upgrade() -> None:
    contract_columns = _columns("canonical_calculation_contracts")
    if "result_currency" not in contract_columns:
        op.add_column(
            "canonical_calculation_contracts",
            sa.Column("result_currency", sa.String(length=3), nullable=True),
        )
    if "conversion_policy" not in contract_columns:
        op.add_column(
            "canonical_calculation_contracts",
            sa.Column(
                "conversion_policy",
                sa.String(length=50),
                server_default="fail_closed",
                nullable=False,
            ),
        )

    component_columns = _columns("canonical_calculation_components")
    if "currency" not in component_columns:
        op.add_column(
            "canonical_calculation_components",
            sa.Column("currency", sa.String(length=3), nullable=True),
        )
    if "expected_currency" not in component_columns:
        op.add_column(
            "canonical_calculation_components",
            sa.Column("expected_currency", sa.String(length=3), nullable=True),
        )
    if "conversion_policy" not in component_columns:
        op.add_column(
            "canonical_calculation_components",
            sa.Column(
                "conversion_policy",
                sa.String(length=50),
                server_default="fail_closed",
                nullable=False,
            ),
        )
    if "fx_policy_id" not in component_columns:
        op.add_column(
            "canonical_calculation_components",
            sa.Column("fx_policy_id", sa.String(length=100), nullable=True),
        )

    if (
        "fk_canonical_calc_components_fx_policy_id"
        not in _foreign_key_names("canonical_calculation_components")
    ):
        op.create_foreign_key(
            "fk_canonical_calc_components_fx_policy_id",
            "canonical_calculation_components",
            "fx_policies",
            ["fx_policy_id"],
            ["id"],
        )


def downgrade() -> None:
    # These columns are owned by revision 020 in normal databases. This revision
    # only repairs drifted databases where Alembic was marked past 020 without
    # the columns present, so downgrade intentionally leaves the schema intact.
    pass
