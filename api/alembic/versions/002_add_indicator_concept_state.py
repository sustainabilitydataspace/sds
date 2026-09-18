"""Add concept_state to indicators and finalize unit precision."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "002_add_indicator_concept_state"
down_revision = "001_baseline_schema"
branch_labels = None
depends_on = None

CONCEPT_STATE = sa.Enum(
    "catalogued",
    "semantically_modelled",
    "calculable",
    name="concept_state",
    native_enum=False,
)


def upgrade() -> None:
    op.add_column(
        "indicators",
        sa.Column(
            "concept_state",
            CONCEPT_STATE,
            nullable=False,
            server_default="catalogued",
        ),
    )


def downgrade() -> None:
    op.drop_column("indicators", "concept_state")
