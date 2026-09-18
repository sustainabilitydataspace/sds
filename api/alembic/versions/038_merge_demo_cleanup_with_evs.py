"""Merge demo cleanup with EVS restatement migration branch.

Revision ID: 038_merge_demo_cleanup_with_evs
Revises: 036_remove_persisted_demo_indicators, 037_add_evs_restatement_tenant_private
Create Date: 2026-06-22
"""

from __future__ import annotations

revision = "038_merge_demo_cleanup_with_evs"
down_revision = (
    "036_remove_persisted_demo_indicators",
    "037_add_evs_restatement_tenant_private",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
