"""add value import jobs

Revision ID: 008_value_import_jobs
Revises: 007_dataset_snapshots
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "008_value_import_jobs"
down_revision = "007_dataset_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "value_import_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_format", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("submitted_by", sa.String(length=100), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("request_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("total_rows", sa.Integer(), nullable=True),
        sa.Column("accepted_rows", sa.Integer(), nullable=True),
        sa.Column("rejected_rows", sa.Integer(), nullable=True),
        sa.Column("committed", sa.Boolean(), nullable=True),
        sa.Column("result_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_value_import_jobs_status",
        "value_import_jobs",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_value_import_jobs_submitted_by",
        "value_import_jobs",
        ["submitted_by"],
        unique=False,
    )
    op.create_index(
        "ix_value_import_jobs_submitted_created",
        "value_import_jobs",
        ["submitted_by", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_value_import_jobs_submitted_created", table_name="value_import_jobs")
    op.drop_index("ix_value_import_jobs_submitted_by", table_name="value_import_jobs")
    op.drop_index("ix_value_import_jobs_status", table_name="value_import_jobs")
    op.drop_table("value_import_jobs")
