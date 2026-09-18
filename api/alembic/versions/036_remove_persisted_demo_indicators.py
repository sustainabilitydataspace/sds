"""Remove persisted demo catalog and value rows from runtime databases.

Revision ID: 036_remove_persisted_demo_indicators
Revises: 035_add_profile_registry_tables, 035_repair_runtime_calculation_contracts
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op

revision = "036_remove_persisted_demo_indicators"
down_revision = (
    "035_add_profile_registry_tables",
    "035_repair_runtime_calculation_contracts",
)
branch_labels = None
depends_on = None


DEMO_INDICATOR_PREDICATE = """
    id LIKE 'urn:sds:reg:demo:%'
    OR identifier LIKE 'urn:sds:reg:demo:%'
    OR source_ref = 'FastAPI guided demo sample dataset'
    OR evidence_path = 'demo://fastapi/sample-load'
    OR owner = 'SDS demo'
"""


def upgrade() -> None:
    op.execute(
        f"""
        WITH demo_contexts AS (
            SELECT id
            FROM value_contexts
            WHERE sds_indicator_id LIKE 'urn:sds:reg:demo:%'
               OR indicator_identifier LIKE 'urn:sds:reg:demo:%'
        )
        DELETE FROM reported_value_pointers
        WHERE context_id IN (SELECT id FROM demo_contexts)
           OR revision_id IN (
               SELECT id FROM value_revisions
               WHERE context_id IN (SELECT id FROM demo_contexts)
           )
        """
    )
    op.execute(
        """
        WITH demo_contexts AS (
            SELECT id
            FROM value_contexts
            WHERE sds_indicator_id LIKE 'urn:sds:reg:demo:%'
               OR indicator_identifier LIKE 'urn:sds:reg:demo:%'
        )
        DELETE FROM current_value_pointers
        WHERE context_id IN (SELECT id FROM demo_contexts)
           OR revision_id IN (
               SELECT id FROM value_revisions
               WHERE context_id IN (SELECT id FROM demo_contexts)
           )
        """
    )
    op.execute(
        """
        WITH demo_contexts AS (
            SELECT id
            FROM value_contexts
            WHERE sds_indicator_id LIKE 'urn:sds:reg:demo:%'
               OR indicator_identifier LIKE 'urn:sds:reg:demo:%'
        )
        DELETE FROM value_revision_events
        WHERE context_id IN (SELECT id FROM demo_contexts)
           OR revision_id IN (
               SELECT id FROM value_revisions
               WHERE context_id IN (SELECT id FROM demo_contexts)
           )
        """
    )

    op.execute(
        "ALTER TABLE value_context_dimensions "
        "DISABLE TRIGGER trg_reject_value_context_dimensions_mutation"
    )
    op.execute(
        """
        WITH demo_contexts AS (
            SELECT id
            FROM value_contexts
            WHERE sds_indicator_id LIKE 'urn:sds:reg:demo:%'
               OR indicator_identifier LIKE 'urn:sds:reg:demo:%'
        )
        DELETE FROM value_context_dimensions
        WHERE value_context_id IN (SELECT id FROM demo_contexts)
        """
    )
    op.execute(
        "ALTER TABLE value_context_dimensions "
        "ENABLE TRIGGER trg_reject_value_context_dimensions_mutation"
    )

    op.execute(
        """
        WITH demo_contexts AS (
            SELECT id
            FROM value_contexts
            WHERE sds_indicator_id LIKE 'urn:sds:reg:demo:%'
               OR indicator_identifier LIKE 'urn:sds:reg:demo:%'
        )
        DELETE FROM value_revisions
        WHERE context_id IN (SELECT id FROM demo_contexts)
        """
    )
    op.execute(
        """
        DELETE FROM value_contexts
        WHERE sds_indicator_id LIKE 'urn:sds:reg:demo:%'
           OR indicator_identifier LIKE 'urn:sds:reg:demo:%'
        """
    )
    op.execute("DELETE FROM esg_values WHERE concept LIKE 'urn:sds:reg:demo:%'")

    op.execute(
        """
        WITH demo_contracts AS (
            SELECT id
            FROM canonical_calculation_contracts
            WHERE indicator_id LIKE 'urn:sds:reg:demo:%'
               OR indicator_identifier LIKE 'urn:sds:reg:demo:%'
        )
        DELETE FROM canonical_calculation_support_rules
        WHERE contract_id IN (SELECT id FROM demo_contracts)
        """
    )
    op.execute(
        """
        WITH demo_contracts AS (
            SELECT id
            FROM canonical_calculation_contracts
            WHERE indicator_id LIKE 'urn:sds:reg:demo:%'
               OR indicator_identifier LIKE 'urn:sds:reg:demo:%'
        )
        DELETE FROM canonical_calculation_gates
        WHERE contract_id IN (SELECT id FROM demo_contracts)
        """
    )
    op.execute(
        """
        WITH demo_contracts AS (
            SELECT id
            FROM canonical_calculation_contracts
            WHERE indicator_id LIKE 'urn:sds:reg:demo:%'
               OR indicator_identifier LIKE 'urn:sds:reg:demo:%'
        )
        DELETE FROM canonical_calculation_dimensions
        WHERE contract_id IN (SELECT id FROM demo_contracts)
        """
    )
    op.execute(
        """
        WITH demo_contracts AS (
            SELECT id
            FROM canonical_calculation_contracts
            WHERE indicator_id LIKE 'urn:sds:reg:demo:%'
               OR indicator_identifier LIKE 'urn:sds:reg:demo:%'
        )
        DELETE FROM canonical_calculation_components
        WHERE contract_id IN (SELECT id FROM demo_contracts)
           OR indicator_identifier LIKE 'urn:sds:reg:demo:%'
        """
    )
    op.execute(
        """
        DELETE FROM canonical_calculation_contracts
        WHERE indicator_id LIKE 'urn:sds:reg:demo:%'
           OR indicator_identifier LIKE 'urn:sds:reg:demo:%'
        """
    )

    op.execute(
        """
        WITH demo_canonical AS (
            SELECT id
            FROM canonical_concepts
            WHERE canonical_uri LIKE 'urn:sds:reg:demo:%'
               OR indicator_id LIKE 'urn:sds:reg:demo:%'
        )
        DELETE FROM mapping_assertion_components
        WHERE canonical_concept_id IN (SELECT id FROM demo_canonical)
        """
    )
    op.execute(
        """
        DELETE FROM canonical_concept_indicator_links
        WHERE indicator_id LIKE 'urn:sds:reg:demo:%'
           OR canonical_concept_id IN (
               SELECT id FROM canonical_concepts
               WHERE canonical_uri LIKE 'urn:sds:reg:demo:%'
                  OR indicator_id LIKE 'urn:sds:reg:demo:%'
           )
        """
    )
    op.execute(
        """
        DELETE FROM canonical_concept_equivalences
        WHERE target_uri LIKE 'urn:sds:reg:demo:%'
           OR canonical_concept_id IN (
               SELECT id FROM canonical_concepts
               WHERE canonical_uri LIKE 'urn:sds:reg:demo:%'
                  OR indicator_id LIKE 'urn:sds:reg:demo:%'
           )
        """
    )
    op.execute(
        """
        DELETE FROM canonical_concept_variables
        WHERE variable_uri LIKE 'urn:sds:reg:demo:%'
           OR canonical_concept_id IN (
               SELECT id FROM canonical_concepts
               WHERE canonical_uri LIKE 'urn:sds:reg:demo:%'
                  OR indicator_id LIKE 'urn:sds:reg:demo:%'
           )
        """
    )
    op.execute(
        """
        DELETE FROM canonical_concept_formulas
        WHERE expression LIKE '%urn:sds:reg:demo:%'
           OR canonical_concept_id IN (
               SELECT id FROM canonical_concepts
               WHERE canonical_uri LIKE 'urn:sds:reg:demo:%'
                  OR indicator_id LIKE 'urn:sds:reg:demo:%'
           )
        """
    )
    op.execute(
        """
        DELETE FROM canonical_concepts
        WHERE canonical_uri LIKE 'urn:sds:reg:demo:%'
           OR indicator_id LIKE 'urn:sds:reg:demo:%'
        """
    )

    op.execute(
        """
        DELETE FROM concept_indicator_links
        WHERE indicator_id LIKE 'urn:sds:reg:demo:%'
           OR concept_id IN (
               SELECT id FROM concepts
               WHERE uri LIKE 'urn:sds:reg:demo:%'
                  OR indicator_id LIKE 'urn:sds:reg:demo:%'
           )
        """
    )
    op.execute(
        """
        DELETE FROM concept_equivalences
        WHERE target_uri LIKE 'urn:sds:reg:demo:%'
           OR source_concept_id IN (
               SELECT id FROM concepts
               WHERE uri LIKE 'urn:sds:reg:demo:%'
                  OR indicator_id LIKE 'urn:sds:reg:demo:%'
           )
        """
    )
    op.execute(
        """
        DELETE FROM concept_variables
        WHERE variable_uri LIKE 'urn:sds:reg:demo:%'
           OR concept_id IN (
               SELECT id FROM concepts
               WHERE uri LIKE 'urn:sds:reg:demo:%'
                  OR indicator_id LIKE 'urn:sds:reg:demo:%'
           )
        """
    )
    op.execute(
        """
        DELETE FROM concept_formulas
        WHERE expression LIKE '%urn:sds:reg:demo:%'
           OR concept_id IN (
               SELECT id FROM concepts
               WHERE uri LIKE 'urn:sds:reg:demo:%'
                  OR indicator_id LIKE 'urn:sds:reg:demo:%'
           )
        """
    )
    op.execute(
        """
        DELETE FROM concepts
        WHERE uri LIKE 'urn:sds:reg:demo:%'
           OR indicator_id LIKE 'urn:sds:reg:demo:%'
        """
    )

    op.execute(f"DELETE FROM indicators WHERE {DEMO_INDICATOR_PREDICATE}")


def downgrade() -> None:
    # Do not restore retired demo catalog rows or demo observations.
    pass
