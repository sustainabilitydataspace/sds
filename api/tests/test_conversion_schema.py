import importlib.util
import inspect
from pathlib import Path

import sqlalchemy as sa

from src.database import models

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "020_create_conversion_engine_tables.py"
)
_MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "migration_020_create_conversion_engine_tables", _MIGRATION_PATH
)
migration = importlib.util.module_from_spec(_MIGRATION_SPEC)
assert _MIGRATION_SPEC.loader is not None
_MIGRATION_SPEC.loader.exec_module(migration)


FX_OBSERVATION_LOOKUP_KEY = (
    "provider",
    "rate_type",
    "base_currency",
    "quote_currency",
    "rate_date",
)

FX_PERIOD_LOOKUP_KEY = (
    "provider",
    "rate_type",
    "base_currency",
    "quote_currency",
    "period_type",
    "period_start",
    "period_end",
)


def _unique_constraint_columns(table, name):
    for constraint in table.constraints:
        if isinstance(constraint, sa.UniqueConstraint) and constraint.name == name:
            return tuple(column.name for column in constraint.columns)
    raise AssertionError(f"{table.name} does not expose unique constraint {name}")


def _index_columns(table, name):
    for index in table.indexes:
        if index.name == name:
            return tuple(column.name for column in index.columns)
    raise AssertionError(f"{table.name} does not expose index {name}")


def _default_arg(column_name, table):
    column = table.c[column_name]
    if column.default is not None:
        return column.default.arg
    if column.server_default is not None:
        return str(column.server_default.arg)
    raise AssertionError(f"{table.name}.{column_name} has no default")


def test_unit_tables_expose_dimension_and_source_fields():
    unit_cols = set(models.Unit.__table__.columns.keys())
    rule_cols = set(models.ConversionRule.__table__.columns.keys())

    assert {
        "dimension_vector",
        "source_system",
        "source_version",
        "valid_from",
        "valid_to",
    }.issubset(unit_cols)
    assert {
        "rule_hash",
        "priority",
        "valid_from",
        "valid_to",
        "source_system",
        "source_version",
    }.issubset(rule_cols)


def test_fx_tables_exist_with_auditable_keys():
    assert hasattr(models, "Currency")
    assert hasattr(models, "FXRateBatch")
    assert hasattr(models, "FXRateObservation")
    assert hasattr(models, "FXRatePeriod")
    assert hasattr(models, "FXPolicy")

    rate_cols = set(models.FXRateObservation.__table__.columns.keys())
    assert {
        "id",
        "batch_id",
        "base_currency",
        "quote_currency",
        "rate_date",
        "rate_value",
        "provider",
        "rate_type",
        "source_hash",
    }.issubset(rate_cols)


def test_fx_observations_expose_unique_lookup_key_and_composite_index():
    table = models.FXRateObservation.__table__

    assert (
        _unique_constraint_columns(table, "uq_fx_rate_observation_key")
        == FX_OBSERVATION_LOOKUP_KEY
    )
    assert (
        _index_columns(table, "ix_fx_rate_observations_lookup")
        == FX_OBSERVATION_LOOKUP_KEY
    )


def test_fx_periods_expose_unique_lookup_key_and_composite_index():
    table = models.FXRatePeriod.__table__

    assert _unique_constraint_columns(table, "uq_fx_rate_period_key") == (
        FX_PERIOD_LOOKUP_KEY
    )
    assert _index_columns(table, "ix_fx_rate_periods_lookup") == FX_PERIOD_LOOKUP_KEY


def test_fx_policy_defaults_fail_closed_and_models_active_flag():
    table = models.FXPolicy.__table__

    assert table.c.selection_mode.nullable is False
    assert table.c.business_day_rule.nullable is False
    assert table.c.triangulation_allowed.nullable is False
    assert table.c.fallback_behavior.nullable is False
    assert table.c.rounding_scale.nullable is False
    assert table.c.rounding_mode.nullable is False
    assert table.c.policy_metadata.nullable is True
    assert table.c.is_active.nullable is False

    assert _default_arg("fallback_behavior", table) == "fail_closed"
    assert _default_arg("business_day_rule", table) == "previous_available"
    assert _default_arg("triangulation_allowed", table) is False
    assert _default_arg("rounding_scale", table) == 6
    assert _default_arg("rounding_mode", table) == "ROUND_HALF_UP"
    assert _default_arg("is_active", table) is True


def test_esg_values_expose_row_level_conversion_provenance_columns():
    table = models.ESGValue.__table__
    cols = set(table.columns.keys())

    assert {
        "original_value",
        "currency",
        "original_currency",
        "currency_conversion_applied",
        "conversion_trace",
        "value_date",
        "period_start",
        "period_end",
    }.issubset(cols)
    assert str(table.c.currency.type) == "VARCHAR(3)"
    assert str(table.c.original_currency.type) == "VARCHAR(3)"
    assert table.c.original_value.type.precision == 30
    assert table.c.original_value.type.scale == 10
    assert table.c.currency_conversion_applied.nullable is False
    assert table.c.currency_conversion_applied.default.arg is False


def test_conversion_engine_migration_is_additive_and_drops_tables_first():
    assert migration.down_revision == "019_widen_calculation_contract_version"

    source = inspect.getsource(migration.downgrade)
    first_column_drop = source.index('op.drop_column("esg_values"')
    for table_name in (
        "fx_policies",
        "fx_rate_periods",
        "fx_rate_observations",
        "fx_rate_batches",
        "currencies",
    ):
        assert source.index(f'op.drop_table("{table_name}")') < first_column_drop
