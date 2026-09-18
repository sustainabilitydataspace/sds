"""Add EVS + restatement + tenant-private + disclosure/license (VARCH-1f).

Sixth and last of six VARCH-1 migration groups. Three archetypes coexist, each reusing an
existing trigger function (this migration introduces NO new trigger function):

- Bitemporal versioned subjects (VARCH-1b/1c pattern: per-logical-key btree_gist EXCLUDE
  no-overlap among published rows, version chain + partial-unique fork prevention, hash hex
  CHECK, enum CHECKs, resolver indexes; the GENERIC sds_reject_versioned_mutation from
  migration 033): semantic_effective_version_sets, semantic_restatement_events,
  semantic_trace_supersessions, semantic_restatement_subscriptions, semantic_tombstones,
  license_rights_windows, license_rights_decisions, aggregate_disclosure_policies,
  aggregate_suppression_decision_pins.
- Append-only registries (VARCH-1d shape: version VARCHAR, hash NOT NULL + UNIQUE, status
  active->deprecated; the sds_reject_profile_mutation function OWNED BY migration 035):
  private_commitment_profiles, private_commitment_keys, aggregate_disclosure_profiles.
- Immutable children / insert-only ledgers (the shared sds_reject_child_mutation from
  migration 033): semantic_effective_version_set_members, semantic_restatement_outbox,
  aggregate_release_ledger, aggregate_query_audit.
- tenant_private_value_payloads: tenant-scoped, append-only ENCRYPTED ciphertext store. It
  is NOT a publicly resolver-readable bitemporal subject, so it gets NO no-overlap EXCLUDE
  (several encrypted payloads may legitimately coexist for one logical key under key
  rotation / re-encryption); it carries tenant_id NOT NULL and the 033 child guard. Erasure
  is by crypto-shred of the referenced key, not row deletion.

The downgrade drops this slice's triggers + tables in reverse FK order ONLY. It does NOT
drop the shared functions (sds_reject_versioned_mutation / sds_reject_child_mutation owned by
033; sds_reject_profile_mutation owned by 035). btree_gist is left installed (shared).

Revision ID: 037_add_evs_restatement_tenant_private
Revises: 036_add_factors_and_consolidation
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op

revision = "037_add_evs_restatement_tenant_private"
down_revision = "036_add_factors_and_consolidation"
branch_labels = None
depends_on = None

# Shared bitemporal column fragment (raw SQL), identical to migrations 033-034.
_BITEMPORAL = """
            decision_commit_id BIGINT NOT NULL
                REFERENCES decision_commit_sequence (commit_id),
            valid_from TIMESTAMPTZ NOT NULL,
            valid_to TIMESTAMPTZ,
            status VARCHAR(20) NOT NULL DEFAULT 'published',
            created_by VARCHAR REFERENCES semantic_stewards (id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
"""

# Shared registry audit fragment (raw SQL), identical to migration 035.
_AUDIT = """
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            deprecated_at TIMESTAMPTZ,
            created_by VARCHAR REFERENCES semantic_stewards (id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
"""

# Bitemporal versioned subjects -> sds_reject_versioned_mutation (033).
_VERSIONED_TABLES = (
    "semantic_effective_version_sets",
    "semantic_restatement_events",
    "semantic_trace_supersessions",
    "semantic_restatement_subscriptions",
    "semantic_tombstones",
    "license_rights_windows",
    "license_rights_decisions",
    "aggregate_disclosure_policies",
    "aggregate_suppression_decision_pins",
)

# Append-only registries -> sds_reject_profile_mutation (035).
_REGISTRY_TABLES = (
    "private_commitment_profiles",
    "private_commitment_keys",
    "aggregate_disclosure_profiles",
)

# Immutable children / insert-only ledgers -> sds_reject_child_mutation (033).
_CHILD_TABLES = (
    "semantic_effective_version_set_members",
    "semantic_restatement_outbox",
    "aggregate_release_ledger",
    "aggregate_query_audit",
    "tenant_private_value_payloads",
)

# Full reverse-FK-order drop list for downgrade.
_ALL_TABLES_REVERSE = (
    "tenant_private_value_payloads",
    "aggregate_query_audit",
    "aggregate_release_ledger",
    "semantic_restatement_outbox",
    "semantic_effective_version_set_members",
    "aggregate_disclosure_profiles",
    "private_commitment_keys",
    "private_commitment_profiles",
    "aggregate_suppression_decision_pins",
    "aggregate_disclosure_policies",
    "license_rights_decisions",
    "license_rights_windows",
    "semantic_tombstones",
    "semantic_restatement_subscriptions",
    "semantic_trace_supersessions",
    "semantic_restatement_events",
    "semantic_effective_version_sets",
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


def _registry_checks(prefix: str, hash_col: str) -> str:
    return f"""
            CONSTRAINT ck_{prefix}_status CHECK (status IN ('active', 'deprecated')),
            CONSTRAINT ck_{prefix}_hash_hex CHECK ({hash_col} ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT uq_{prefix}_hash UNIQUE ({hash_col})
    """


def upgrade() -> None:
    """Create the VARCH-1f EVS / restatement / tenant-private / disclosure tables."""
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # === Bitemporal versioned subjects ================================================

    # --- semantic_effective_version_sets ---------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_effective_version_sets (
            id VARCHAR PRIMARY KEY,
            evs_key VARCHAR NOT NULL,
            scope_context VARCHAR NOT NULL,
            replay_manifest_ref VARCHAR NOT NULL,
            evs_version BIGINT NOT NULL,
            previous_version_id VARCHAR
                REFERENCES semantic_effective_version_sets (id),
            superseded_by_version_id VARCHAR
                REFERENCES semantic_effective_version_sets (id),
            evs_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_effective_version_sets", "evs_hash")},
            CONSTRAINT uq_semantic_evs_id_scope UNIQUE (id, scope_context),
            CONSTRAINT uq_semantic_evs_key_ver
                UNIQUE (evs_key, scope_context, evs_version),
            CONSTRAINT ex_semantic_evs_no_overlap EXCLUDE USING gist (
                evs_key WITH =, scope_context WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_evs_prev_version
        ON semantic_effective_version_sets (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_evs_resolver
        ON semantic_effective_version_sets
            (evs_key, scope_context, valid_from, valid_to, decision_commit_id)
        """)

    # --- semantic_restatement_events -------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_restatement_events (
            id VARCHAR PRIMARY KEY,
            restatement_key VARCHAR NOT NULL,
            subject_kind VARCHAR(40) NOT NULL,
            subject_ref VARCHAR NOT NULL,
            restatement_policy VARCHAR(40) NOT NULL,
            reason_code VARCHAR(40) NOT NULL,
            restatement_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES semantic_restatement_events (id),
            superseded_by_version_id VARCHAR
                REFERENCES semantic_restatement_events (id),
            restatement_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_restatement_events", "restatement_hash")},
            CONSTRAINT ck_semantic_restatement_events_policy CHECK (
                restatement_policy IN
                ('freeze_prior_trace', 'supersede_trace', 'recompute_trace')
            ),
            CONSTRAINT uq_semantic_restatement_events_key_ver
                UNIQUE (restatement_key, restatement_version),
            CONSTRAINT ex_semantic_restatement_events_no_overlap EXCLUDE USING gist (
                subject_kind WITH =, subject_ref WITH =, restatement_key WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_restatement_events_prev_version
        ON semantic_restatement_events (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_restatement_events_resolver
        ON semantic_restatement_events
            (subject_kind, subject_ref, valid_from, valid_to, decision_commit_id)
        """)

    # --- semantic_trace_supersessions ------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_trace_supersessions (
            id VARCHAR PRIMARY KEY,
            prior_trace_ref VARCHAR NOT NULL,
            successor_trace_ref VARCHAR NOT NULL,
            restatement_id VARCHAR NOT NULL
                REFERENCES semantic_restatement_events (id),
            supersession_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES semantic_trace_supersessions (id),
            superseded_by_version_id VARCHAR
                REFERENCES semantic_trace_supersessions (id),
            supersession_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_trace_supersessions", "supersession_hash")},
            CONSTRAINT ck_semantic_trace_supersessions_distinct
                CHECK (prior_trace_ref <> successor_trace_ref),
            CONSTRAINT uq_semantic_trace_supersessions_key_ver
                UNIQUE (prior_trace_ref, successor_trace_ref, supersession_version),
            CONSTRAINT ex_semantic_trace_supersessions_no_overlap EXCLUDE USING gist (
                prior_trace_ref WITH =, successor_trace_ref WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_trace_supersessions_prev_version
        ON semantic_trace_supersessions (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_trace_supersessions_resolver
        ON semantic_trace_supersessions
            (prior_trace_ref, valid_from, valid_to, decision_commit_id)
        """)

    # --- semantic_restatement_subscriptions ------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_restatement_subscriptions (
            id VARCHAR PRIMARY KEY,
            subscriber_ref VARCHAR NOT NULL,
            subject_kind VARCHAR(40) NOT NULL,
            subject_ref VARCHAR NOT NULL,
            delivery_status VARCHAR(20) NOT NULL,
            subscription_version BIGINT NOT NULL,
            previous_version_id VARCHAR
                REFERENCES semantic_restatement_subscriptions (id),
            superseded_by_version_id VARCHAR
                REFERENCES semantic_restatement_subscriptions (id),
            subscription_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks(
                "semantic_restatement_subscriptions", "subscription_hash"
            )},
            CONSTRAINT ck_semantic_restatement_subscriptions_delivery CHECK (
                delivery_status IN ('active', 'paused', 'dead_letter')
            ),
            CONSTRAINT uq_semantic_restatement_subscriptions_key_ver
                UNIQUE (subscriber_ref, subject_kind, subject_ref,
                    subscription_version),
            CONSTRAINT ex_semantic_restatement_subscriptions_no_overlap
                EXCLUDE USING gist (
                    subscriber_ref WITH =, subject_kind WITH =, subject_ref WITH =,
                    tstzrange(valid_from, valid_to) WITH &&
                ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_semantic_restatement_subscriptions_prev_version
        ON semantic_restatement_subscriptions (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_restatement_subscriptions_resolver
        ON semantic_restatement_subscriptions
            (subscriber_ref, valid_from, valid_to, decision_commit_id)
        """)

    # --- semantic_tombstones ---------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_tombstones (
            id VARCHAR PRIMARY KEY,
            subject_kind VARCHAR(40) NOT NULL,
            subject_ref VARCHAR NOT NULL,
            tombstone_reason VARCHAR(40) NOT NULL,
            restatement_id VARCHAR REFERENCES semantic_restatement_events (id),
            tombstone_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES semantic_tombstones (id),
            superseded_by_version_id VARCHAR REFERENCES semantic_tombstones (id),
            tombstone_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_tombstones", "tombstone_hash")},
            CONSTRAINT ck_semantic_tombstones_reason CHECK (
                tombstone_reason IN ('consent_withdrawal', 'license_revocation',
                    'erasure', 'restatement', 'other')
            ),
            CONSTRAINT uq_semantic_tombstones_key_ver
                UNIQUE (subject_kind, subject_ref, tombstone_version),
            CONSTRAINT ex_semantic_tombstones_no_overlap EXCLUDE USING gist (
                subject_kind WITH =, subject_ref WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_tombstones_prev_version
        ON semantic_tombstones (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_tombstones_resolver
        ON semantic_tombstones
            (subject_kind, subject_ref, valid_from, valid_to, decision_commit_id)
        """)

    # --- license_rights_windows ------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS license_rights_windows (
            id VARCHAR PRIMARY KEY,
            license_key VARCHAR NOT NULL,
            source_authority VARCHAR NOT NULL,
            dataset_ref VARCHAR NOT NULL,
            license_id VARCHAR NOT NULL,
            rights_scope VARCHAR(40) NOT NULL,
            access_scope VARCHAR(40) NOT NULL,
            redistribution_grant BOOLEAN NOT NULL DEFAULT false,
            derivative_use_grant BOOLEAN NOT NULL DEFAULT false,
            license_window_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES license_rights_windows (id),
            superseded_by_version_id VARCHAR REFERENCES license_rights_windows (id),
            license_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("license_rights_windows", "license_hash")},
            CONSTRAINT uq_license_rights_windows_id_key UNIQUE (id, license_key),
            CONSTRAINT uq_license_rights_windows_key_ver
                UNIQUE (license_key, license_window_version),
            CONSTRAINT ex_license_rights_windows_no_overlap EXCLUDE USING gist (
                license_key WITH =, tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_license_rights_windows_prev_version
        ON license_rights_windows (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_license_rights_windows_resolver
        ON license_rights_windows
            (license_key, valid_from, valid_to, decision_commit_id)
        """)

    # --- license_rights_decisions ----------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS license_rights_decisions (
            id VARCHAR PRIMARY KEY,
            license_window_id VARCHAR NOT NULL,
            license_key VARCHAR NOT NULL,
            decision_key VARCHAR NOT NULL,
            temporal_egress_policy VARCHAR(40) NOT NULL,
            allowed_recipient_scope VARCHAR(40) NOT NULL,
            decision_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES license_rights_decisions (id),
            superseded_by_version_id VARCHAR
                REFERENCES license_rights_decisions (id),
            decision_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("license_rights_decisions", "decision_hash")},
            CONSTRAINT ck_license_rights_decisions_egress_policy CHECK (
                temporal_egress_policy IN
                ('trace_decision_time', 'egress_read_time', 'retroactive_restatement')
            ),
            CONSTRAINT fk_license_rights_decisions_window_key
                FOREIGN KEY (license_window_id, license_key)
                REFERENCES license_rights_windows (id, license_key),
            CONSTRAINT uq_license_rights_decisions_key_ver
                UNIQUE (decision_key, decision_version),
            CONSTRAINT ex_license_rights_decisions_no_overlap EXCLUDE USING gist (
                license_window_id WITH =, decision_key WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_license_rights_decisions_prev_version
        ON license_rights_decisions (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_license_rights_decisions_resolver
        ON license_rights_decisions
            (decision_key, valid_from, valid_to, decision_commit_id)
        """)

    # --- aggregate_disclosure_policies -----------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS aggregate_disclosure_policies (
            id VARCHAR PRIMARY KEY,
            policy_key VARCHAR NOT NULL,
            scope_context VARCHAR NOT NULL,
            disclosure_profile_ref VARCHAR NOT NULL,
            min_cohort_k INTEGER NOT NULL,
            policy_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES aggregate_disclosure_policies (id),
            superseded_by_version_id VARCHAR
                REFERENCES aggregate_disclosure_policies (id),
            policy_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("aggregate_disclosure_policies", "policy_hash")},
            CONSTRAINT ck_aggregate_disclosure_policies_min_k CHECK (min_cohort_k >= 1),
            CONSTRAINT uq_aggregate_disclosure_policies_key_ver
                UNIQUE (policy_key, scope_context, policy_version),
            CONSTRAINT ex_aggregate_disclosure_policies_no_overlap EXCLUDE USING gist (
                policy_key WITH =, scope_context WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_aggregate_disclosure_policies_prev_version
        ON aggregate_disclosure_policies (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_aggregate_disclosure_policies_resolver
        ON aggregate_disclosure_policies
            (policy_key, scope_context, valid_from, valid_to, decision_commit_id)
        """)

    # --- aggregate_suppression_decision_pins -----------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS aggregate_suppression_decision_pins (
            id VARCHAR PRIMARY KEY,
            suppression_key VARCHAR NOT NULL,
            disclosure_profile_ref VARCHAR NOT NULL,
            released_cell_set_hash CHAR(64),
            complementary_selection_hash CHAR(64),
            suppression_decision VARCHAR(20) NOT NULL,
            pin_version BIGINT NOT NULL,
            previous_version_id VARCHAR
                REFERENCES aggregate_suppression_decision_pins (id),
            superseded_by_version_id VARCHAR
                REFERENCES aggregate_suppression_decision_pins (id),
            pin_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("aggregate_suppression_decision_pins", "pin_hash")},
            CONSTRAINT ck_aggregate_suppression_decision_pins_decision CHECK (
                suppression_decision IN ('released', 'suppressed')
            ),
            CONSTRAINT ck_aggregate_suppression_decision_pins_cells_hex CHECK (
                released_cell_set_hash IS NULL
                OR released_cell_set_hash ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT ck_aggregate_suppression_decision_pins_compl_hex CHECK (
                complementary_selection_hash IS NULL
                OR complementary_selection_hash ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT uq_aggregate_suppression_decision_pins_key_ver
                UNIQUE (suppression_key, pin_version),
            CONSTRAINT ex_aggregate_suppression_decision_pins_no_overlap
                EXCLUDE USING gist (
                    suppression_key WITH =,
                    tstzrange(valid_from, valid_to) WITH &&
                ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_aggregate_suppression_decision_pins_prev_version
        ON aggregate_suppression_decision_pins (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_aggregate_suppression_decision_pins_resolver
        ON aggregate_suppression_decision_pins
            (suppression_key, valid_from, valid_to, decision_commit_id)
        """)

    # === Append-only registries =======================================================

    # --- private_commitment_profiles -------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS private_commitment_profiles (
            id VARCHAR PRIMARY KEY,
            profile_id VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            algorithm VARCHAR(40) NOT NULL,
            descriptor JSONB NOT NULL,
            profile_hash CHAR(64) NOT NULL,
            {_AUDIT},
            {_registry_checks("private_commitment_profiles", "profile_hash")},
            CONSTRAINT uq_private_commitment_profiles_id_ver
                UNIQUE (profile_id, version)
        )
        """)

    # --- private_commitment_keys -----------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS private_commitment_keys (
            id VARCHAR PRIMARY KEY,
            key_id VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            tenant_id VARCHAR NOT NULL,
            key_generation INTEGER NOT NULL,
            commitment_profile_pk VARCHAR NOT NULL
                REFERENCES private_commitment_profiles (id),
            key_hash CHAR(64) NOT NULL,
            {_AUDIT},
            {_registry_checks("private_commitment_keys", "key_hash")},
            CONSTRAINT ck_private_commitment_keys_generation
                CHECK (key_generation >= 0),
            CONSTRAINT uq_private_commitment_keys_id_ver UNIQUE (key_id, version)
        )
        """)

    # --- aggregate_disclosure_profiles -----------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS aggregate_disclosure_profiles (
            id VARCHAR PRIMARY KEY,
            profile_id VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            descriptor JSONB NOT NULL,
            profile_hash CHAR(64) NOT NULL,
            fixture_corpus_sha256 CHAR(64),
            {_AUDIT},
            {_registry_checks("aggregate_disclosure_profiles", "profile_hash")},
            CONSTRAINT ck_aggregate_disclosure_profiles_corpus_hex CHECK (
                fixture_corpus_sha256 IS NULL
                OR fixture_corpus_sha256 ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT uq_aggregate_disclosure_profiles_id_ver
                UNIQUE (profile_id, version)
        )
        """)

    # === Immutable children / insert-only ledgers =====================================

    # --- semantic_effective_version_set_members (immutable child of the EVS) ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS semantic_effective_version_set_members (
            id VARCHAR PRIMARY KEY,
            effective_version_set_id VARCHAR NOT NULL,
            scope_context VARCHAR NOT NULL,
            subject_kind VARCHAR(40) NOT NULL,
            subject_ref VARCHAR NOT NULL,
            resolved_version_id VARCHAR NOT NULL,
            member_hash CHAR(64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_semantic_evs_members_hash_hex
                CHECK (member_hash IS NULL OR member_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT uq_semantic_evs_members_evs_subject
                UNIQUE (effective_version_set_id, subject_kind, subject_ref),
            CONSTRAINT fk_semantic_evs_members_evs_scope
                FOREIGN KEY (effective_version_set_id, scope_context)
                REFERENCES semantic_effective_version_sets (id, scope_context)
        )
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_evs_members_resolver
        ON semantic_effective_version_set_members
            (effective_version_set_id, subject_kind, subject_ref)
        """)

    # --- semantic_restatement_outbox (insert-only ledger) ----------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS semantic_restatement_outbox (
            id VARCHAR PRIMARY KEY,
            restatement_id VARCHAR NOT NULL
                REFERENCES semantic_restatement_events (id),
            subscriber_ref VARCHAR NOT NULL,
            idempotency_key VARCHAR NOT NULL,
            decision_commit_id BIGINT NOT NULL
                REFERENCES decision_commit_sequence (commit_id),
            event_schema_version VARCHAR NOT NULL,
            payload JSONB NOT NULL,
            event_hash CHAR(64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_semantic_restatement_outbox_hash_hex
                CHECK (event_hash IS NULL OR event_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT uq_semantic_restatement_outbox_idempotency
                UNIQUE (idempotency_key)
        )
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_restatement_outbox_delivery
        ON semantic_restatement_outbox (subscriber_ref, decision_commit_id)
        """)

    # --- aggregate_release_ledger (insert-only ledger) -------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS aggregate_release_ledger (
            id VARCHAR PRIMARY KEY,
            release_key VARCHAR NOT NULL,
            scope_context VARCHAR NOT NULL,
            disclosure_profile_ref VARCHAR NOT NULL,
            suppression_decision_pin_id VARCHAR
                REFERENCES aggregate_suppression_decision_pins (id),
            license_rights_decision_id VARCHAR
                REFERENCES license_rights_decisions (id),
            allowed_recipient_scope VARCHAR(40) NOT NULL,
            decision_commit_id BIGINT NOT NULL
                REFERENCES decision_commit_sequence (commit_id),
            released_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            release_hash CHAR(64),
            CONSTRAINT ck_aggregate_release_ledger_hash_hex
                CHECK (release_hash IS NULL OR release_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT uq_aggregate_release_ledger_release_key UNIQUE (release_key)
        )
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_aggregate_release_ledger_resolver
        ON aggregate_release_ledger (scope_context, decision_commit_id)
        """)

    # --- aggregate_query_audit (insert-only ledger) ----------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS aggregate_query_audit (
            id VARCHAR PRIMARY KEY,
            audit_key VARCHAR NOT NULL,
            scope_context VARCHAR NOT NULL,
            subscriber_ref VARCHAR,
            disclosure_profile_ref VARCHAR NOT NULL,
            timing_bucket VARCHAR(40),
            query_count INTEGER NOT NULL DEFAULT 1,
            audit_hash CHAR(64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_aggregate_query_audit_hash_hex
                CHECK (audit_hash IS NULL OR audit_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_aggregate_query_audit_query_count CHECK (query_count >= 1),
            CONSTRAINT uq_aggregate_query_audit_audit_key UNIQUE (audit_key)
        )
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_aggregate_query_audit_resolver
        ON aggregate_query_audit (scope_context, subscriber_ref, created_at)
        """)

    # === Tenant-private encrypted payload store =======================================
    # NOT publicly bitemporal-overlap-checked: opaque ciphertext, tenant-scoped, append-
    # only. Several payloads may coexist for one logical key (rotation / re-encryption),
    # so a valid-time no-overlap EXCLUDE is intentionally omitted. Erasure = crypto-shred
    # of the referenced key, never row DELETE (enforced by the 033 child guard).
    op.execute("""
        CREATE TABLE IF NOT EXISTS tenant_private_value_payloads (
            id VARCHAR PRIMARY KEY,
            tenant_id VARCHAR NOT NULL,
            payload_key VARCHAR NOT NULL,
            value_context_ref VARCHAR NOT NULL,
            payload_ciphertext BYTEA NOT NULL,
            dek_ref VARCHAR NOT NULL,
            commitment_key_id VARCHAR REFERENCES private_commitment_keys (id),
            commitment_profile_ref VARCHAR,
            private_input_schema_ref VARCHAR,
            payload_version BIGINT NOT NULL,
            payload_hash CHAR(64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_tenant_private_value_payloads_hash_hex
                CHECK (payload_hash IS NULL OR payload_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT uq_tenant_private_value_payloads_key_ver
                UNIQUE (tenant_id, payload_key, payload_version)
        )
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tenant_private_value_payloads_resolver
        ON tenant_private_value_payloads
            (tenant_id, payload_key, value_context_ref)
        """)

    # === Triggers (reuse existing functions only; NONE created here) ===================
    # Bitemporal versioned subjects: generic published->superseded guard (033).
    for table in _VERSIONED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"""
            CREATE TRIGGER trg_reject_{table}_mutation
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION sds_reject_versioned_mutation()
            """)
    # Append-only registries: active->deprecated guard (owned by migration 035).
    for table in _REGISTRY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"""
            CREATE TRIGGER trg_reject_{table}_mutation
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION sds_reject_profile_mutation()
            """)
    # Immutable children / insert-only ledgers / tenant-private: full no-mutation (033).
    for table in _CHILD_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"""
            CREATE TRIGGER trg_reject_{table}_mutation
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION sds_reject_child_mutation()
            """)


def downgrade() -> None:
    """Drop the VARCH-1f tables + triggers in reverse FK order ONLY.

    The shared functions are NOT dropped here:
      - sds_reject_versioned_mutation / sds_reject_child_mutation are owned by 033;
      - sds_reject_profile_mutation is owned by 035.
    btree_gist is left installed (shared).
    """
    for table in _ALL_TABLES_REVERSE:
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
