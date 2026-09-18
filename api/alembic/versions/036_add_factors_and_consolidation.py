"""Add factors + consolidation (VARCH-1e).

Fifth of six VARCH-1 migration groups. Factor sets / values / vintage-selection policies are
first-class bitemporal inputs; consolidation groups / members / consents and composition
profiles / composed decisions provide the trace-safe cross-tenant consolidation surface
(candidate-v14 Core Tables):

- semantic_factor_sets — bitemporal factor-set family with provenance (source authority /
  dataset id+version / evidence hash / loose license ref). Composite UNIQUE(id, factor_set_key)
  is the member-shares-set FK target for factor values.
- semantic_factor_values — bitemporal factor value; value-shares-set composite FK to
  semantic_factor_sets(id, factor_set_key).
- factor_vintage_selection_policies — bitemporal explicit factor-vintage selection policy
  (closed-enum policy_kind); loose subject_ref (no FK).
- semantic_consolidation_groups — bitemporal authorized cross-tenant group. Composite
  UNIQUE(id, group_key) is the member-shares-group FK target for members.
- semantic_consolidation_group_members — IMMUTABLE child; member-shares-group composite FK to
  semantic_consolidation_groups(id, group_key); reuses sds_reject_child_mutation (033).
- semantic_consolidation_consents — bitemporal consent record; real FK to a group.
- consolidation_composition_profiles — bitemporal deterministic composition function (closed-
  enum composition_kind).
- consolidation_composed_decisions — bitemporal composed decision; real FK to a composition
  profile; pins composed disclosure/rights/member-set hashes.

The seven versioned mains reuse the VARCH-1b bitemporal pattern (per-key btree_gist EXCLUDE
no-overlap, version chain + partial-unique fork prevention, hash hex CHECK, enum CHECKs,
resolver indexes) and the GENERIC append-only trigger function sds_reject_versioned_mutation
created in migration 033. The immutable group-member child reuses sds_reject_child_mutation
(033). The downgrade drops this slice's triggers + tables ONLY (reverse FK order); it does
NOT drop the shared 033 trigger functions.

Revision ID: 036_add_factors_and_consolidation
Revises: 035_add_profile_registry_tables
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op

revision = "036_add_factors_and_consolidation"
down_revision = "035_add_profile_registry_tables"
branch_labels = None
depends_on = None

_BITEMPORAL = """
            decision_commit_id BIGINT NOT NULL
                REFERENCES decision_commit_sequence (commit_id),
            valid_from TIMESTAMPTZ NOT NULL,
            valid_to TIMESTAMPTZ,
            status VARCHAR(20) NOT NULL DEFAULT 'published',
            created_by VARCHAR REFERENCES semantic_stewards (id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
"""

# Versioned main tables: append-only versioned mutation trigger. Reverse-order safe for
# drops because later entries FK earlier ones (values->sets, members handled separately,
# consents->groups, composed_decisions->composition_profiles).
_VERSIONED_TABLES = (
    "semantic_factor_sets",
    "semantic_factor_values",
    "factor_vintage_selection_policies",
    "semantic_consolidation_groups",
    "semantic_consolidation_consents",
    "consolidation_composition_profiles",
    "consolidation_composed_decisions",
)
# Immutable children: fully append-only child guard.
_CHILD_TABLES = ("semantic_consolidation_group_members",)


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
    """Create the VARCH-1e factor + consolidation tables and guardrails."""
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # --- semantic_factor_sets --------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_factor_sets (
            id VARCHAR PRIMARY KEY,
            factor_set_key VARCHAR NOT NULL,
            label VARCHAR,
            source_authority VARCHAR NOT NULL,
            source_dataset_id VARCHAR,
            source_dataset_version VARCHAR,
            evidence_hash CHAR(64),
            license_ref VARCHAR,
            factor_set_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES semantic_factor_sets (id),
            superseded_by_version_id VARCHAR REFERENCES semantic_factor_sets (id),
            factor_set_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_factor_sets", "factor_set_hash")},
            CONSTRAINT ck_semantic_factor_sets_evidence_hex
                CHECK (evidence_hash IS NULL OR evidence_hash ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT uq_semantic_factor_sets_id_key
                UNIQUE (id, factor_set_key),
            CONSTRAINT uq_semantic_factor_sets_key_ver
                UNIQUE (factor_set_key, factor_set_version),
            CONSTRAINT ex_semantic_factor_sets_no_overlap EXCLUDE USING gist (
                factor_set_key WITH =, tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_factor_sets_prev_version
        ON semantic_factor_sets (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_factor_sets_resolver
        ON semantic_factor_sets
            (factor_set_key, valid_from, valid_to, decision_commit_id)
        """)

    # --- semantic_factor_values ------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_factor_values (
            id VARCHAR PRIMARY KEY,
            factor_set_id VARCHAR NOT NULL,
            factor_set_key VARCHAR NOT NULL,
            factor_key VARCHAR NOT NULL,
            numeric_value VARCHAR,
            unit_ref VARCHAR,
            quantity_kind_ref VARCHAR,
            factor_value_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES semantic_factor_values (id),
            superseded_by_version_id VARCHAR REFERENCES semantic_factor_values (id),
            factor_value_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_factor_values", "factor_value_hash")},
            CONSTRAINT fk_semantic_factor_values_set_key
                FOREIGN KEY (factor_set_id, factor_set_key)
                REFERENCES semantic_factor_sets (id, factor_set_key),
            CONSTRAINT uq_semantic_factor_values_key_ver
                UNIQUE (factor_set_id, factor_key, factor_value_version),
            CONSTRAINT ex_semantic_factor_values_no_overlap EXCLUDE USING gist (
                factor_set_id WITH =, factor_key WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_factor_values_prev_version
        ON semantic_factor_values (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_factor_values_resolver
        ON semantic_factor_values
            (factor_set_id, factor_key, valid_from, valid_to, decision_commit_id)
        """)

    # --- factor_vintage_selection_policies -------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS factor_vintage_selection_policies (
            id VARCHAR PRIMARY KEY,
            policy_key VARCHAR NOT NULL,
            subject_ref VARCHAR NOT NULL,
            policy_kind VARCHAR(40) NOT NULL,
            policy_version BIGINT NOT NULL,
            previous_version_id VARCHAR
                REFERENCES factor_vintage_selection_policies (id),
            superseded_by_version_id VARCHAR
                REFERENCES factor_vintage_selection_policies (id),
            policy_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("factor_vintage_selection_policies", "policy_hash")},
            CONSTRAINT ck_factor_vintage_selection_policies_kind CHECK (
                policy_kind IN ('latest_published', 'reporting_period_match',
                'fixed_vintage', 'as_of_decision_time', 'custom')
            ),
            CONSTRAINT uq_factor_vintage_selection_policies_key_ver
                UNIQUE (policy_key, policy_version),
            CONSTRAINT ex_factor_vintage_selection_policies_no_overlap EXCLUDE USING gist (
                policy_key WITH =, subject_ref WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_factor_vintage_selection_policies_prev_version
        ON factor_vintage_selection_policies (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_factor_vintage_selection_policies_resolver
        ON factor_vintage_selection_policies
            (policy_key, subject_ref, valid_from, valid_to, decision_commit_id)
        """)

    # --- semantic_consolidation_groups -----------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_consolidation_groups (
            id VARCHAR PRIMARY KEY,
            group_key VARCHAR NOT NULL,
            label VARCHAR,
            owner_tenant_ref VARCHAR NOT NULL,
            group_version BIGINT NOT NULL,
            previous_version_id VARCHAR
                REFERENCES semantic_consolidation_groups (id),
            superseded_by_version_id VARCHAR
                REFERENCES semantic_consolidation_groups (id),
            group_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_consolidation_groups", "group_hash")},
            CONSTRAINT uq_semantic_consolidation_groups_id_key
                UNIQUE (id, group_key),
            CONSTRAINT uq_semantic_consolidation_groups_key_ver
                UNIQUE (group_key, group_version),
            CONSTRAINT ex_semantic_consolidation_groups_no_overlap EXCLUDE USING gist (
                group_key WITH =, tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_consolidation_groups_prev_version
        ON semantic_consolidation_groups (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_consolidation_groups_resolver
        ON semantic_consolidation_groups
            (group_key, valid_from, valid_to, decision_commit_id)
        """)

    # --- semantic_consolidation_group_members (immutable child) -----------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS semantic_consolidation_group_members (
            id VARCHAR PRIMARY KEY,
            group_id VARCHAR NOT NULL,
            group_key VARCHAR NOT NULL,
            member_tenant_ref VARCHAR NOT NULL,
            member_order INTEGER,
            member_hash CHAR(64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_semantic_consolidation_group_members_hash_hex
                CHECK (member_hash IS NULL OR member_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT uq_consolidation_group_members_grp_tenant
                UNIQUE (group_id, member_tenant_ref),
            CONSTRAINT fk_consolidation_group_members_group_key
                FOREIGN KEY (group_id, group_key)
                REFERENCES semantic_consolidation_groups (id, group_key)
        )
        """)

    # --- semantic_consolidation_consents ---------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_consolidation_consents (
            id VARCHAR PRIMARY KEY,
            group_id VARCHAR NOT NULL
                REFERENCES semantic_consolidation_groups (id),
            member_tenant_ref VARCHAR NOT NULL,
            consent_status VARCHAR(20) NOT NULL,
            consent_scope VARCHAR,
            consent_version BIGINT NOT NULL,
            previous_version_id VARCHAR
                REFERENCES semantic_consolidation_consents (id),
            superseded_by_version_id VARCHAR
                REFERENCES semantic_consolidation_consents (id),
            consent_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_consolidation_consents", "consent_hash")},
            CONSTRAINT ck_semantic_consolidation_consents_consent_status CHECK (
                consent_status IN ('granted', 'withdrawn', 'pending', 'denied')
            ),
            CONSTRAINT uq_semantic_consolidation_consents_key_ver
                UNIQUE (group_id, member_tenant_ref, consent_version),
            CONSTRAINT ex_semantic_consolidation_consents_no_overlap EXCLUDE USING gist (
                group_id WITH =, member_tenant_ref WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_semantic_consolidation_consents_prev_version
        ON semantic_consolidation_consents (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_consolidation_consents_resolver
        ON semantic_consolidation_consents
            (group_id, member_tenant_ref, valid_from, valid_to, decision_commit_id)
        """)

    # --- consolidation_composition_profiles ------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS consolidation_composition_profiles (
            id VARCHAR PRIMARY KEY,
            profile_key VARCHAR NOT NULL,
            composition_kind VARCHAR(40) NOT NULL,
            descriptor JSONB,
            profile_version BIGINT NOT NULL,
            previous_version_id VARCHAR
                REFERENCES consolidation_composition_profiles (id),
            superseded_by_version_id VARCHAR
                REFERENCES consolidation_composition_profiles (id),
            profile_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("consolidation_composition_profiles", "profile_hash")},
            CONSTRAINT ck_consolidation_composition_profiles_kind CHECK (
                composition_kind IN ('most_restrictive', 'group_level_governing',
                'intersection_only', 'custom')
            ),
            CONSTRAINT uq_consolidation_composition_profiles_key_ver
                UNIQUE (profile_key, profile_version),
            CONSTRAINT ex_consolidation_composition_profiles_no_overlap
                EXCLUDE USING gist (
                    profile_key WITH =, tstzrange(valid_from, valid_to) WITH &&
                ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_consolidation_composition_profiles_prev_version
        ON consolidation_composition_profiles (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_consolidation_composition_profiles_resolver
        ON consolidation_composition_profiles
            (profile_key, valid_from, valid_to, decision_commit_id)
        """)

    # --- consolidation_composed_decisions --------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS consolidation_composed_decisions (
            id VARCHAR PRIMARY KEY,
            composition_profile_id VARCHAR NOT NULL
                REFERENCES consolidation_composition_profiles (id),
            group_ref VARCHAR NOT NULL,
            decision_key VARCHAR NOT NULL,
            decision_outcome VARCHAR(20) NOT NULL,
            member_set_hash CHAR(64),
            composed_disclosure_outcome_hash CHAR(64),
            composed_rights_decision_hash CHAR(64),
            decision_version BIGINT NOT NULL,
            previous_version_id VARCHAR
                REFERENCES consolidation_composed_decisions (id),
            superseded_by_version_id VARCHAR
                REFERENCES consolidation_composed_decisions (id),
            decision_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("consolidation_composed_decisions", "decision_hash")},
            CONSTRAINT ck_consolidation_composed_decisions_outcome CHECK (
                decision_outcome IN ('published', 'blocked')
            ),
            CONSTRAINT ck_consolidation_composed_decisions_member_hex CHECK (
                member_set_hash IS NULL OR member_set_hash ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT ck_consolidation_composed_decisions_disc_hex CHECK (
                composed_disclosure_outcome_hash IS NULL
                OR composed_disclosure_outcome_hash ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT ck_consolidation_composed_decisions_rights_hex CHECK (
                composed_rights_decision_hash IS NULL
                OR composed_rights_decision_hash ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT uq_consolidation_composed_decisions_key_ver
                UNIQUE (decision_key, decision_version),
            CONSTRAINT ex_consolidation_composed_decisions_no_overlap
                EXCLUDE USING gist (
                    decision_key WITH =, tstzrange(valid_from, valid_to) WITH &&
                ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_consolidation_composed_decisions_prev_version
        ON consolidation_composed_decisions (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_consolidation_composed_decisions_resolver
        ON consolidation_composed_decisions
            (decision_key, valid_from, valid_to, decision_commit_id)
        """)

    # Reuse the generic versioned append-only trigger function created in migration 033.
    for table in _VERSIONED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"""
            CREATE TRIGGER trg_reject_{table}_mutation
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION sds_reject_versioned_mutation()
            """)
    # Immutable group-member child: reuse the shared child guard from migration 033.
    for table in _CHILD_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"""
            CREATE TRIGGER trg_reject_{table}_mutation
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION sds_reject_child_mutation()
            """)


def downgrade() -> None:
    """Drop the VARCH-1e tables + triggers ONLY, in reverse FK order.

    The shared sds_reject_versioned_mutation / sds_reject_child_mutation functions are owned
    by migration 033 and are NOT dropped here. btree_gist is left installed (shared).
    """
    # Children first, then versioned mains in reverse FK order (decisions -> profiles,
    # consents -> groups, values -> sets).
    for table in _CHILD_TABLES + tuple(reversed(_VERSIONED_TABLES)):
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
