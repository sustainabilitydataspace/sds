"""add change feed indexes

Revision ID: 009_change_feed_indexes
Revises: 008_value_import_jobs
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op


revision = "009_change_feed_indexes"
down_revision = "008_value_import_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_dataset_snapshots_feed_order",
        "dataset_snapshots",
        ["created_at", "dataset", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_dataset_snapshots_feed_order", table_name="dataset_snapshots")
