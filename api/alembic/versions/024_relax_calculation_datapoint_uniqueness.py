"""Relax calculation datapoint uniqueness to exact-scope local identity.

Revision ID: 024_relax_calculation_datapoint_uniqueness
Revises: 023_reconcile_calculation_conversion_columns
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "024_relax_calculation_datapoint_uniqueness"
down_revision = "023_reconcile_calculation_conversion_columns"
branch_labels = None
depends_on = None


TABLE_NAME = "canonical_calculation_contracts"
OLD_GLOBAL_INDEX = "ix_canonical_calc_contracts_active_datapoint"
LOOKUP_INDEX = "ix_canonical_calc_contracts_active_datapoint_lookup"
MODEL_DATAPOINT_INDEX = "ix_canonical_calc_contracts_active_model_datapoint"
ACTIVE_DATAPOINT_WHERE = (
    "is_active IS TRUE AND canonical_datapoint_id IS NOT NULL"
)


def _index_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(TABLE_NAME)}


def upgrade() -> None:
    index_names = _index_names()
    if OLD_GLOBAL_INDEX in index_names:
        op.drop_index(OLD_GLOBAL_INDEX, table_name=TABLE_NAME)
        index_names.remove(OLD_GLOBAL_INDEX)

    if LOOKUP_INDEX not in index_names:
        op.create_index(
            LOOKUP_INDEX,
            TABLE_NAME,
            ["canonical_datapoint_id"],
            unique=False,
            postgresql_where=sa.text(ACTIVE_DATAPOINT_WHERE),
        )

    if MODEL_DATAPOINT_INDEX not in index_names:
        op.create_index(
            MODEL_DATAPOINT_INDEX,
            TABLE_NAME,
            ["model_id", "canonical_datapoint_id"],
            unique=True,
            postgresql_where=sa.text(ACTIVE_DATAPOINT_WHERE),
        )


def downgrade() -> None:
    index_names = _index_names()
    if MODEL_DATAPOINT_INDEX in index_names:
        op.drop_index(MODEL_DATAPOINT_INDEX, table_name=TABLE_NAME)
    if LOOKUP_INDEX in index_names:
        op.drop_index(LOOKUP_INDEX, table_name=TABLE_NAME)
    if OLD_GLOBAL_INDEX not in index_names:
        op.create_index(
            OLD_GLOBAL_INDEX,
            TABLE_NAME,
            ["canonical_datapoint_id"],
            unique=True,
            postgresql_where=sa.text(ACTIVE_DATAPOINT_WHERE),
        )
