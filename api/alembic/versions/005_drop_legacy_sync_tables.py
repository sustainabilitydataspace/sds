"""drop legacy sync tables

Revision ID: 005_drop_legacy_sync
Revises: 004_concept_links
Create Date: 2026-04-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "005_drop_legacy_sync"
down_revision = "004_concept_links"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


def upgrade() -> None:
    tables = _table_names()
    for table_name in ("sync_state", "sync_config"):
        if table_name in tables:
            op.drop_table(table_name)


def downgrade() -> None:
    tables = _table_names()
    if "sync_config" not in tables:
        op.create_table(
            "sync_config",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("auto_sync_on_startup", sa.Boolean(), nullable=True),
            sa.Column("auto_sync_interval_minutes", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if "sync_state" not in tables:
        op.create_table(
            "sync_state",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("last_started_at", sa.DateTime(), nullable=True),
            sa.Column("last_completed_at", sa.DateTime(), nullable=True),
            sa.Column("last_success", sa.Boolean(), nullable=True),
            sa.Column("last_result", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
