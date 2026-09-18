"""add indicator import jobs

Revision ID: 013_indicator_import_jobs
Revises: 012_value_type_names
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "013_indicator_import_jobs"
down_revision = "012_value_type_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "indicator_import_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("job_type", sa.String(length=20), nullable=False),
        sa.Column("source_format", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("submitted_by", sa.String(length=100), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("source_size_bytes", sa.Integer(), nullable=True),
        sa.Column("source_payload", sa.Text(), nullable=True),
        sa.Column("validation_job_id", sa.String(), nullable=True),
        sa.Column("request_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("total_rows", sa.Integer(), nullable=True),
        sa.Column("accepted_rows", sa.Integer(), nullable=True),
        sa.Column("rejected_rows", sa.Integer(), nullable=True),
        sa.Column("committed", sa.Boolean(), nullable=True),
        sa.Column("result_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_indicator_import_jobs_status", "indicator_import_jobs", ["status"], unique=False)
    op.create_index("ix_indicator_import_jobs_submitted_by", "indicator_import_jobs", ["submitted_by"], unique=False)
    op.create_index("ix_indicator_import_jobs_source_sha256", "indicator_import_jobs", ["source_sha256"], unique=False)
    op.create_index("ix_indicator_import_jobs_validation_job_id", "indicator_import_jobs", ["validation_job_id"], unique=False)
    op.create_index(
        "ix_indicator_import_jobs_type_status",
        "indicator_import_jobs",
        ["job_type", "status"],
        unique=False,
    )
    op.create_index(
        "ix_indicator_import_jobs_submitted_created",
        "indicator_import_jobs",
        ["submitted_by", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_indicator_import_jobs_submitted_created", table_name="indicator_import_jobs")
    op.drop_index("ix_indicator_import_jobs_type_status", table_name="indicator_import_jobs")
    op.drop_index("ix_indicator_import_jobs_validation_job_id", table_name="indicator_import_jobs")
    op.drop_index("ix_indicator_import_jobs_source_sha256", table_name="indicator_import_jobs")
    op.drop_index("ix_indicator_import_jobs_submitted_by", table_name="indicator_import_jobs")
    op.drop_index("ix_indicator_import_jobs_status", table_name="indicator_import_jobs")
    op.drop_table("indicator_import_jobs")
