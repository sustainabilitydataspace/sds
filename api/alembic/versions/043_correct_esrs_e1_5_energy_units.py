"""Correct ESRS E1-5 energy quantity metadata.

Revision ID: 043_correct_esrs_e1_5_energy_units
Revises: 042_add_localized_text
Create Date: 2026-06-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "043_correct_esrs_e1_5_energy_units"
down_revision = "042_add_localized_text"
branch_labels = None
depends_on = None

ENERGY_QUANTITY_CODES = (
    "E1-5_01",
    "E1-5_02",
    "E1-5_03",
    "E1-5_05",
    "E1-5_06",
    "E1-5_07",
    "E1-5_08",
    "E1-5_10",
    "E1-5_11",
    "E1-5_12",
    "E1-5_13",
    "E1-5_14",
    "E1-5_16",
    "E1-5_17",
)

TEXT_NARRATIVE_CODES = (
    "E1-5_01",
    "E1-5_02",
    "E1-5_03",
    "E1-5_05",
    "E1-5_06",
    "E1-5_07",
    "E1-5_08",
    "E1-5_13",
    "E1-5_14",
    "E1-5_16",
    "E1-5_17",
)

E1_5_13_DESCRIPTION = (
    "Fuel consumption from other fossil sources in own operations "
    "(energy content), as required by ESRS E1-5, paragraph 38(d) and "
    "AR 34, table row (4)."
)
E1_5_13_OLD_DESCRIPTION = (
    "Disclosure datapoint as defined in IG3 (ESRS E1-5, paragraph 38.d)."
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE indicators
            SET
              unit_name = 'MWh',
              unit_type = 'Energy',
              value_type = 'numeric',
              description = CASE
                WHEN code_esrs = 'E1-5_13' THEN :e1_5_13_description
                ELSE description
              END,
              updated_at = now()
            WHERE code_esrs IN :codes
            """
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": ENERGY_QUANTITY_CODES, "e1_5_13_description": E1_5_13_DESCRIPTION},
    )
    bind.execute(
        sa.text(
            """
            UPDATE concepts
            SET
              unit = 'MWh',
              projection_metadata = jsonb_set(
                jsonb_set(
                  jsonb_set(
                    coalesce(projection_metadata, '{}'::jsonb),
                    '{unit_name}',
                    '"MWh"'::jsonb,
                    true
                  ),
                  '{unit_type}',
                  '"Energy"'::jsonb,
                  true
                ),
                '{value_type}',
                '"numeric"'::jsonb,
                true
              ),
              updated_at = now()
            WHERE indicator_id IN (
              SELECT id FROM indicators WHERE code_esrs IN :codes
            )
            """
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": ENERGY_QUANTITY_CODES},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE indicators
            SET
              unit_name = 'Text',
              unit_type = 'Text',
              value_type = 'narrative',
              description = CASE
                WHEN code_esrs = 'E1-5_13' THEN :e1_5_13_old_description
                ELSE description
              END,
              updated_at = now()
            WHERE code_esrs IN :text_codes
            """
        ).bindparams(sa.bindparam("text_codes", expanding=True)),
        {"text_codes": TEXT_NARRATIVE_CODES, "e1_5_13_old_description": E1_5_13_OLD_DESCRIPTION},
    )
    bind.execute(
        sa.text(
            """
            UPDATE indicators
            SET unit_type = 'Mass', updated_at = now()
            WHERE code_esrs = 'E1-5_10'
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE indicators
            SET unit_type = 'Volume', updated_at = now()
            WHERE code_esrs = 'E1-5_12'
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE concepts
            SET unit = CASE
                WHEN indicator_id IN (
                  SELECT id FROM indicators WHERE code_esrs IN :text_codes
                ) THEN 'Text'
                ELSE unit
              END,
              updated_at = now()
            WHERE indicator_id IN (
              SELECT id FROM indicators
              WHERE code_esrs IN :all_codes
            )
            """
        )
        .bindparams(sa.bindparam("text_codes", expanding=True))
        .bindparams(sa.bindparam("all_codes", expanding=True)),
        {"text_codes": TEXT_NARRATIVE_CODES, "all_codes": ENERGY_QUANTITY_CODES},
    )
