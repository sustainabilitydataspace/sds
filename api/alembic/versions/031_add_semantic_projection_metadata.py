"""Add semantic projection provenance metadata.

Revision ID: 031_add_semantic_projection_metadata
Revises: 030_remove_demo_functionality
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import op

revision = "031_add_semantic_projection_metadata"
down_revision = "030_remove_demo_functionality"
branch_labels = None
depends_on = None


def _add_projection_columns(table_name: str) -> None:
    op.execute(
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS projection_source VARCHAR(80)"
    )
    op.execute(
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS projection_hash VARCHAR(64)"
    )
    op.execute(
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS projection_version VARCHAR(80)"
    )
    op.execute(
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS projection_metadata JSONB"
    )
    op.execute(
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS projected_at TIMESTAMP"
    )


def upgrade() -> None:
    """Add provenance columns for deterministic semantic catalog projection."""
    _add_projection_columns("concepts")
    _add_projection_columns("canonical_concepts")

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_concepts_projection_source
        ON concepts (projection_source)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_canonical_concepts_projection_source
        ON canonical_concepts (projection_source)
        """
    )


def downgrade() -> None:
    """Drop only projection metadata introduced by this revision."""
    op.execute("DROP INDEX IF EXISTS ix_canonical_concepts_projection_source")
    op.execute("DROP INDEX IF EXISTS ix_concepts_projection_source")
    for table_name in ("canonical_concepts", "concepts"):
        op.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS projected_at")
        op.execute(
            f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS projection_metadata"
        )
        op.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS projection_version")
        op.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS projection_hash")
        op.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS projection_source")
