"""Alembic autogenerate comparison policy."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Column

# Existing migrations intentionally keep database-side defaults for non-null
# fields that also have ORM/Python defaults. Alembic renders those as default
# removals even though they are not product schema changes. Keep this list
# explicit so new default drift remains visible.
IGNORED_SERVER_DEFAULT_DIFFS = frozenset(
    {
        ("atomizer_package_imports", "audit_only_count"),
        ("atomizer_package_imports", "contract_node_count"),
        ("atomizer_package_imports", "created_at"),
        ("atomizer_package_imports", "executable_contract_count"),
        ("atomizer_package_imports", "register_row_count"),
        ("atomizer_package_imports", "semantic_only_count"),
        ("atomizer_package_imports", "updated_at"),
        ("canonical_calculation_components", "created_at"),
        ("canonical_calculation_contracts", "created_at"),
        ("canonical_calculation_contracts", "effective_from"),
        ("canonical_calculation_dimensions", "created_at"),
        ("canonical_calculation_gates", "created_at"),
        ("canonical_calculation_support_rules", "created_at"),
        ("canonical_concept_equivalences", "confidence"),
        ("canonical_concept_equivalences", "created_at"),
        ("canonical_concept_equivalences", "relationship_type"),
        ("canonical_concept_formulas", "created_at"),
        ("canonical_concept_formulas", "expression_language"),
        ("canonical_concept_formulas", "is_active"),
        ("canonical_concept_formulas", "version"),
        ("canonical_concept_indicator_links", "confidence"),
        ("canonical_concept_indicator_links", "created_at"),
        ("canonical_concept_variables", "aggregation_method"),
        ("canonical_concept_variables", "created_at"),
        ("canonical_concept_variables", "ordering"),
        ("canonical_concepts", "created_at"),
        ("canonical_concepts", "effective_from"),
        ("canonical_concepts", "revision"),
        ("canonical_mapping_package_jobs", "created_at"),
        ("canonical_mapping_package_jobs", "updated_at"),
        ("concept_equivalences", "confidence"),
        ("concept_equivalences", "relationship_type"),
        ("concept_formulas", "expression_language"),
        ("concept_formulas", "is_active"),
        ("concept_formulas", "version"),
        ("concept_indicator_links", "confidence"),
        ("concept_variables", "aggregation_method"),
        ("concept_variables", "ordering"),
        ("currencies", "created_at"),
        ("current_value_pointers", "updated_at"),
        ("fx_policies", "business_day_rule"),
        ("fx_policies", "created_at"),
        ("fx_rate_batches", "imported_at"),
        ("fx_rate_observations", "created_at"),
        ("fx_rate_periods", "created_at"),
        ("indicator_import_jobs", "created_at"),
        ("indicator_import_jobs", "updated_at"),
        ("mapping_assertion_components", "created_at"),
        ("mapping_assertion_groups", "created_at"),
        ("mapping_assertion_groups", "valid_from"),
        ("materialized_pairwise_mappings", "generated_at"),
        ("reported_value_pointers", "reported_at"),
        ("standard_datapoints", "created_at"),
        ("standard_releases", "created_at"),
        ("value_contexts", "created_at"),
        ("value_revision_events", "occurred_at"),
        ("value_revisions", "created_at"),
    }
)


def compare_server_default(
    context: Any,
    inspected_column: Column[Any] | None,
    metadata_column: Column[Any] | None,
    inspected_default: Any,
    metadata_default: Any,
    rendered_metadata_default: str | None,
) -> bool | None:
    """Return False for reviewed default-rendering noise, else use Alembic default."""
    del (
        context,
        inspected_column,
        inspected_default,
        metadata_default,
        rendered_metadata_default,
    )

    if metadata_column is None:
        return None
    table = getattr(getattr(metadata_column, "table", None), "name", None)
    column = getattr(metadata_column, "name", None)
    if (table, column) in IGNORED_SERVER_DEFAULT_DIFFS:
        return False
    return None
