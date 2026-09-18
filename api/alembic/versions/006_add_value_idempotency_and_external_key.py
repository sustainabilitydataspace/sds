"""add value idempotency and external key

Revision ID: 006_value_idempotency
Revises: 005_drop_legacy_sync
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "006_value_idempotency"
down_revision = "005_drop_legacy_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("esg_values", sa.Column("external_key", sa.String(length=255), nullable=True))
    op.create_index(
        "ix_esg_values_external_key_unique",
        "esg_values",
        ["external_key"],
        unique=True,
        postgresql_where=sa.text("external_key IS NOT NULL"),
    )

    op.create_table(
        "value_idempotency_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("scope", sa.String(length=50), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_value_idempotency_keys_expires_at", "value_idempotency_keys", ["expires_at"], unique=False)
    op.create_index(
        "ix_value_idempotency_keys_user_scope_key",
        "value_idempotency_keys",
        ["user_id", "scope", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_value_idempotency_keys_user_scope_key", table_name="value_idempotency_keys")
    op.drop_index("ix_value_idempotency_keys_expires_at", table_name="value_idempotency_keys")
    op.drop_table("value_idempotency_keys")
    op.drop_index("ix_esg_values_external_key_unique", table_name="esg_values")
    op.drop_column("esg_values", "external_key")
