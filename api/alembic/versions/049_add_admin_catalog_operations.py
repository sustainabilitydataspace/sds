"""Add the append-only admin catalog operations audit table.

Records admin calculation-contract validations/imports and unit-catalog
repairs/reversals. The highest ``ok`` id in scope ``unit_catalog`` is the
unit-catalog revision that API processes compare before serving unit lookups.

Revision ID: 049_add_admin_catalog_operations
Revises: 048_harden_value_revision_integrity
Create Date: 2026-10-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "049_add_admin_catalog_operations"
down_revision = "048_harden_value_revision_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_catalog_operations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.String(length=200), nullable=False),
        sa.Column("auth_method", sa.String(length=30), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("result", sa.String(length=30), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("package_hash", sa.String(length=64), nullable=True),
        sa.Column("repair_id", sa.String(length=64), nullable=True),
        sa.Column("unit_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("catalog_revision_before", sa.BigInteger(), nullable=True),
        sa.Column("catalog_revision_after", sa.BigInteger(), nullable=True),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column(
            "unit_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "scope IN ('calculation_contracts', 'unit_catalog')",
            name="ck_admin_catalog_operations_scope",
        ),
        sa.CheckConstraint(
            "action IN ('contract_validate', 'contract_import', "
            "'unit_repair_commit', 'unit_repair_reverse')",
            name="ck_admin_catalog_operations_action",
        ),
        sa.CheckConstraint(
            "result IN ('ok', 'already_imported', 'rejected', 'failed')",
            name="ck_admin_catalog_operations_result",
        ),
        sa.CheckConstraint(
            "action NOT IN ('unit_repair_commit', 'unit_repair_reverse') "
            "OR result <> 'ok' OR unit_snapshot IS NOT NULL",
            name="ck_admin_catalog_operations_snapshot_required",
        ),
    )
    op.create_index(
        "ix_admin_catalog_operations_scope_result_id",
        "admin_catalog_operations",
        ["scope", "result", "id"],
    )
    op.create_index(
        "ix_admin_catalog_operations_repair_id",
        "admin_catalog_operations",
        ["repair_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_catalog_operations_repair_id", table_name="admin_catalog_operations"
    )
    op.drop_index(
        "ix_admin_catalog_operations_scope_result_id",
        table_name="admin_catalog_operations",
    )
    op.drop_table("admin_catalog_operations")
