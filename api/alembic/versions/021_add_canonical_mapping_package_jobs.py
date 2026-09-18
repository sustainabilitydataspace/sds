"""add canonical mapping package jobs

Revision ID: 021_canonical_mapping_package_jobs
Revises: 020_create_conversion_engine_tables
Create Date: 2026-05-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "021_canonical_mapping_package_jobs"
down_revision = "020_create_conversion_engine_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canonical_mapping_package_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("job_type", sa.String(length=20), nullable=False),
        sa.Column("package_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("submitted_by", sa.String(length=100), nullable=False),
        sa.Column("validation_job_id", sa.String(), nullable=True),
        sa.Column(
            "request_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("total_rows", sa.Integer(), nullable=True),
        sa.Column("accepted_rows", sa.Integer(), nullable=True),
        sa.Column("rejected_rows", sa.Integer(), nullable=True),
        sa.Column("committed", sa.Boolean(), nullable=True),
        sa.Column(
            "result_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_canonical_mapping_package_jobs_status",
        "canonical_mapping_package_jobs",
        ["status"],
    )
    op.create_index(
        "ix_canonical_mapping_package_jobs_submitted_by",
        "canonical_mapping_package_jobs",
        ["submitted_by"],
    )
    op.create_index(
        "ix_canonical_mapping_package_jobs_package_id",
        "canonical_mapping_package_jobs",
        ["package_id"],
    )
    op.create_index(
        "ix_canonical_mapping_package_jobs_validation_job_id",
        "canonical_mapping_package_jobs",
        ["validation_job_id"],
    )
    op.create_index(
        "ix_canonical_mapping_package_jobs_type_status",
        "canonical_mapping_package_jobs",
        ["job_type", "status"],
    )
    op.create_index(
        "ix_canonical_mapping_package_jobs_submitted_created",
        "canonical_mapping_package_jobs",
        ["submitted_by", "created_at"],
    )
    op.create_index(
        "ix_canonical_mapping_package_jobs_package_created",
        "canonical_mapping_package_jobs",
        ["package_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canonical_mapping_package_jobs_package_created",
        table_name="canonical_mapping_package_jobs",
    )
    op.drop_index(
        "ix_canonical_mapping_package_jobs_submitted_created",
        table_name="canonical_mapping_package_jobs",
    )
    op.drop_index(
        "ix_canonical_mapping_package_jobs_type_status",
        table_name="canonical_mapping_package_jobs",
    )
    op.drop_index(
        "ix_canonical_mapping_package_jobs_validation_job_id",
        table_name="canonical_mapping_package_jobs",
    )
    op.drop_index(
        "ix_canonical_mapping_package_jobs_package_id",
        table_name="canonical_mapping_package_jobs",
    )
    op.drop_index(
        "ix_canonical_mapping_package_jobs_submitted_by",
        table_name="canonical_mapping_package_jobs",
    )
    op.drop_index(
        "ix_canonical_mapping_package_jobs_status",
        table_name="canonical_mapping_package_jobs",
    )
    op.drop_table("canonical_mapping_package_jobs")
