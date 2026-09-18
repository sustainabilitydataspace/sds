"""Add read-path scale indexes.

Revision ID: 029_add_read_path_scale_indexes
Revises: 028_reconcile_alembic_metadata_drift
Create Date: 2026-05-31
"""

from __future__ import annotations

from alembic import op

revision = "029_add_read_path_scale_indexes"
down_revision = "028_reconcile_alembic_metadata_drift"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add additive indexes for high-cardinality read paths."""
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_esg_values_read_cursor
        ON esg_values (concept, entity, period DESC, created_at DESC, id DESC)
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_esg_values_change_cursor
        ON esg_values (updated_at, id)
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_materialized_pairwise_mappings_metadata_esg_dimension
        ON materialized_pairwise_mappings ((metadata_json ->> 'esg_dimension'))
        WHERE is_current IS TRUE
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_materialized_pairwise_mappings_metadata_dimension
        ON materialized_pairwise_mappings ((metadata_json ->> 'dimension'))
        WHERE is_current IS TRUE
        """)


def downgrade() -> None:
    """Drop only indexes introduced by this revision."""
    op.execute(
        "DROP INDEX IF EXISTS ix_materialized_pairwise_mappings_metadata_dimension"
    )
    op.execute(
        "DROP INDEX IF EXISTS ix_materialized_pairwise_mappings_metadata_esg_dimension"
    )
    op.execute("DROP INDEX IF EXISTS ix_esg_values_change_cursor")
    op.execute("DROP INDEX IF EXISTS ix_esg_values_read_cursor")
