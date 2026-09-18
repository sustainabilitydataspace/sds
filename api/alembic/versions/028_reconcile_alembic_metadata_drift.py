"""Reconcile Alembic metadata drift indexes.

Revision ID: 028_reconcile_alembic_metadata_drift
Revises: 027_widen_unit_symbol_columns
Create Date: 2026-05-29
"""

from __future__ import annotations

from alembic import op

revision = "028_reconcile_alembic_metadata_drift"
down_revision = "027_widen_unit_symbol_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Repair indexes that existing 027 deployments may not have."""
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_canonical_concepts_indicator_state
        ON canonical_concepts (indicator_id, concept_state)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_canonical_concept_indicator_links_concept_type
        ON canonical_concept_indicator_links (canonical_concept_id, link_type)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_fx_rate_observations_lookup
        ON fx_rate_observations (
            provider,
            rate_type,
            base_currency,
            quote_currency,
            rate_date
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_fx_rate_observations_source_hash
        ON fx_rate_observations (source_hash)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_fx_rate_periods_lookup
        ON fx_rate_periods (
            provider,
            rate_type,
            base_currency,
            quote_currency,
            period_type,
            period_start,
            period_end
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_fx_rate_periods_quote_currency
        ON fx_rate_periods (quote_currency)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_fx_rate_periods_source_hash
        ON fx_rate_periods (source_hash)
        """
    )


def downgrade() -> None:
    """Drop only indexes whose canonical home is this reconciliation revision."""
    op.execute("DROP INDEX IF EXISTS ix_fx_rate_periods_source_hash")
    op.execute("DROP INDEX IF EXISTS ix_fx_rate_periods_quote_currency")
    op.execute("DROP INDEX IF EXISTS ix_fx_rate_observations_source_hash")
    op.execute("DROP INDEX IF EXISTS ix_canonical_concept_indicator_links_concept_type")
    op.execute("DROP INDEX IF EXISTS ix_canonical_concepts_indicator_state")
