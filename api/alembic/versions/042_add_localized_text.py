"""Localization foundation: append-only localized_text store (LOC-1).

Implements the SDS API localization design:
a single cross-cutting, append-only/versioned translation store keyed by canonical subject
identity (subject_kind, subject_uri, field, language, optional tenant scope) — NOT by DB primary
key. Canonical source fields (Concept.label/description, etc.) stay authoritative in V1; this table
stores APPROVED target-language DISPLAY text plus the source_hash it was approved against, so a
later source-text change marks the row stale and excludes it from public display.

Public runtime serves only ``approved`` rows whose ``source_hash`` still matches the current source
text and whose effective interval is active. Only ONE active approved row may exist per
(subject_uri, subject_kind, field, language, scope_kind, tenant_id) — enforced by partial unique
indexes (global vs tenant-scoped). Adds only a new table; reversible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "042_add_localized_text"
down_revision = "041_add_legacy_value_classifications"
branch_labels = None
depends_on = None

_HEX64 = "{col} ~ '^[0-9a-f]{{64}}$'"

_SUBJECT_KINDS = (
    "concept",
    "canonical_concept",
    "indicator",
    "standard_datapoint",
    "unit",
    "equivalence",
    "mapping_assertion",
    "calculation_contract",
    "calculation_trace_field",
    "hierarchy",
    "hierarchy_level",
    "api_operation",
    "api_field",
    "api_example",
    "error_code",
)
_FIELDS = (
    "label",
    "description",
    "title",
    "indicator_name",
    "short_label",
    "help_text",
    "example_summary",
    "example_description",
    "error_message",
)
_STATUSES = (
    "draft",
    "machine_draft",
    "reviewed",
    "approved",
    "stale",
    "deprecated",
    "rejected",
)
_SOURCE_TYPES = ("official", "human_reviewed", "machine_assisted", "customer_override")


def _in_list(col: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({joined})"


def upgrade() -> None:
    op.create_table(
        "localized_text",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("subject_kind", sa.String(40), nullable=False),
        sa.Column("subject_uri", sa.String(500), nullable=False),
        sa.Column("field", sa.String(50), nullable=False),
        sa.Column("language", sa.String(35), nullable=False),
        sa.Column("display_language", sa.String(35), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_language", sa.String(35), nullable=False),
        sa.Column("source_hash", sa.CHAR(64), nullable=False),
        sa.Column("translation_hash", sa.CHAR(64), nullable=False),
        sa.Column("source_type", sa.String(30)),
        sa.Column("source_ref", sa.String(500)),
        sa.Column("reviewer_ref", sa.String(500)),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "effective_from",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("effective_to", sa.DateTime()),
        sa.Column("scope_kind", sa.String(30)),
        sa.Column("tenant_id", sa.String()),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.String(100)),
        sa.CheckConstraint(
            _in_list("subject_kind", _SUBJECT_KINDS), name="ck_localized_text_kind"
        ),
        sa.CheckConstraint(_in_list("field", _FIELDS), name="ck_localized_text_field"),
        sa.CheckConstraint(
            _in_list("status", _STATUSES), name="ck_localized_text_status"
        ),
        sa.CheckConstraint(
            f"source_type IS NULL OR {_in_list('source_type', _SOURCE_TYPES)}",
            name="ck_localized_text_source_type",
        ),
        sa.CheckConstraint(
            _HEX64.format(col="source_hash"), name="ck_localized_text_source_hash_hex"
        ),
        sa.CheckConstraint(
            _HEX64.format(col="translation_hash"),
            name="ck_localized_text_translation_hash_hex",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_localized_text_revision_positive"),
        # approved rows must carry reviewer/source evidence (design package contract).
        sa.CheckConstraint(
            "status <> 'approved' OR (reviewer_ref IS NOT NULL AND source_ref IS NOT NULL)",
            name="ck_localized_text_approved_evidence",
        ),
    )
    op.create_index(
        "ix_localized_text_lookup",
        "localized_text",
        ["subject_kind", "subject_uri", "field", "language"],
    )
    op.create_index("ix_localized_text_status", "localized_text", ["status"])
    # one active approved GLOBAL row per subject/field/language (no tenant scope)
    op.create_index(
        "ux_localized_text_active_approved_global",
        "localized_text",
        ["subject_kind", "subject_uri", "field", "language"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'approved' AND effective_to IS NULL AND tenant_id IS NULL"
        ),
    )
    # one active approved TENANT-scoped row per subject/field/language/tenant
    op.create_index(
        "ux_localized_text_active_approved_tenant",
        "localized_text",
        ["subject_kind", "subject_uri", "field", "language", "tenant_id"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'approved' AND effective_to IS NULL AND tenant_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_localized_text_active_approved_tenant", table_name="localized_text"
    )
    op.drop_index(
        "ux_localized_text_active_approved_global", table_name="localized_text"
    )
    op.drop_index("ix_localized_text_status", table_name="localized_text")
    op.drop_index("ix_localized_text_lookup", table_name="localized_text")
    op.drop_table("localized_text")
