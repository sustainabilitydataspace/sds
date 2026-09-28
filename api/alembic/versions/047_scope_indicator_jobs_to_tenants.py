"""Scope indicator import jobs to immutable tenant and owner identities.

Revision ID: 047_scope_indicator_jobs_to_tenants
Revises: 046_harden_auth_and_value_tenancy
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "047_scope_indicator_jobs_to_tenants"
down_revision = "046_harden_auth_and_value_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "indicator_import_jobs", sa.Column("tenant_id", sa.String(), nullable=True)
    )
    op.add_column(
        "indicator_import_jobs",
        sa.Column("owner_user_id", sa.String(), nullable=True),
    )
    op.add_column(
        "indicator_import_jobs",
        sa.Column("ownership_state", sa.String(length=20), nullable=True),
    )
    op.execute(
        """
        UPDATE indicator_import_jobs AS jobs
        SET owner_user_id = users.id
        FROM user_accounts AS users
        WHERE jobs.submitted_by = users.username
        """
    )
    op.execute(
        """
        UPDATE indicator_import_jobs
        SET tenant_id = NULL,
            ownership_state = 'quarantined'
        """
    )
    op.execute(
        """
        UPDATE indicator_import_jobs
        SET status = 'failed',
            committed = FALSE,
            source_payload = NULL,
            error_message = COALESCE(
                error_message,
                'Legacy job quarantined: immutable tenant ownership is unavailable'
            ),
            completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
            updated_at = CURRENT_TIMESTAMP
        WHERE ownership_state = 'quarantined'
          AND status IN ('pending', 'running')
        """
    )
    op.execute(
        """
        UPDATE indicator_import_jobs
        SET source_payload = NULL
        WHERE ownership_state = 'quarantined'
           OR job_type = 'validation'
        """
    )
    op.alter_column("indicator_import_jobs", "ownership_state", nullable=False)
    op.create_foreign_key(
        "fk_indicator_import_jobs_owner_user_id",
        "indicator_import_jobs",
        "user_accounts",
        ["owner_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_indicator_import_jobs_ownership",
        "indicator_import_jobs",
        """
        (ownership_state = 'resolved' AND tenant_id IS NOT NULL
         AND btrim(tenant_id) <> '' AND owner_user_id IS NOT NULL
         AND btrim(owner_user_id) <> '')
        OR
        (ownership_state = 'quarantined' AND tenant_id IS NULL)
        """,
    )
    op.create_index(
        "ix_indicator_import_jobs_tenant_id",
        "indicator_import_jobs",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_indicator_import_jobs_owner_user_id",
        "indicator_import_jobs",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_indicator_import_jobs_tenant_created",
        "indicator_import_jobs",
        ["tenant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError(
        "irreversible indicator-job ownership migration; restore a pre-upgrade "
        "database snapshot rather than discarding tenant identity"
    )
