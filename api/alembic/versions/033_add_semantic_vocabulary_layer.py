"""Add the bitemporal semantic vocabulary + atomization contract layer (VARCH-1b).

Second of six VARCH-1 migration groups. Creates the governance/versioning overlay every
resolver-readable semantic subject needs, bound to the VARCH-1a decision-time backbone:

- semantic_axes / semantic_terms / semantic_term_relations — versioned vocabulary + graph
- partition_sets (+ partition_set_members) — MECE partitions; members are forced to share
  the partition's axis via composite FKs
- concept_atomization_contracts (+ contract_required_axes) — per-subject atomization
  contract (formula_kind / aggregation / required axes / partition); this is what the
  ONTO-CONTRACT slice exposes
- semantic_scope_assignments — tenant-aware scope metadata

Every main table is bitemporal (valid + decision time), append-only, versioned, and
content-addressable. DDL guardrails: per-logical-key btree_gist EXCLUDE preventing
valid-time overlap among published rows; partial-unique version-chain fork prevention;
generic append-only triggers (published->superseded only for versioned tables; fully
immutable child tables). This is an overlay: it intentionally does NOT FK the existing flat
canonical_calculation_dimensions (wired later in VARCH-5 / ONTO-CONTRACT).

Revision ID: 033_add_semantic_vocabulary_layer
Revises: 032_add_semantic_decision_backbone
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op

revision = "033_add_semantic_vocabulary_layer"
down_revision = "032_add_semantic_decision_backbone"
branch_labels = None
depends_on = None

# Shared column fragments (raw SQL).
_BITEMPORAL = """
            decision_commit_id BIGINT NOT NULL
                REFERENCES decision_commit_sequence (commit_id),
            valid_from TIMESTAMPTZ NOT NULL,
            valid_to TIMESTAMPTZ,
            status VARCHAR(20) NOT NULL DEFAULT 'published',
            created_by VARCHAR REFERENCES semantic_stewards (id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
"""


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
    """Create the VARCH-1b vocabulary + contract tables and guardrails."""
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # --- semantic_axes ---------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_axes (
            id VARCHAR PRIMARY KEY,
            axis_key VARCHAR NOT NULL,
            label VARCHAR,
            description VARCHAR,
            axis_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES semantic_axes (id),
            superseded_by_version_id VARCHAR REFERENCES semantic_axes (id),
            axis_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_axes", "axis_hash")},
            CONSTRAINT uq_semantic_axes_key_ver UNIQUE (axis_key, axis_version),
            CONSTRAINT ex_semantic_axes_no_overlap EXCLUDE USING gist (
                axis_key WITH =, tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_axes_prev_version
        ON semantic_axes (previous_version_id) WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_axes_resolver
        ON semantic_axes (axis_key, valid_from, valid_to, decision_commit_id)
        """)

    # --- semantic_terms --------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_terms (
            id VARCHAR PRIMARY KEY,
            axis_id VARCHAR NOT NULL REFERENCES semantic_axes (id),
            term_key VARCHAR NOT NULL,
            label VARCHAR,
            description VARCHAR,
            term_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES semantic_terms (id),
            superseded_by_version_id VARCHAR REFERENCES semantic_terms (id),
            term_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_terms", "term_hash")},
            CONSTRAINT uq_semantic_terms_id_axis UNIQUE (id, axis_id),
            CONSTRAINT uq_semantic_terms_key_ver
                UNIQUE (axis_id, term_key, term_version),
            CONSTRAINT ex_semantic_terms_no_overlap EXCLUDE USING gist (
                axis_id WITH =, term_key WITH =, tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_terms_prev_version
        ON semantic_terms (previous_version_id) WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_terms_resolver
        ON semantic_terms (axis_id, term_key, valid_from, valid_to, decision_commit_id)
        """)

    # --- semantic_term_relations -----------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_term_relations (
            id VARCHAR PRIMARY KEY,
            source_term_id VARCHAR NOT NULL REFERENCES semantic_terms (id),
            target_term_id VARCHAR NOT NULL REFERENCES semantic_terms (id),
            relation_type VARCHAR(20) NOT NULL,
            relation_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES semantic_term_relations (id),
            superseded_by_version_id VARCHAR REFERENCES semantic_term_relations (id),
            relation_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_term_relations", "relation_hash")},
            CONSTRAINT ck_semantic_term_relations_type CHECK (
                relation_type IN
                ('broader', 'narrower', 'related', 'equivalent', 'excludes')
            ),
            CONSTRAINT ck_semantic_term_relations_distinct
                CHECK (source_term_id <> target_term_id),
            CONSTRAINT uq_semantic_term_relations_key_ver UNIQUE
                (source_term_id, target_term_id, relation_type, relation_version),
            CONSTRAINT ex_semantic_term_relations_no_overlap EXCLUDE USING gist (
                source_term_id WITH =, target_term_id WITH =, relation_type WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_term_relations_prev_version
        ON semantic_term_relations (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_term_relations_resolver
        ON semantic_term_relations
            (source_term_id, target_term_id, valid_from, valid_to)
        """)

    # --- partition_sets --------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS partition_sets (
            id VARCHAR PRIMARY KEY,
            partition_key VARCHAR NOT NULL,
            axis_id VARCHAR NOT NULL REFERENCES semantic_axes (id),
            partition_version BIGINT NOT NULL,
            is_mece BOOLEAN NOT NULL DEFAULT false,
            previous_version_id VARCHAR REFERENCES partition_sets (id),
            superseded_by_version_id VARCHAR REFERENCES partition_sets (id),
            partition_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("partition_sets", "partition_hash")},
            CONSTRAINT uq_partition_sets_id_axis UNIQUE (id, axis_id),
            CONSTRAINT uq_partition_sets_key_ver
                UNIQUE (partition_key, partition_version),
            CONSTRAINT ex_partition_sets_no_overlap EXCLUDE USING gist (
                partition_key WITH =, tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_partition_sets_prev_version
        ON partition_sets (previous_version_id) WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_partition_sets_resolver
        ON partition_sets (axis_id, valid_from, valid_to)
        """)

    # --- partition_set_members (immutable child) -------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS partition_set_members (
            id VARCHAR PRIMARY KEY,
            partition_set_id VARCHAR NOT NULL,
            axis_id VARCHAR NOT NULL,
            term_id VARCHAR NOT NULL,
            member_order INTEGER,
            member_hash CHAR(64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_partition_set_members_hash_hex
                CHECK (member_hash IS NULL OR member_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT uq_partition_set_members_pset_term
                UNIQUE (partition_set_id, term_id),
            CONSTRAINT fk_partition_set_members_partition_axis
                FOREIGN KEY (partition_set_id, axis_id)
                REFERENCES partition_sets (id, axis_id),
            CONSTRAINT fk_partition_set_members_term_axis
                FOREIGN KEY (term_id, axis_id)
                REFERENCES semantic_terms (id, axis_id)
        )
        """)

    # --- concept_atomization_contracts -----------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS concept_atomization_contracts (
            id VARCHAR PRIMARY KEY,
            subject_kind VARCHAR(40) NOT NULL,
            subject_ref VARCHAR NOT NULL,
            contract_key VARCHAR NOT NULL,
            contract_version BIGINT NOT NULL,
            formula_kind VARCHAR(40) NOT NULL,
            aggregation_policy VARCHAR(40) NOT NULL,
            partition_set_id VARCHAR REFERENCES partition_sets (id),
            previous_version_id VARCHAR REFERENCES concept_atomization_contracts (id),
            superseded_by_version_id VARCHAR
                REFERENCES concept_atomization_contracts (id),
            contract_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("concept_atomization_contracts", "contract_hash")},
            CONSTRAINT ck_concept_atomization_contracts_subject_kind CHECK (
                subject_kind IN ('concept', 'canonical_concept', 'standard_datapoint')
            ),
            CONSTRAINT ck_concept_atomization_contracts_formula_kind CHECK (
                formula_kind IN
                ('dimensional_input', 'derived_formula', 'measured', 'other')
            ),
            CONSTRAINT ck_concept_atomization_contracts_aggregation CHECK (
                aggregation_policy IN ('none', 'sum', 'weighted', 'custom')
            ),
            CONSTRAINT uq_concept_atomization_contracts_key_ver
                UNIQUE (contract_key, contract_version),
            CONSTRAINT ex_concept_atomization_contracts_no_overlap EXCLUDE USING gist (
                subject_kind WITH =, subject_ref WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_concept_atomization_contracts_prev_version
        ON concept_atomization_contracts (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_concept_atomization_contracts_resolver
        ON concept_atomization_contracts
            (subject_kind, subject_ref, valid_from, valid_to, decision_commit_id)
        """)

    # --- contract_required_axes (immutable child) ------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS contract_required_axes (
            id VARCHAR PRIMARY KEY,
            contract_id VARCHAR NOT NULL
                REFERENCES concept_atomization_contracts (id),
            axis_id VARCHAR NOT NULL REFERENCES semantic_axes (id),
            required BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_contract_required_axes_contract_axis
                UNIQUE (contract_id, axis_id)
        )
        """)

    # --- semantic_scope_assignments --------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS semantic_scope_assignments (
            id VARCHAR PRIMARY KEY,
            subject_kind VARCHAR NOT NULL,
            subject_id VARCHAR NOT NULL,
            scope_kind VARCHAR(20) NOT NULL,
            tenant_id VARCHAR NOT NULL DEFAULT '__public__',
            scope_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES semantic_scope_assignments (id),
            superseded_by_version_id VARCHAR
                REFERENCES semantic_scope_assignments (id),
            scope_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("semantic_scope_assignments", "scope_hash")},
            CONSTRAINT ck_semantic_scope_assignments_scope_kind CHECK (
                scope_kind IN ('public', 'shared', 'tenant_private')
            ),
            CONSTRAINT uq_semantic_scope_assignments_key_ver
                UNIQUE (subject_kind, subject_id, tenant_id, scope_version),
            CONSTRAINT ex_semantic_scope_assignments_no_overlap EXCLUDE USING gist (
                subject_kind WITH =, subject_id WITH =, tenant_id WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_scope_assignments_prev_version
        ON semantic_scope_assignments (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_scope_assignments_resolver
        ON semantic_scope_assignments (subject_kind, subject_id, valid_from, valid_to)
        """)

    # --- append-only triggers --------------------------------------------------------
    # Generic versioned-table guard: a published row may only transition to superseded
    # (status + superseded_by_version_id); every other column is immutable (jsonb diff).
    op.execute("""
        CREATE OR REPLACE FUNCTION sds_reject_versioned_mutation()
        RETURNS trigger AS $$
        DECLARE old_j jsonb; new_j jsonb;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION '% is append-only: DELETE forbidden', TG_TABLE_NAME;
            END IF;
            IF OLD.status <> 'published' THEN
                RAISE EXCEPTION '% row is immutable (status=%)',
                    TG_TABLE_NAME, OLD.status;
            END IF;
            IF NEW.status <> 'superseded' THEN
                RAISE EXCEPTION '% published row may only move to superseded',
                    TG_TABLE_NAME;
            END IF;
            IF NEW.superseded_by_version_id IS NULL THEN
                RAISE EXCEPTION '% supersede must set superseded_by_version_id',
                    TG_TABLE_NAME;
            END IF;
            old_j := to_jsonb(OLD) - 'status' - 'superseded_by_version_id';
            new_j := to_jsonb(NEW) - 'status' - 'superseded_by_version_id';
            IF old_j <> new_j THEN
                RAISE EXCEPTION '% is append-only: only the published->superseded '
                    'transition may update a published row', TG_TABLE_NAME;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """)
    # Generic immutable child guard: no UPDATE or DELETE at all.
    op.execute("""
        CREATE OR REPLACE FUNCTION sds_reject_child_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: UPDATE/DELETE forbidden', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """)

    for table in (
        "semantic_axes",
        "semantic_terms",
        "semantic_term_relations",
        "partition_sets",
        "concept_atomization_contracts",
        "semantic_scope_assignments",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"""
            CREATE TRIGGER trg_reject_{table}_mutation
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION sds_reject_versioned_mutation()
            """)

    for table in ("partition_set_members", "contract_required_axes"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"""
            CREATE TRIGGER trg_reject_{table}_mutation
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION sds_reject_child_mutation()
            """)


def downgrade() -> None:
    """Drop the VARCH-1b layer in reverse FK dependency order.

    btree_gist is left installed (shared with VARCH-1a and other schema).
    """
    for table in (
        "semantic_scope_assignments",
        "contract_required_axes",
        "concept_atomization_contracts",
        "partition_set_members",
        "partition_sets",
        "semantic_term_relations",
        "semantic_terms",
        "semantic_axes",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")

    op.execute("DROP FUNCTION IF EXISTS sds_reject_versioned_mutation()")
    op.execute("DROP FUNCTION IF EXISTS sds_reject_child_mutation()")
