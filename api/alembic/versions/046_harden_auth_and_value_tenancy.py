"""Harden authentication state and tenant-scope value idempotency.

Revision ID: 046_harden_auth_and_value_tenancy
Revises: 045_merge_energy_units_and_value_identity
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "046_harden_auth_and_value_tenancy"
down_revision = "045_merge_energy_units_and_value_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL search_path TO pg_catalog, public, pg_temp")
    op.execute(
        """
        DO $$ BEGIN
            IF pg_catalog.current_setting('transaction_isolation')
               <> 'read committed' THEN
                RAISE EXCEPTION 'source ownership migration requires READ COMMITTED';
            END IF;
        END $$
        """
    )
    # Serialize direct SQL writers before inspecting source links. PostgreSQL
    # retains these locks through the Alembic transaction and the DDL below.
    op.execute(
        "LOCK TABLE public.esg_values, public.value_revisions "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    # Historical revisions have no source FK. Both links to legacy rows and
    # missing targets for internal sources require operator disposition.
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM public.value_revisions AS vr
                WHERE EXISTS (
                    SELECT 1 FROM public.esg_values AS legacy
                    WHERE legacy.id = vr.source_record_id
                ) OR (
                    vr.source_system IN ('sds_values', 'legacy_esg_values')
                    AND NOT EXISTS (
                        SELECT 1 FROM public.esg_values AS legacy
                        WHERE legacy.id = vr.source_record_id
                    )
                )
            ) THEN
                RAISE EXCEPTION 'Legacy value/revision ownership requires '
                    'operator-reviewed disposition before migration 046';
            END IF;
        END $$
        """
    )
    op.add_column(
        "user_accounts",
        sa.Column(
            "auth_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "esg_values",
        sa.Column("tenant_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "esg_values",
        sa.Column("ownership_state", sa.String(length=20), nullable=True),
    )
    # A revision's source_record_id is not a foreign key or independent proof
    # that its tenant owns the legacy row. Do not infer ownership from it.
    op.execute(
        """
        UPDATE esg_values
        SET tenant_id = NULL,
            ownership_state = 'quarantined'
        """
    )
    op.drop_index("ix_esg_values_external_key_unique", table_name="esg_values")
    op.create_index(
        "ix_esg_values_tenant_external_key_unique",
        "esg_values",
        ["tenant_id", "external_key"],
        unique=True,
        postgresql_where=sa.text(
            "external_key IS NOT NULL AND ownership_state = 'resolved'"
        ),
    )
    op.add_column(
        "value_idempotency_keys",
        sa.Column("tenant_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "value_idempotency_keys",
        sa.Column("ownership_state", sa.String(length=20), nullable=True),
    )
    op.execute(
        "DELETE FROM value_idempotency_keys WHERE expires_at <= CURRENT_TIMESTAMP"
    )
    op.execute(
        """
        UPDATE value_idempotency_keys
        SET tenant_id = NULL,
            ownership_state = 'quarantined'
        """
    )
    op.drop_index(
        "ix_value_idempotency_keys_user_scope_key",
        table_name="value_idempotency_keys",
    )
    op.create_index(
        "ix_value_idempotency_keys_tenant_user_scope_key",
        "value_idempotency_keys",
        ["tenant_id", "user_id", "scope", "idempotency_key"],
        unique=True,
    )
    op.alter_column("esg_values", "ownership_state", nullable=False)
    op.alter_column("value_idempotency_keys", "ownership_state", nullable=False)
    op.create_check_constraint(
        "ck_esg_values_ownership",
        "esg_values",
        """
        (ownership_state = 'resolved' AND tenant_id IS NOT NULL
         AND btrim(tenant_id) <> '')
        OR
        (ownership_state = 'quarantined' AND tenant_id IS NULL)
        """,
    )
    op.create_check_constraint(
        "ck_value_idempotency_keys_ownership",
        "value_idempotency_keys",
        """
        (ownership_state = 'resolved' AND tenant_id IS NOT NULL
         AND btrim(tenant_id) <> '')
        OR
        (ownership_state = 'quarantined' AND tenant_id IS NULL)
        """,
    )
    # Internal sources must resolve to a same-tenant value; external sources
    # must never collide with a local value ID. Both insertion orders take the
    # same transaction advisory lock before any absence/ownership check.
    op.execute(
        """
        CREATE FUNCTION public.sds_check_revision_value_source() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
            IF pg_catalog.current_setting('transaction_isolation')
               <> 'read committed' THEN
                RAISE EXCEPTION 'source ownership writes require READ COMMITTED';
            END IF;
            IF NEW.source_system IN ('sds_values', 'legacy_esg_values')
               AND NEW.source_record_id IS NULL THEN
                RAISE EXCEPTION 'internal source requires matching local value';
            END IF;
            IF NEW.source_record_id IS NULL THEN
                RETURN NEW;
            END IF;
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(NEW.source_record_id, 0));
            IF NEW.source_system IN ('sds_values', 'legacy_esg_values') THEN
                IF NOT EXISTS (
                    SELECT 1 FROM public.esg_values AS v
                    WHERE v.id = NEW.source_record_id
                      AND v.ownership_state = 'resolved'
                      AND v.tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'internal source requires matching local value';
                END IF;
            ELSIF EXISTS (
                SELECT 1 FROM public.esg_values AS v
                WHERE v.id = NEW.source_record_id
            ) THEN
                RAISE EXCEPTION 'external source collides with local value';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.sds_check_value_source_revisions() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
            IF pg_catalog.current_setting('transaction_isolation')
               <> 'read committed' THEN
                RAISE EXCEPTION 'source ownership writes require READ COMMITTED';
            END IF;
            IF TG_OP = 'DELETE' THEN
                PERFORM pg_catalog.pg_advisory_xact_lock(
                    pg_catalog.hashtextextended(OLD.id, 0));
                IF EXISTS (
                    SELECT 1 FROM public.value_revisions AS vr
                    WHERE vr.source_record_id = OLD.id
                ) THEN
                    RAISE EXCEPTION 'cannot delete referenced value source';
                END IF;
                RETURN OLD;
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.id IS DISTINCT FROM OLD.id THEN
                RAISE EXCEPTION 'value source identity is immutable';
            END IF;
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(NEW.id, 0));
            IF EXISTS (
                SELECT 1 FROM public.value_revisions AS vr
                WHERE vr.source_record_id = NEW.id
                  AND (vr.source_system IS NULL OR vr.source_system NOT IN
                       ('sds_values', 'legacy_esg_values'))
            ) THEN
                RAISE EXCEPTION 'external source collides with local value';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.value_revisions AS vr
                WHERE vr.source_record_id = NEW.id
                  AND (NEW.ownership_state <> 'resolved'
                       OR vr.tenant_id IS DISTINCT FROM NEW.tenant_id)
            ) THEN
                RAISE EXCEPTION 'quarantined or foreign value source';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER sds_revision_source_owner
        BEFORE INSERT OR UPDATE OF source_record_id, source_system, tenant_id
        ON public.value_revisions FOR EACH ROW
        EXECUTE FUNCTION public.sds_check_revision_value_source()
        """
    )
    op.execute(
        """
        CREATE TRIGGER sds_value_source_owner
        BEFORE INSERT OR DELETE OR UPDATE OF id, tenant_id, ownership_state
        ON public.esg_values FOR EACH ROW
        EXECUTE FUNCTION public.sds_check_value_source_revisions()
        """
    )
    # TRUNCATE does not fire row-level DELETE guards. Serialize with revision
    # writers and refuse to erase the source table while links survive.
    op.execute(
        """
        CREATE FUNCTION public.sds_check_value_source_truncate() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
            IF pg_catalog.current_setting('transaction_isolation')
               <> 'read committed' THEN
                RAISE EXCEPTION 'source ownership writes require READ COMMITTED';
            END IF;
            LOCK TABLE public.value_revisions IN SHARE ROW EXCLUSIVE MODE;
            IF EXISTS (
                SELECT 1 FROM public.value_revisions AS vr
                WHERE vr.source_record_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'cannot truncate referenced value source';
            END IF;
            RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER sds_value_source_truncate
        BEFORE TRUNCATE ON public.esg_values FOR EACH STATEMENT
        EXECUTE FUNCTION public.sds_check_value_source_truncate()
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "irreversible tenant security migration; restore a pre-upgrade database "
        "snapshot rather than discarding tenant identity or auth epochs"
    )
