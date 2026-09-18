"""Add canonical operational identity fields to value contexts.

Revision-backed value contexts keep the original standard-bound identity
unchanged, while allowing new SDS/Sygris operational observations to carry a
governed canonical concept identity and source-observation classification.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "043_add_canonical_value_context_identity"
down_revision = "042_add_localized_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "value_contexts",
        sa.Column("canonical_concept_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "value_contexts",
        sa.Column("canonical_uri", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "value_contexts",
        sa.Column(
            "source_observation_type",
            sa.String(length=50),
            nullable=False,
            server_default="standard_direct_legacy",
        ),
    )
    op.create_check_constraint(
        "ck_value_contexts_source_observation_type",
        "value_contexts",
        "source_observation_type IN ("
        "'standard_direct_legacy', "
        "'canonical_operational', "
        "'standard_bound_evidence'"
        ")",
    )
    op.create_foreign_key(
        "fk_value_contexts_canonical_concept_id_canonical_concepts",
        "value_contexts",
        "canonical_concepts",
        ["canonical_concept_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_value_contexts_canonical_concept_id"),
        "value_contexts",
        ["canonical_concept_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_value_contexts_canonical_uri"),
        "value_contexts",
        ["canonical_uri"],
        unique=False,
    )
    op.create_index(
        "ix_value_contexts_canonical_concept_period",
        "value_contexts",
        ["canonical_concept_id", "reporting_period_id"],
        unique=False,
    )
    op.create_index(
        "ix_value_contexts_tenant_canonical_period",
        "value_contexts",
        ["tenant_id", "canonical_uri", "reporting_period_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_value_contexts_tenant_canonical_period", table_name="value_contexts")
    op.drop_index(
        "ix_value_contexts_canonical_concept_period", table_name="value_contexts"
    )
    op.drop_index(op.f("ix_value_contexts_canonical_uri"), table_name="value_contexts")
    op.drop_index(
        op.f("ix_value_contexts_canonical_concept_id"),
        table_name="value_contexts",
    )
    op.drop_constraint(
        "fk_value_contexts_canonical_concept_id_canonical_concepts",
        "value_contexts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_value_contexts_source_observation_type",
        "value_contexts",
        type_="check",
    )
    op.drop_column("value_contexts", "source_observation_type")
    op.drop_column("value_contexts", "canonical_uri")
    op.drop_column("value_contexts", "canonical_concept_id")
