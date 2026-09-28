"""Guardrails for Alembic metadata drift reconciliation."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import CheckConstraint, Index, UniqueConstraint

from src.database import models as _models  # noqa: F401 - register metadata
from src.database.base import Base
from tests.legacy_guided_tokens import LEGACY_REVISION

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_028 = (
    REPO_ROOT
    / "api"
    / "alembic"
    / "versions"
    / "028_reconcile_alembic_metadata_drift.py"
)
MIGRATION_029 = (
    REPO_ROOT / "api" / "alembic" / "versions" / "029_add_read_path_scale_indexes.py"
)
MIGRATION_031 = (
    REPO_ROOT
    / "api"
    / "alembic"
    / "versions"
    / "031_add_semantic_projection_metadata.py"
)
MIGRATION_043 = (
    REPO_ROOT
    / "api"
    / "alembic"
    / "versions"
    / "043_add_canonical_value_context_identity.py"
)
ALEMBIC_ENV = REPO_ROOT / "api" / "alembic" / "env.py"


def _index(table_name: str, index_name: str) -> Index | None:
    table = Base.metadata.tables[table_name]
    return next((idx for idx in table.indexes if idx.name == index_name), None)


def _unique_constraint_columns(table_name: str, columns: tuple[str, ...]) -> bool:
    table = Base.metadata.tables[table_name]
    return any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns) == columns
        for constraint in table.constraints
    )


def _index_columns(table_name: str, index_name: str) -> tuple[str, ...]:
    idx = _index(table_name, index_name)
    assert idx is not None, f"Missing index {index_name} on {table_name}"
    return tuple(column.name for column in idx.columns)


def _check_constraint_names(table_name: str) -> set[str]:
    table = Base.metadata.tables[table_name]
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_unique_identity_columns_match_migrated_constraint_plus_index_shape() -> None:
    """Models should match the current migrated DB, not autogen unique-index churn."""
    cases = [
        (
            "atomizer_package_imports",
            ("package_hash",),
            "ix_atomizer_package_imports_package_hash",
        ),
        ("concepts", ("uri",), "ix_concepts_uri"),
        ("indicators", ("identifier",), "ix_indicators_identifier"),
        ("revoked_tokens", ("token_hash",), "ix_revoked_tokens_token_hash"),
        ("user_accounts", ("username",), "ix_user_accounts_username"),
    ]

    for table_name, columns, index_name in cases:
        assert _unique_constraint_columns(table_name, columns)
        idx = _index(table_name, index_name)
        assert idx is not None
        assert idx.unique is False


def test_value_revision_source_observation_index_matches_migration_contract() -> None:
    assert _index_columns(
        "value_revisions", "ix_value_revisions_source_observation"
    ) == ("tenant_id", "source_system", "external_key", "source_record_id")


def test_value_context_canonical_identity_metadata_matches_migration_contract() -> None:
    table = Base.metadata.tables["value_contexts"]
    for column_name in (
        "canonical_concept_id",
        "canonical_uri",
        "source_observation_type",
    ):
        assert column_name in table.c

    assert table.c.canonical_concept_id.nullable is True
    assert table.c.canonical_uri.nullable is True
    assert table.c.source_observation_type.nullable is False
    assert "ck_value_contexts_source_observation_type" in _check_constraint_names(
        "value_contexts"
    )
    assert _index_columns("value_contexts", "ix_value_contexts_canonical_uri") == (
        "canonical_uri",
    )
    assert _index_columns(
        "value_contexts", "ix_value_contexts_canonical_concept_id"
    ) == ("canonical_concept_id",)
    assert _index_columns(
        "value_contexts", "ix_value_contexts_tenant_canonical_period"
    ) == ("tenant_id", "canonical_uri", "reporting_period_id")
    assert _index_columns(
        "value_contexts", "ix_value_contexts_canonical_concept_period"
    ) == ("canonical_concept_id", "reporting_period_id")


def test_reconciliation_indexes_are_declared_in_metadata() -> None:
    expected = {
        ("esg_values", "ix_esg_values_read_cursor"): (
            "concept",
            "entity",
            "period",
            "created_at",
            "id",
        ),
        ("esg_values", "ix_esg_values_change_cursor"): ("updated_at", "id"),
        ("canonical_concepts", "ix_canonical_concepts_indicator_state"): (
            "indicator_id",
            "concept_state",
        ),
        (
            "canonical_concept_indicator_links",
            "ix_canonical_concept_indicator_links_concept_type",
        ): ("canonical_concept_id", "link_type"),
        ("fx_rate_observations", "ix_fx_rate_observations_source_hash"): (
            "source_hash",
        ),
        ("fx_rate_periods", "ix_fx_rate_periods_quote_currency"): ("quote_currency",),
        ("fx_rate_periods", "ix_fx_rate_periods_source_hash"): ("source_hash",),
    }

    for (table_name, index_name), columns in expected.items():
        assert _index_columns(table_name, index_name) == columns


def test_reconciliation_migration_declares_idempotent_index_repair() -> None:
    assert MIGRATION_028.exists()
    text = MIGRATION_028.read_text(encoding="utf-8")

    for snippet in [
        'revision = "028_reconcile_alembic_metadata_drift"',
        'down_revision = "027_widen_unit_symbol_columns"',
        "CREATE INDEX IF NOT EXISTS ix_canonical_concepts_indicator_state",
        "CREATE INDEX IF NOT EXISTS ix_canonical_concept_indicator_links_concept_type",
        "CREATE INDEX IF NOT EXISTS ix_fx_rate_observations_source_hash",
        "CREATE INDEX IF NOT EXISTS ix_fx_rate_periods_quote_currency",
        "CREATE INDEX IF NOT EXISTS ix_fx_rate_periods_source_hash",
    ]:
        assert snippet in text


def test_read_path_scale_migration_declares_additive_indexes() -> None:
    assert MIGRATION_029.exists()
    text = MIGRATION_029.read_text(encoding="utf-8")

    for snippet in [
        'revision = "029_add_read_path_scale_indexes"',
        'down_revision = "028_reconcile_alembic_metadata_drift"',
        "CREATE INDEX IF NOT EXISTS ix_esg_values_read_cursor",
        "CREATE INDEX IF NOT EXISTS ix_esg_values_change_cursor",
        "CREATE INDEX IF NOT EXISTS ix_materialized_pairwise_mappings_metadata_esg_dimension",
        "CREATE INDEX IF NOT EXISTS ix_materialized_pairwise_mappings_metadata_dimension",
        "WHERE is_current IS TRUE",
    ]:
        assert snippet in text


def test_semantic_projection_metadata_columns_are_declared() -> None:
    for table_name in ("concepts", "canonical_concepts"):
        table = Base.metadata.tables[table_name]
        for column_name in (
            "projection_source",
            "projection_hash",
            "projection_version",
            "projection_metadata",
            "projected_at",
        ):
            assert column_name in table.c

    assert _index_columns("concepts", "ix_concepts_projection_source") == (
        "projection_source",
    )
    assert _index_columns(
        "canonical_concepts", "ix_canonical_concepts_projection_source"
    ) == ("projection_source",)


def test_semantic_projection_metadata_migration_is_additive() -> None:
    assert MIGRATION_031.exists()
    text = MIGRATION_031.read_text(encoding="utf-8")

    for snippet in [
        'revision = "031_add_semantic_projection_metadata"',
        f'down_revision = "{LEGACY_REVISION}"',
        "ADD COLUMN IF NOT EXISTS projection_source",
        "ADD COLUMN IF NOT EXISTS projection_hash",
        "ADD COLUMN IF NOT EXISTS projection_version",
        "ADD COLUMN IF NOT EXISTS projection_metadata",
        "ADD COLUMN IF NOT EXISTS projected_at",
        "CREATE INDEX IF NOT EXISTS ix_concepts_projection_source",
        "CREATE INDEX IF NOT EXISTS ix_canonical_concepts_projection_source",
    ]:
        assert snippet in text


def test_canonical_value_context_identity_migration_is_additive() -> None:
    assert MIGRATION_043.exists()
    text = MIGRATION_043.read_text(encoding="utf-8")

    for snippet in [
        'revision = "043_add_canonical_value_context_identity"',
        'down_revision = "042_add_localized_text"',
        "canonical_concept_id",
        "canonical_uri",
        "source_observation_type",
        "ck_value_contexts_source_observation_type",
        "fk_value_contexts_canonical_concept_id_canonical_concepts",
        "ix_value_contexts_tenant_canonical_period",
        "ix_value_contexts_canonical_concept_period",
    ]:
        assert snippet in text


def test_alembic_server_default_noise_policy_is_explicit() -> None:
    from src.database.alembic_compare import (  # noqa: PLC0415
        IGNORED_SERVER_DEFAULT_DIFFS,
        compare_server_default,
    )

    assert (
        "atomizer_package_imports",
        "register_row_count",
    ) in IGNORED_SERVER_DEFAULT_DIFFS
    assert ("value_revisions", "created_at") in IGNORED_SERVER_DEFAULT_DIFFS

    assert (
        compare_server_default(
            None,
            None,
            Base.metadata.tables["atomizer_package_imports"].c.register_row_count,
            "0",
            None,
            None,
        )
        is False
    )


def test_alembic_env_uses_custom_server_default_comparator() -> None:
    text = ALEMBIC_ENV.read_text(encoding="utf-8")
    assert "from src.database.alembic_compare import compare_server_default" in text
    assert "compare_server_default=compare_server_default" in text
