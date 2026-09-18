"""Repair runtime calculation contracts away from legacy demo variables.

Revision ID: 035_repair_runtime_calculation_contracts
Revises: 034_add_value_dimensions_and_bindings
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op

revision = "035_repair_runtime_calculation_contracts"
down_revision = "034_add_value_dimensions_and_bindings"
branch_labels = None
depends_on = None


LEGACY_FORMULA_PREDICATE = """
    expression LIKE '%syg:Water_Industrial%'
    OR expression LIKE '%syg:Water_Cooling%'
    OR expression LIKE '%syg:Electricity_Scope2%'
"""

LEGACY_VARIABLE_PREDICATE = """
    variable_uri LIKE 'syg:%'
    OR variable_uri LIKE '%sygris#Water_Industrial'
    OR variable_uri LIKE '%sygris#Water_Cooling'
    OR variable_uri LIKE '%sygris#Electricity_Scope2'
"""


def _insert_formula(uri: str, expression: str) -> None:
    op.execute(
        f"""
        INSERT INTO concept_formulas (
            concept_id, expression, expression_language, version, is_active,
            created_at, updated_at
        )
        SELECT c.id, '{expression}', 'sds_formula_v1', 2, TRUE,
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM concepts c
        WHERE c.uri = '{uri}'
          AND NOT EXISTS (
              SELECT 1
              FROM concept_formulas cf
              WHERE cf.concept_id = c.id
                AND cf.expression = '{expression}'
                AND cf.is_active = TRUE
          )
        """
    )


def _insert_variable(
    concept_uri: str,
    variable_uri: str,
    variable_label: str,
) -> None:
    op.execute(
        f"""
        INSERT INTO concept_variables (
            concept_id, variable_uri, variable_label, ordering,
            aggregation_method, temporal_granularity, created_at
        )
        SELECT c.id, '{variable_uri}', '{variable_label}', 1,
               'SUM', 'annual', CURRENT_TIMESTAMP
        FROM concepts c
        WHERE c.uri = '{concept_uri}'
          AND NOT EXISTS (
              SELECT 1
              FROM concept_variables cv
              WHERE cv.concept_id = c.id
                AND cv.variable_uri = '{variable_uri}'
          )
        """
    )


def upgrade() -> None:
    op.execute(f"DELETE FROM concept_formulas WHERE {LEGACY_FORMULA_PREDICATE}")
    op.execute(f"DELETE FROM concept_variables WHERE {LEGACY_VARIABLE_PREDICATE}")

    op.execute(
        """
        DELETE FROM concept_equivalences
        WHERE source_concept_id IN (
            SELECT id FROM concepts
            WHERE uri IN (
                'https://sustainabilitydataspace.com/sygris#Electricity_Scope2',
                'https://sustainabilitydataspace.com/sygris#Water_Cooling',
                'https://sustainabilitydataspace.com/sygris#Water_Industrial'
            )
        )
        """
    )
    op.execute(
        """
        DELETE FROM concepts
        WHERE uri IN (
            'https://sustainabilitydataspace.com/sygris#Electricity_Scope2',
            'https://sustainabilitydataspace.com/sygris#Water_Cooling',
            'https://sustainabilitydataspace.com/sygris#Water_Industrial'
        )
        """
    )

    _insert_formula(
        "urn:sds:disclosure:csrd:e3-5",
        "SUM(urn:sds:reg:esrs:e3_5_01)",
    )
    _insert_variable(
        "urn:sds:disclosure:csrd:e3-5",
        "urn:sds:reg:esrs:e3_5_01",
        "Quantitative anticipated financial effects from water and marine resources-related impacts",
    )

    _insert_formula(
        "urn:sds:disclosure:gri:303-3",
        "SUM(urn:sds:reg:gri:gri_303_3_a_total_all_areas)",
    )
    _insert_variable(
        "urn:sds:disclosure:gri:303-3",
        "urn:sds:reg:gri:gri_303_3_a_total_all_areas",
        "Total water withdrawal from all areas",
    )


def downgrade() -> None:
    # Do not restore retired demo/placeholder calculation contracts.
    pass
