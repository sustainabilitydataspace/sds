"""Add the admin demo package install ledger.

One row per install attempt of a bundled demo package (package id and pinned
digest, actor, request id, state, failed step and error class, per-component
counts, materialization hashes, functional check outcomes). Never stores
values or package contents.

Revision ID: 051_add_admin_demo_package_installs
Revises: 050_add_admin_error_diagnostics
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "051_add_admin_demo_package_installs"
down_revision = "050_add_admin_error_diagnostics"
branch_labels = None
depends_on = None

STATES = ("installed", "verified", "installed_unverified", "failed")


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # Earlier migrations may leave SET LOCAL search_path = pg_catalog, ...
        op.execute("SET LOCAL search_path TO DEFAULT")
    op.create_table(
        "admin_demo_package_installs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("package_id", sa.String(length=64), nullable=False),
        sa.Column("package_digest", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.String(length=200), nullable=False),
        sa.Column("auth_method", sa.String(length=30), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("failed_step", sa.String(length=40), nullable=True),
        sa.Column("error_class", sa.String(length=80), nullable=True),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("materialization_hash_before", sa.String(length=64), nullable=True),
        sa.Column("materialization_hash_after", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "state IN (" + ", ".join(f"'{state}'" for state in STATES) + ")",
            name="ck_admin_demo_package_installs_state",
        ),
    )
    op.create_index(
        "ix_admin_demo_package_installs_package_id",
        "admin_demo_package_installs",
        ["package_id", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_demo_package_installs_package_id",
        table_name="admin_demo_package_installs",
    )
    op.drop_table("admin_demo_package_installs")
