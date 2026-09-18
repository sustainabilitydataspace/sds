"""Add profile / manifest / schema registry tables (VARCH-1d).

Fourth of six VARCH-1 migration groups. Persists the VARCH-0 in-code profiles as append-only,
content-addressable DB registries (candidate-v14: "historical profiles and contract schemas
are append-only and forever executable"):

- canonical_hash_profiles (+ _fixtures)
- computation_profiles (+ _fixtures)
- semantic_replay_manifests (+ _fixtures)
- contract_schema_versions

Unlike the bitemporal subjects in 032-034 these are immutable version registries (NO
valid/decision columns) referenced by trace-pinned (id, version, hash) tuples. version is
VARCHAR (matches the VARCH-0 in-code 'v1' style); hashes are NOT NULL + UNIQUE. A row may
only transition active -> deprecated via the new sds_reject_profile_mutation trigger;
fixtures are fully immutable (reuse the shared sds_reject_child_mutation from migration 033).
Seed data (the 2 implemented + 7 ratified VARCH-0 profiles) is deferred to VARCH-3.

Revision ID: 035_add_profile_registry_tables
Revises: 034_add_value_dimensions_and_bindings
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op

revision = "035_add_profile_registry_tables"
down_revision = "034_add_value_dimensions_and_bindings"
branch_labels = None
depends_on = None

_AUDIT = """
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            deprecated_at TIMESTAMPTZ,
            created_by VARCHAR REFERENCES semantic_stewards (id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
"""

_MAIN_TABLES = (
    "canonical_hash_profiles",
    "computation_profiles",
    "semantic_replay_manifests",
    "contract_schema_versions",
)
_FIXTURE_TABLES = (
    "canonical_hash_profile_fixtures",
    "computation_profile_fixtures",
    "semantic_replay_manifest_fixtures",
)


def _profile_table(name: str, hash_col: str, extra_cols: str, extra_constraints: str):
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {name} (
            id VARCHAR PRIMARY KEY,
            {extra_cols}
            {hash_col} CHAR(64) NOT NULL,
            {_AUDIT},
            CONSTRAINT ck_{name}_status CHECK (status IN ('active', 'deprecated')),
            CONSTRAINT ck_{name}_hash_hex CHECK ({hash_col} ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT uq_{name}_hash UNIQUE ({hash_col})
            {extra_constraints}
        )
        """)


def _fixture_table(name: str, parent: str, parent_fk_col: str):
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {name} (
            id VARCHAR PRIMARY KEY,
            {parent_fk_col} VARCHAR NOT NULL REFERENCES {parent} (id),
            fixture_key VARCHAR NOT NULL,
            fixture_payload JSONB NOT NULL,
            fixture_hash CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_{name}_hash_hex CHECK (fixture_hash ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT uq_{name}_key UNIQUE ({parent_fk_col}, fixture_key),
            CONSTRAINT uq_{name}_hash UNIQUE (fixture_hash)
        )
        """)


def upgrade() -> None:
    """Create the VARCH-1d registry tables + the profile append-only trigger."""
    _profile_table(
        "canonical_hash_profiles",
        "profile_hash",
        """
            profile_id VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            kind VARCHAR(20) NOT NULL,
            descriptor JSONB NOT NULL,
            fixture_corpus_sha256 CHAR(64),
        """,
        """,
            CONSTRAINT ck_canonical_hash_profiles_kind
                CHECK (kind IN ('implemented', 'ratified')),
            CONSTRAINT ck_canonical_hash_profiles_corpus_hex CHECK (
                fixture_corpus_sha256 IS NULL
                OR fixture_corpus_sha256 ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT uq_canonical_hash_profiles_id_ver UNIQUE (profile_id, version)
        """,
    )
    _profile_table(
        "computation_profiles",
        "profile_hash",
        """
            profile_id VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            kind VARCHAR(20) NOT NULL,
            descriptor JSONB NOT NULL,
            fixture_corpus_sha256 CHAR(64),
        """,
        """,
            CONSTRAINT ck_computation_profiles_kind
                CHECK (kind IN ('implemented', 'ratified')),
            CONSTRAINT ck_computation_profiles_corpus_hex CHECK (
                fixture_corpus_sha256 IS NULL
                OR fixture_corpus_sha256 ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT uq_computation_profiles_id_ver UNIQUE (profile_id, version)
        """,
    )
    _profile_table(
        "semantic_replay_manifests",
        "manifest_hash",
        """
            manifest_id VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            profile_tuple JSONB NOT NULL,
            evaluation_order JSONB NOT NULL,
            transition_policy VARCHAR,
            descriptor JSONB NOT NULL,
            fixture_corpus_sha256 CHAR(64),
        """,
        """,
            CONSTRAINT ck_semantic_replay_manifests_corpus_hex CHECK (
                fixture_corpus_sha256 IS NULL
                OR fixture_corpus_sha256 ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT uq_semantic_replay_manifests_id_ver UNIQUE (manifest_id, version)
        """,
    )
    _profile_table(
        "contract_schema_versions",
        "schema_hash",
        """
            schema_id VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            schema JSONB NOT NULL,
        """,
        """,
            CONSTRAINT uq_contract_schema_versions_id_ver UNIQUE (schema_id, version)
        """,
    )

    _fixture_table(
        "canonical_hash_profile_fixtures", "canonical_hash_profiles", "profile_pk"
    )
    _fixture_table("computation_profile_fixtures", "computation_profiles", "profile_pk")
    _fixture_table(
        "semantic_replay_manifest_fixtures", "semantic_replay_manifests", "manifest_pk"
    )

    # New generic append-only trigger for registry rows: forbid DELETE; allow only the
    # active -> deprecated status transition (setting deprecated_at); all else immutable.
    op.execute("""
        CREATE OR REPLACE FUNCTION sds_reject_profile_mutation()
        RETURNS trigger AS $$
        DECLARE old_j jsonb; new_j jsonb;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION '% is append-only: DELETE forbidden', TG_TABLE_NAME;
            END IF;
            IF OLD.status <> 'active' THEN
                RAISE EXCEPTION '% row is immutable (status=%)',
                    TG_TABLE_NAME, OLD.status;
            END IF;
            IF NEW.status <> 'deprecated' THEN
                RAISE EXCEPTION '% active row may only move to deprecated',
                    TG_TABLE_NAME;
            END IF;
            IF NEW.deprecated_at IS NULL THEN
                RAISE EXCEPTION '% deprecation must set deprecated_at', TG_TABLE_NAME;
            END IF;
            old_j := to_jsonb(OLD) - 'status' - 'deprecated_at';
            new_j := to_jsonb(NEW) - 'status' - 'deprecated_at';
            IF old_j <> new_j THEN
                RAISE EXCEPTION '% is append-only: only the active->deprecated '
                    'transition may update a row', TG_TABLE_NAME;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """)
    for table in _MAIN_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"""
            CREATE TRIGGER trg_reject_{table}_mutation
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION sds_reject_profile_mutation()
            """)
    # Fixtures are fully immutable: reuse the shared child guard from migration 033.
    for table in _FIXTURE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"""
            CREATE TRIGGER trg_reject_{table}_mutation
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION sds_reject_child_mutation()
            """)


def downgrade() -> None:
    """Drop VARCH-1d tables + triggers and the profile trigger function.

    sds_reject_child_mutation is owned by migration 033 and is NOT dropped here.
    """
    for table in _FIXTURE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
    for table in _MAIN_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_reject_{table}_mutation ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS sds_reject_profile_mutation()")
