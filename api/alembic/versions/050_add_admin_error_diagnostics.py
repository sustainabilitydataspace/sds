"""Add the bounded, sanitized admin error diagnostics table.

Stores only exception types, in-repository frame locations, a fixed
classification and allow-listed PostgreSQL diagnostic fields for server
errors, keyed by the request id returned to the client. Retention (14 days,
newest 500 rows) is enforced by the application under an advisory lock.

Revision ID: 050_add_admin_error_diagnostics
Revises: 049_add_admin_catalog_operations
Create Date: 2026-10-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "050_add_admin_error_diagnostics"
down_revision = "049_add_admin_catalog_operations"
branch_labels = None
depends_on = None

CLASSIFICATIONS = (
    "db_unique_violation",
    "db_foreign_key_violation",
    "db_not_null_violation",
    "db_check_violation",
    "db_trigger_exception",
    "db_serialization_failure",
    "db_statement_timeout",
    "db_connection_error",
    "db_other",
    "unit_conversion_error",
    "value_ingest_error",
    "validation_error",
    "timeout",
    "unknown",
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # 048 runs SET LOCAL search_path = pg_catalog, ...; when the upgrade
        # continues in the same transaction the new table must still land in
        # the default schema, not pg_catalog.
        op.execute("SET LOCAL search_path TO DEFAULT")
    op.create_table(
        "admin_error_diagnostics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("method", sa.String(length=10), nullable=True),
        sa.Column("route_template", sa.String(length=300), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(length=40), nullable=False),
        sa.Column(
            "exception_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("frames", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("db", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", name="uq_admin_error_diagnostics_request_id"),
        sa.CheckConstraint(
            "classification IN ("
            + ", ".join(f"'{name}'" for name in CLASSIFICATIONS)
            + ")",
            name="ck_admin_error_diagnostics_classification",
        ),
    )
    op.create_index(
        "ix_admin_error_diagnostics_created_id",
        "admin_error_diagnostics",
        ["created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_error_diagnostics_created_id", table_name="admin_error_diagnostics"
    )
    op.drop_table("admin_error_diagnostics")
