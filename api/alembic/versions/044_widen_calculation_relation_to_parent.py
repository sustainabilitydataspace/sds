"""Widen calculation contract parent relation text.

Calculation contracts can carry explanatory parent relationships, not only
short enum-like relation labels.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "044_widen_calculation_relation_to_parent"
down_revision = "043_add_canonical_value_context_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "canonical_calculation_contracts",
        "relation_to_parent",
        existing_type=sa.String(length=100),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "canonical_calculation_contracts",
        "relation_to_parent",
        existing_type=sa.Text(),
        type_=sa.String(length=100),
        existing_nullable=True,
        postgresql_using="left(relation_to_parent, 100)",
    )
