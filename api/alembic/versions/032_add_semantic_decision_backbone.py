"""Add the semantic-atomization decision-time backbone (VARCH-1a).

Creates the five tables every later VARCH table foreign-keys: steward identities, the
fenced decision-time commit sequencer, and append-only publication chains. DB-level
guardrails enforced here:

- ``decision_commit_sequence.commit_id`` is ``GENERATED ALWAYS AS IDENTITY`` (single
  DB-owned monotonic allocation; no app ``max()+1``), bound to a fence token and epoch,
  with fork prevention (partial unique on ``previous_commit_id``) and a monotonicity
  CHECK.
- ``semantic_publication_chains`` forbids valid-time overlap among published rows for the
  same subject via a ``btree_gist`` EXCLUDE constraint, and is append-only via a
  BEFORE UPDATE/DELETE trigger that permits only the published->superseded transition.
- ``decision_commit_epochs`` allows at most one active epoch; ``decision_commit_fences``
  allows one held fence per epoch (partial unique indexes).

Single-writer fencing (fence-held-at-insert), epoch-promotion serialization, and the
author!=approver invariant are runtime guarantees of the VARCH-4 sequencer, not this DDL.
Hash columns are populated at runtime via the VARCH-0 ``typed_content_hash`` profile.

Revision ID: 032_add_semantic_decision_backbone
Revises: 031_add_semantic_projection_metadata
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op

revision = "032_add_semantic_decision_backbone"
down_revision = "031_add_semantic_projection_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the VARCH-1a decision-time backbone tables and guardrails."""
    # btree_gist is required for the publication-chain no-overlap EXCLUDE constraint.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute("""
        CREATE TABLE IF NOT EXISTS semantic_stewards (
            id VARCHAR PRIMARY KEY,
            steward_id VARCHAR NOT NULL,
            display_name VARCHAR,
            role VARCHAR(20) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_semantic_stewards_role
                CHECK (role IN ('steward', 'author', 'approver'))
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_stewards_steward_id
        ON semantic_stewards (steward_id)
        """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS decision_commit_epochs (
            epoch_number BIGINT PRIMARY KEY,
            fence_token VARCHAR NOT NULL,
            promoted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            promoted_by VARCHAR,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            superseded_at TIMESTAMPTZ,
            superseded_by_epoch BIGINT,
            commit_id_floor BIGINT,
            CONSTRAINT ck_decision_commit_epochs_status
                CHECK (status IN ('active', 'superseded'))
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_decision_commit_epochs_fence_token
        ON decision_commit_epochs (fence_token)
        """)
    # At most one active epoch (constant-key partial unique index).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_decision_commit_epoch
        ON decision_commit_epochs (status) WHERE status = 'active'
        """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS decision_commit_fences (
            id VARCHAR PRIMARY KEY,
            fence_token VARCHAR NOT NULL,
            epoch_number BIGINT NOT NULL
                REFERENCES decision_commit_epochs (epoch_number),
            holder VARCHAR,
            issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            status VARCHAR(20) NOT NULL DEFAULT 'held',
            revoked_at TIMESTAMPTZ,
            CONSTRAINT ck_decision_commit_fences_status
                CHECK (status IN ('held', 'released', 'revoked')),
            CONSTRAINT uq_decision_commit_fences_fence_token UNIQUE (fence_token),
            CONSTRAINT uq_decision_commit_fences_epoch_token
                UNIQUE (epoch_number, fence_token)
        )
        """)
    # One held fence per epoch.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_one_held_fence_per_epoch
        ON decision_commit_fences (epoch_number) WHERE status = 'held'
        """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS decision_commit_sequence (
            commit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            epoch_number BIGINT NOT NULL,
            fence_token VARCHAR NOT NULL,
            committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            previous_commit_id BIGINT
                REFERENCES decision_commit_sequence (commit_id),
            commit_hash CHAR(64),
            payload_kind VARCHAR,
            CONSTRAINT ck_decision_commit_sequence_positive CHECK (commit_id > 0),
            CONSTRAINT ck_decision_commit_sequence_monotonic
                CHECK (previous_commit_id IS NULL OR previous_commit_id < commit_id),
            CONSTRAINT ck_decision_commit_sequence_hash_hex
                CHECK (commit_hash IS NULL OR commit_hash ~ '^[0-9a-f]{64}$'),
            -- Composite FK binds each commit to a (epoch, fence) PAIR that actually
            -- exists together, so a commit cannot reference a fence from one epoch
            -- while claiming a different epoch_number.
            CONSTRAINT fk_decision_commit_sequence_epoch_fence
                FOREIGN KEY (epoch_number, fence_token)
                REFERENCES decision_commit_fences (epoch_number, fence_token)
        )
        """)
    # No two commits may claim the same predecessor (fork prevention).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_decision_commit_sequence_previous
        ON decision_commit_sequence (previous_commit_id)
        WHERE previous_commit_id IS NOT NULL
        """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS semantic_publication_chains (
            id VARCHAR PRIMARY KEY,
            subject_kind VARCHAR NOT NULL,
            subject_id VARCHAR NOT NULL,
            valid_from TIMESTAMPTZ NOT NULL,
            valid_to TIMESTAMPTZ,
            decision_commit_id BIGINT NOT NULL
                REFERENCES decision_commit_sequence (commit_id),
            superseded_by_commit_id BIGINT
                REFERENCES decision_commit_sequence (commit_id),
            supersedes_publication_id VARCHAR
                REFERENCES semantic_publication_chains (id),
            publication_hash CHAR(64),
            status VARCHAR(20) NOT NULL DEFAULT 'published',
            created_by VARCHAR REFERENCES semantic_stewards (id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_semantic_publication_chains_status
                CHECK (status IN ('published', 'superseded')),
            CONSTRAINT ck_semantic_publication_chains_valid_interval
                CHECK (valid_to IS NULL OR valid_to > valid_from),
            CONSTRAINT ck_semantic_publication_chains_hash_hex
                CHECK (publication_hash IS NULL OR publication_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ex_semantic_publication_chains_no_overlap
                EXCLUDE USING gist (
                    subject_kind WITH =,
                    subject_id WITH =,
                    tstzrange(valid_from, valid_to) WITH &&
                ) WHERE (status = 'published')
        )
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_semantic_publication_chains_resolver
        ON semantic_publication_chains
            (subject_kind, subject_id, valid_from, valid_to, decision_commit_id)
        """)

    # Append-only immutability: forbid DELETE, and forbid UPDATE except the controlled
    # published->superseded transition (status and superseded_by_commit_id only).
    op.execute("""
        CREATE OR REPLACE FUNCTION sds_reject_publication_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'semantic_publication_chains is append-only: DELETE forbidden';
            END IF;

            IF OLD.status <> 'published' THEN
                RAISE EXCEPTION
                    'semantic_publication_chains row % is immutable (status=%)',
                    OLD.id, OLD.status;
            END IF;

            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.subject_kind IS DISTINCT FROM OLD.subject_kind
               OR NEW.subject_id IS DISTINCT FROM OLD.subject_id
               OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
               OR NEW.valid_to IS DISTINCT FROM OLD.valid_to
               OR NEW.decision_commit_id IS DISTINCT FROM OLD.decision_commit_id
               OR NEW.supersedes_publication_id
                    IS DISTINCT FROM OLD.supersedes_publication_id
               OR NEW.publication_hash IS DISTINCT FROM OLD.publication_hash
               OR NEW.created_by IS DISTINCT FROM OLD.created_by
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION
                    'semantic_publication_chains is append-only: only the '
                    'published->superseded transition may update a published row';
            END IF;

            IF NEW.status <> 'superseded' THEN
                RAISE EXCEPTION
                    'semantic_publication_chains published row may only move to '
                    'superseded';
            END IF;

            IF NEW.superseded_by_commit_id IS NULL THEN
                RAISE EXCEPTION
                    'semantic_publication_chains supersede must set '
                    'superseded_by_commit_id';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """)
    op.execute("""
        DROP TRIGGER IF EXISTS trg_reject_publication_mutation
        ON semantic_publication_chains
        """)
    op.execute("""
        CREATE TRIGGER trg_reject_publication_mutation
        BEFORE UPDATE OR DELETE ON semantic_publication_chains
        FOR EACH ROW EXECUTE FUNCTION sds_reject_publication_mutation()
        """)


def downgrade() -> None:
    """Drop the VARCH-1a backbone in reverse FK dependency order.

    btree_gist is left installed: it is a shared extension other schema may rely on.
    """
    op.execute("""
        DROP TRIGGER IF EXISTS trg_reject_publication_mutation
        ON semantic_publication_chains
        """)
    op.execute("DROP FUNCTION IF EXISTS sds_reject_publication_mutation()")
    op.execute("DROP TABLE IF EXISTS semantic_publication_chains")
    op.execute("DROP TABLE IF EXISTS decision_commit_sequence")
    op.execute("DROP TABLE IF EXISTS decision_commit_fences")
    op.execute("DROP TABLE IF EXISTS decision_commit_epochs")
    op.execute("DROP TABLE IF EXISTS semantic_stewards")
