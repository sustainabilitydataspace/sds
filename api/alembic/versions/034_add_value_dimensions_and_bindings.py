"""Add value dimensions + bindings (VARCH-1c).

Third of six VARCH-1 migration groups. Attaches dimensions to actual value contexts and
binds the semantic vocabulary to the Sygris / mapping / measurement-basis surfaces:

- value_context_dimensions — source of truth for (axis, term) per value context; real FK to
  the existing value_contexts(id); member-shares-axis composite FK to semantic_terms(id,
  axis_id). value_contexts.dimensions_json stays read-only derived (NOT changed here).
- sygris_variable_bindings — approved binding making a Sygris variable publicly projectable.
- mapping_component_bindings — binds a mapping assertion component (loose INTEGER ref) to a
  semantic axis (+ optional term); mapping_assertion_* receive NO changes here.
- measurement_basis_contracts — bitemporal measurement-basis family contract.

All four reuse the VARCH-1b bitemporal pattern (per-key btree_gist EXCLUDE no-overlap,
version chain + partial-unique fork prevention, hash hex CHECK, enum CHECKs, resolver
indexes) and the GENERIC append-only trigger function sds_reject_versioned_mutation created
in migration 033. The downgrade drops this slice's triggers + tables ONLY; it does NOT drop
the shared 033 trigger functions.

Revision ID: 034_add_value_dimensions_and_bindings
Revises: 033_add_semantic_vocabulary_layer
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op

revision = "034_add_value_dimensions_and_bindings"
down_revision = "033_add_semantic_vocabulary_layer"
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

_NEW_TABLES = (
    "value_context_dimensions",
    "sygris_variable_bindings",
    "mapping_component_bindings",
    "measurement_basis_contracts",
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
    """Create the VARCH-1c value-dimension + binding tables and guardrails."""
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # --- value_context_dimensions ----------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS value_context_dimensions (
            id VARCHAR PRIMARY KEY,
            value_context_id INTEGER NOT NULL REFERENCES value_contexts (id),
            axis_id VARCHAR NOT NULL REFERENCES semantic_axes (id),
            term_id VARCHAR NOT NULL,
            dim_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES value_context_dimensions (id),
            superseded_by_version_id VARCHAR REFERENCES value_context_dimensions (id),
            dim_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("value_context_dimensions", "dim_hash")},
            CONSTRAINT fk_value_context_dimensions_term_axis
                FOREIGN KEY (term_id, axis_id)
                REFERENCES semantic_terms (id, axis_id),
            CONSTRAINT uq_value_context_dimensions_key_ver
                UNIQUE (value_context_id, axis_id, dim_version),
            CONSTRAINT ex_value_context_dimensions_no_overlap EXCLUDE USING gist (
                value_context_id WITH =, axis_id WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_value_context_dimensions_prev_version
        ON value_context_dimensions (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_value_context_dimensions_resolver
        ON value_context_dimensions
            (value_context_id, axis_id, valid_from, valid_to, decision_commit_id)
        """)

    # --- sygris_variable_bindings ----------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS sygris_variable_bindings (
            id VARCHAR PRIMARY KEY,
            sygris_variable_ref VARCHAR NOT NULL,
            target_kind VARCHAR(40) NOT NULL,
            target_ref VARCHAR NOT NULL,
            projection_flag VARCHAR(60) NOT NULL,
            binding_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES sygris_variable_bindings (id),
            superseded_by_version_id VARCHAR REFERENCES sygris_variable_bindings (id),
            binding_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("sygris_variable_bindings", "binding_hash")},
            CONSTRAINT ck_sygris_variable_bindings_target_kind
                CHECK (target_kind IN ('concept', 'canonical_concept')),
            CONSTRAINT ck_sygris_variable_bindings_projection_flag
                CHECK (projection_flag IN ('sygris_atomization_catalog', 'none')),
            CONSTRAINT uq_sygris_variable_bindings_key_ver
                UNIQUE (sygris_variable_ref, target_kind, target_ref, binding_version),
            CONSTRAINT ex_sygris_variable_bindings_no_overlap EXCLUDE USING gist (
                sygris_variable_ref WITH =, target_kind WITH =, target_ref WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_sygris_variable_bindings_prev_version
        ON sygris_variable_bindings (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_sygris_variable_bindings_resolver
        ON sygris_variable_bindings
            (sygris_variable_ref, valid_from, valid_to, decision_commit_id)
        """)

    # --- mapping_component_bindings --------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS mapping_component_bindings (
            id VARCHAR PRIMARY KEY,
            component_ref INTEGER NOT NULL,
            axis_id VARCHAR NOT NULL REFERENCES semantic_axes (id),
            term_id VARCHAR REFERENCES semantic_terms (id),
            binding_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES mapping_component_bindings (id),
            superseded_by_version_id VARCHAR REFERENCES mapping_component_bindings (id),
            binding_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("mapping_component_bindings", "binding_hash")},
            CONSTRAINT uq_mapping_component_bindings_key_ver
                UNIQUE (component_ref, axis_id, binding_version),
            CONSTRAINT ex_mapping_component_bindings_no_overlap EXCLUDE USING gist (
                component_ref WITH =, axis_id WITH =,
                tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_mapping_component_bindings_prev_version
        ON mapping_component_bindings (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_mapping_component_bindings_resolver
        ON mapping_component_bindings
            (component_ref, axis_id, valid_from, valid_to, decision_commit_id)
        """)

    # --- measurement_basis_contracts -------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS measurement_basis_contracts (
            id VARCHAR PRIMARY KEY,
            basis_key VARCHAR NOT NULL,
            basis_kind VARCHAR(40) NOT NULL,
            label VARCHAR,
            basis_version BIGINT NOT NULL,
            previous_version_id VARCHAR REFERENCES measurement_basis_contracts (id),
            superseded_by_version_id VARCHAR REFERENCES measurement_basis_contracts (id),
            basis_hash CHAR(64),
            {_BITEMPORAL},
            {_common_checks("measurement_basis_contracts", "basis_hash")},
            CONSTRAINT ck_measurement_basis_contracts_kind CHECK (
                basis_kind IN ('gross_net', 'location_market', 'measurement_method',
                'scenario_basis', 'boundary_basis', 'other')
            ),
            CONSTRAINT uq_measurement_basis_contracts_key_ver
                UNIQUE (basis_key, basis_version),
            CONSTRAINT ex_measurement_basis_contracts_no_overlap EXCLUDE USING gist (
                basis_key WITH =, tstzrange(valid_from, valid_to) WITH &&
            ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_measurement_basis_contracts_prev_version
        ON measurement_basis_contracts (previous_version_id)
        WHERE previous_version_id IS NOT NULL
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_measurement_basis_contracts_resolver
        ON measurement_basis_contracts
            (basis_key, valid_from, valid_to, decision_commit_id)
        """)

    # Reuse the generic versioned append-only trigger function created in migration 033.
    for table in _NEW_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"""
            CREATE TRIGGER trg_reject_{table}_mutation
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION sds_reject_versioned_mutation()
            """)


def downgrade() -> None:
    """Drop the VARCH-1c tables + triggers ONLY.

    The shared sds_reject_versioned_mutation / sds_reject_child_mutation functions are owned
    by migration 033 and are NOT dropped here. btree_gist is left installed (shared).
    """
    for table in _NEW_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
