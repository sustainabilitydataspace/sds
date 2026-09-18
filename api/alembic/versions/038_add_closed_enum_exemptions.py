"""Add the closed-enum exemption table (VARCH-2 coverage-gate dependency).

The coverage gate (candidate-v14 "Coverage Denominator") requires every active subject to
join EITHER an approved atomization contract/binding OR an *unexpired closed-enum exemption*.
The exemption table was planned for VARCH-1e but omitted from migration 036; this migration
adds it as the gate's second coverage path.

semantic_closed_enum_exemptions is a bitemporal, versioned subject following the VARCH-1b/1c
pattern (decision_commit_id, valid interval, published->superseded version chain, per-logical
-key btree_gist EXCLUDE no-overlap among published rows, fork-prevention partial unique, hash
hex CHECK, resolver index) and reuses the GENERIC sds_reject_versioned_mutation trigger
created in migration 033 (NO new trigger function here). ``scope_kind``/``tenant_id`` mirror
semantic_scope_assignments so the gate is scope/tenant-aware. ``expires_at`` is the closed-enum
exemption expiry: an exemption only covers a subject when expires_at is in the future relative
to the coverage slice read time (separate from bitemporal validity). Seed data is deferred to
VARCH-3.

Revision ID: 038_add_closed_enum_exemptions
Revises: 038_merge_demo_cleanup_with_evs
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op

revision = "038_add_closed_enum_exemptions"
down_revision = "038_merge_demo_cleanup_with_evs"
branch_labels = None
depends_on = None

# Shared bitemporal column fragment (raw SQL), identical to migrations 033-037.
_BITEMPORAL = """
            decision_commit_id BIGINT NOT NULL
                REFERENCES decision_commit_sequence (commit_id),
            valid_from TIMESTAMPTZ NOT NULL,
            valid_to TIMESTAMPTZ,
            status VARCHAR(20) NOT NULL DEFAULT 'published',
            created_by VARCHAR REFERENCES semantic_stewards (id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
"""

# The eight denominator subject kinds (mirrors src/semantic/coverage.py SUBJECT_KINDS and the
# semantic_models.py _CLOSED_ENUM_SUBJECT_KINDS list).
_SUBJECT_KINDS = (
    "'concept', 'canonical_concept', 'standard_datapoint', "
    "'mapping_assertion_group', 'mapping_assertion_component', "
    "'calculation_contract', 'calculation_component', 'calculation_dimension'"
)


def _common_checks(prefix: str, hash_col: str) -> str:
    return f"""
            CONSTRAINT ck_{prefix}_status
                CHECK (status IN ('published', 'superseded')),
            CONSTRAINT ck_{prefix}_valid_interval
                CHECK (valid_to IS NULL OR valid_to > valid_from),
            CONSTRAINT ck_{prefix}_hash_hex
                CHECK ({hash_col} IS NULL OR {hash_col} ~ '^[0-9a-f]{{64}}$')
    """


def upgrade() -> None:
    """Create semantic_closed_enum_exemptions and attach the shared 033 guard."""
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS semantic_closed_enum_exemptions (
            id VARCHAR PRIMARY KEY,
            subject_kind VARCHAR(40) NOT NULL,
            subject_ref VARCHAR NOT NULL,
            scope_kind VARCHAR(20) NOT NULL,
            tenant_id VARCHAR NOT NULL DEFAULT '__public__',
            closed_enum_ref VARCHAR NOT NULL,
            exemption_reason VARCHAR(40) NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            exemption_version BIGINT NOT NULL,
            previous_version_id VARCHAR
                REFERENCES semantic_closed_enum_exemptions (id),
            superseded_by_version_id VARCHAR
                REFERENCES semantic_closed_enum_exemptions (id),
            exemption_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_closed_enum_exemptions", "exemption_hash")},
            CONSTRAINT ck_semantic_closed_enum_exemptions_subject_kind
                CHECK (subject_kind IN ({_SUBJECT_KINDS})),
            CONSTRAINT ck_semantic_closed_enum_exemptions_scope_kind
                CHECK (scope_kind IN ('public', 'shared', 'tenant_private')),
            CONSTRAINT ck_semantic_closed_enum_exemptions_reason CHECK (
                exemption_reason IN ('closed_enum_complete', 'non_atomizable',
                    'deprecated_pending', 'other')
            ),
            CONSTRAINT ck_semantic_closed_enum_exemptions_expiry
                CHECK (expires_at > valid_from),
            CONSTRAINT uq_semantic_closed_enum_exemptions_key_ver
                UNIQUE (subject_kind, subject_ref, tenant_id, exemption_version),
            CONSTRAINT ex_semantic_closed_enum_exemptions_no_overlap EXCLUDE USING gist (
                subject_kind WITH =, subject_ref WITH =, tenant_id WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_semantic_closed_enum_exemptions_prev_version
        ON semantic_closed_enum_exemptions (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_semantic_closed_enum_exemptions_resolver
        ON semantic_closed_enum_exemptions
            (subject_kind, subject_ref, tenant_id, valid_from, valid_to,
             decision_commit_id)
        """
    )

    # Reuse the GENERIC published->superseded guard from migration 033 (none created here).
    op.execute(
        "DROP TRIGGER IF EXISTS trg_reject_semantic_closed_enum_exemptions_mutation "
        "ON semantic_closed_enum_exemptions"
    )
    op.execute(
        """
        CREATE TRIGGER trg_reject_semantic_closed_enum_exemptions_mutation
        BEFORE UPDATE OR DELETE ON semantic_closed_enum_exemptions
        FOR EACH ROW EXECUTE FUNCTION sds_reject_versioned_mutation()
        """
    )


def downgrade() -> None:
    """Drop the exemption table + its trigger ONLY.

    sds_reject_versioned_mutation is owned by migration 033 and is NOT dropped here;
    btree_gist is left installed (shared).
    """
    op.execute(
        "DROP TRIGGER IF EXISTS trg_reject_semantic_closed_enum_exemptions_mutation "
        "ON semantic_closed_enum_exemptions"
    )
    op.execute("DROP TABLE IF EXISTS semantic_closed_enum_exemptions")
