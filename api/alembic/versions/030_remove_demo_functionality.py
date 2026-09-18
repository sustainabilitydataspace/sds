"""Remove legacy demo functionality state.

Revision ID: 030_remove_demo_functionality
Revises: 029_add_read_path_scale_indexes
Create Date: 2026-06-03
"""

from __future__ import annotations

from alembic import op

revision = "030_remove_demo_functionality"
down_revision = "029_add_read_path_scale_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Purge stale rows created by removed guided-demo entrypoints."""
    op.execute(
        """
        DELETE FROM api_keys
        WHERE user_id IN (
            SELECT id
            FROM user_accounts
            WHERE username IN ('demo', 'portal-demo')
               OR role IN ('demo', 'portal_demo')
        )
        """
    )
    op.execute(
        """
        DELETE FROM user_accounts
        WHERE username IN ('demo', 'portal-demo')
           OR role IN ('demo', 'portal_demo')
        """
    )

    op.execute(
        """
        DELETE FROM value_idempotency_keys
        WHERE user_id = 'user_demo'
           OR idempotency_key LIKE 'manual-golden-path%%'
           OR idempotency_key LIKE 'demo-request%%'
        """
    )

    op.execute(
        """
        DELETE FROM reported_value_pointers
        WHERE revision_id IN (
            SELECT id
            FROM value_revisions
            WHERE external_key LIKE 'manual:golden-path:%%'
               OR source_system IN (
                    'swagger-golden-paths',
                    'fastapi_demo_seed',
                    'portal_demo_seed',
                    'fastapi_demo_sample_load_v1'
               )
        )
        """
    )
    op.execute(
        """
        DELETE FROM current_value_pointers
        WHERE revision_id IN (
            SELECT id
            FROM value_revisions
            WHERE external_key LIKE 'manual:golden-path:%%'
               OR source_system IN (
                    'swagger-golden-paths',
                    'fastapi_demo_seed',
                    'portal_demo_seed',
                    'fastapi_demo_sample_load_v1'
               )
        )
        """
    )
    op.execute(
        """
        DELETE FROM value_revision_events
        WHERE revision_id IN (
            SELECT id
            FROM value_revisions
            WHERE external_key LIKE 'manual:golden-path:%%'
               OR source_system IN (
                    'swagger-golden-paths',
                    'fastapi_demo_seed',
                    'portal_demo_seed',
                    'fastapi_demo_sample_load_v1'
               )
        )
        """
    )
    op.execute(
        """
        DELETE FROM value_revisions
        WHERE external_key LIKE 'manual:golden-path:%%'
           OR source_system IN (
                'swagger-golden-paths',
                'fastapi_demo_seed',
                'portal_demo_seed',
                'fastapi_demo_sample_load_v1'
           )
        """
    )
    op.execute(
        """
        DELETE FROM value_contexts
        WHERE entity_id IN ('qa_4a1d8346', 'demo_madrid_plant')
          AND NOT EXISTS (
              SELECT 1
              FROM value_revisions
              WHERE value_revisions.context_id = value_contexts.id
          )
        """
    )
    op.execute(
        """
        DELETE FROM esg_values
        WHERE entity IN ('qa_4a1d8346', 'demo_madrid_plant')
           OR external_key LIKE 'manual:golden-path:%%'
           OR created_by IN (
                'swagger-golden-paths',
                'fastapi_demo_seed',
                'portal_demo_seed',
                'fastapi_demo_sample_load_v1'
           )
           OR value_metadata ->> 'source' IN (
                'swagger-golden-paths',
                'fastapi_demo_seed',
                'portal_demo_seed',
                'fastapi_demo_sample_load_v1'
           )
        """
    )

    op.execute(
        """
        DELETE FROM hierarchy_configurations
        WHERE id = 'portal-demo-org'
           OR name = 'Swagger Golden Paths perimeter'
           OR configuration::text LIKE '%%qa_4a1d8346%%'
           OR configuration::text LIKE '%%demo_madrid_plant%%'
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM value_contexts
                WHERE tenant_id = 'nordhaven_components_group'
            )
            AND EXISTS (
                SELECT 1
                FROM value_contexts
                WHERE tenant_id = 'acme'
                  AND entity_id LIKE 'nh_%'
            ) THEN
                RAISE EXCEPTION
                    'Mixed Nordhaven value_context tenants found; resolve acme/nordhaven_components_group contexts before applying demo removal migration';
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        UPDATE value_revisions
        SET tenant_id = 'nordhaven_components_group'
        WHERE tenant_id = 'acme'
          AND context_id IN (
              SELECT id
              FROM value_contexts
              WHERE tenant_id = 'acme'
                AND entity_id LIKE 'nh_%%'
          )
        """
    )
    op.execute(
        """
        UPDATE value_revision_events
        SET tenant_id = 'nordhaven_components_group'
        WHERE tenant_id = 'acme'
          AND context_id IN (
              SELECT id
              FROM value_contexts
              WHERE tenant_id = 'acme'
                AND entity_id LIKE 'nh_%%'
          )
        """
    )
    op.execute(
        """
        UPDATE current_value_pointers
        SET tenant_id = 'nordhaven_components_group'
        WHERE tenant_id = 'acme'
          AND context_id IN (
              SELECT id
              FROM value_contexts
              WHERE tenant_id = 'acme'
                AND entity_id LIKE 'nh_%%'
          )
        """
    )
    op.execute(
        """
        UPDATE reported_value_pointers
        SET tenant_id = 'nordhaven_components_group'
        WHERE tenant_id = 'acme'
          AND context_id IN (
              SELECT id
              FROM value_contexts
              WHERE tenant_id = 'acme'
                AND entity_id LIKE 'nh_%%'
          )
        """
    )
    op.execute(
        """
        UPDATE value_contexts
        SET tenant_id = 'nordhaven_components_group'
        WHERE tenant_id = 'acme'
          AND entity_id LIKE 'nh_%%'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM value_contexts
                WHERE tenant_id = 'acme'
                  AND entity_id LIKE 'nh_%'
            ) THEN
                RAISE EXCEPTION
                    'Nordhaven value_context tenant realignment incomplete after demo removal migration';
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        UPDATE canonical_calculation_components
        SET fx_policy_id = NULL
        WHERE fx_policy_id = 'sds-demo-monthly-average'
        """
    )
    op.execute("DELETE FROM fx_rate_periods WHERE provider = 'SDS_DEMO'")
    op.execute("DELETE FROM fx_rate_observations WHERE provider = 'SDS_DEMO'")
    op.execute("DELETE FROM fx_rate_batches WHERE provider = 'SDS_DEMO'")
    op.execute(
        """
        DELETE FROM fx_policies
        WHERE id = 'sds-demo-monthly-average'
           OR provider = 'SDS_DEMO'
        """
    )


def downgrade() -> None:
    """Removed demo state is intentionally not recreated."""
    return None
