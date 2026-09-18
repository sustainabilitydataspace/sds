"""Legacy value classification + review queue (VARCH-7a).

candidate-v14 "Legacy Value Policy": the ~25,950 ``value_contexts`` with empty
``dimensions_json`` must be classified for the semantic-atomization substrate into one of four
states — ``classified`` (safe deterministic assignment), ``auto_classified_pending_review``
(confidence below threshold; not trusted for public dimensioned claims until reviewed),
``legacy_grandfathered`` (unclassifiable; carries a sunset/review date), and
``not_dimension_required`` (the governing contract does not require dimensions).

This migration adds ``legacy_value_classifications``: one row per ``value_contexts`` row, pinning
the deterministic classification, its confidence, the assigned-dimension content hash (when
classified), the review-queue status, a grandfather ``sunset_date``, and the deterministic
valid-time / decision-time defaults assigned to a legacy row. ``classification_hash``
content-addresses the whole classification so the backfill is replayable and the
``legacy-dimensions-audit`` gate can sample-verify quality.

Adds only a new table; touches no existing data and is reversible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "041_add_legacy_value_classifications"
down_revision = "040_defer_successor_pointer_fks"
branch_labels = None
depends_on = None

_HEX64 = "{col} ~ '^[0-9a-f]{{64}}$'"


def upgrade() -> None:
    op.create_table(
        "legacy_value_classifications",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "value_context_id",
            sa.Integer(),
            sa.ForeignKey("value_contexts.id"),
            nullable=False,
        ),
        sa.Column("classification_state", sa.String(40), nullable=False),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column("contract_requires_dimensions", sa.Boolean(), nullable=False),
        sa.Column("assigned_dimensions_hash", sa.String(64)),
        sa.Column(
            "review_status",
            sa.String(20),
            nullable=False,
            server_default="none",
        ),
        sa.Column("sunset_date", sa.Date()),
        sa.Column(
            "default_valid_from", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("default_decision_commit_id", sa.BigInteger(), nullable=False),
        sa.Column("classification_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "classification_state IN ('classified', "
            "'auto_classified_pending_review', 'legacy_grandfathered', "
            "'not_dimension_required')",
            name="ck_legacy_value_classifications_state",
        ),
        sa.CheckConstraint(
            "review_status IN ('none', 'queued', 'reviewed', 'dismissed')",
            name="ck_legacy_value_classifications_review",
        ),
        sa.CheckConstraint(
            "default_decision_commit_id > 0",
            name="ck_legacy_value_classifications_decision_positive",
        ),
        # grandfathered rows MUST carry a sunset date; others MUST NOT.
        sa.CheckConstraint(
            "(classification_state = 'legacy_grandfathered') = (sunset_date IS NOT NULL)",
            name="ck_legacy_value_classifications_sunset",
        ),
        sa.CheckConstraint(
            "assigned_dimensions_hash IS NULL OR "
            + _HEX64.format(col="assigned_dimensions_hash"),
            name="ck_legacy_value_classifications_dims_hex",
        ),
        sa.CheckConstraint(
            _HEX64.format(col="classification_hash"),
            name="ck_legacy_value_classifications_hash_hex",
        ),
        sa.UniqueConstraint(
            "value_context_id", name="uq_legacy_value_classifications_context"
        ),
    )
    op.create_index(
        "ix_legacy_value_classifications_state",
        "legacy_value_classifications",
        ["classification_state", "review_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_legacy_value_classifications_state",
        table_name="legacy_value_classifications",
    )
    op.drop_table("legacy_value_classifications")
