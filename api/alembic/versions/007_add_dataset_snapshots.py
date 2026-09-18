"""add dataset snapshots

Revision ID: 007_dataset_snapshots
Revises: 006_value_idempotency
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "007_dataset_snapshots"
down_revision = "006_value_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dataset", sa.String(length=50), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("contract_version", sa.String(length=20), nullable=False),
        sa.Column("item_index", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_ref", sa.String(length=500), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dataset_snapshots_dataset_created_at",
        "dataset_snapshots",
        ["dataset", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_dataset_snapshots_dataset_manifest_hash",
        "dataset_snapshots",
        ["dataset", "manifest_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_dataset_snapshots_dataset_manifest_hash", table_name="dataset_snapshots")
    op.drop_index("ix_dataset_snapshots_dataset_created_at", table_name="dataset_snapshots")
    op.drop_table("dataset_snapshots")
