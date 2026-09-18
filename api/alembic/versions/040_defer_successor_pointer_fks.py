"""Make successor-pointer self-FKs deferrable (VARCH-4a).

An atomic decision-time restatement over an OVERLAPPING valid window must run, in one
transaction, ``UPDATE predecessor -> superseded, superseded_by_version_id = successor``
BEFORE the successor row exists, then ``INSERT successor (published)``. Two DB invariants
collide there:

* the append-only trigger (``sds_reject_versioned_mutation``) forces the supersede UPDATE
  to set ``superseded_by_version_id`` in the SAME statement as ``published -> superseded``;
* the published-only no-overlap EXCLUDE forbids inserting the successor as ``published``
  while the predecessor is still ``published`` over the same/overlapping valid window.

With the ``superseded_by_version_id`` self-FK IMMEDIATE, update-first fails the FK (the
successor row does not exist yet) and insert-first fails the EXCLUDE. Making every such
self-FK ``DEFERRABLE INITIALLY DEFERRED`` resolves the deadlock: the successor pointer is
checked at COMMIT, once both rows exist and the predecessor is no longer published.

The set of affected tables is discovered DYNAMICALLY (every column named
``superseded_by_version_id`` backed by a foreign key) so all 27 VARCH-1 versioned tables —
and any future one using the generic trigger — are covered without an enumerated list.

Out of scope by design:
* ``previous_version_id`` is untouched — the predecessor always exists already.
* ``semantic_publication_chains.superseded_by_commit_id`` is untouched — it references
  ``decision_commit_sequence`` (a commit allocated first), so no circular dependency exists.

This migration changes only constraint deferrability; it touches no data and is reversible.
"""

from __future__ import annotations

from alembic import op

revision = "040_defer_successor_pointer_fks"
down_revision = "039_seed_varch0_profiles_and_waste_plastic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT rel.relname AS tbl, con.conname AS fk
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_attribute att
                    ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
                WHERE con.contype = 'f'
                  AND att.attname = 'superseded_by_version_id'
                  AND con.condeferrable = false
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I ALTER CONSTRAINT %I DEFERRABLE INITIALLY DEFERRED',
                    r.tbl, r.fk
                );
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT rel.relname AS tbl, con.conname AS fk
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_attribute att
                    ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
                WHERE con.contype = 'f'
                  AND att.attname = 'superseded_by_version_id'
                  AND con.condeferrable = true
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I ALTER CONSTRAINT %I NOT DEFERRABLE',
                    r.tbl, r.fk
                );
            END LOOP;
        END $$;
        """
    )
